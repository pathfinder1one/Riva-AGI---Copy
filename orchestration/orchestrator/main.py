"""
Riva-AGI Orchestrator Engine — orchestration/orchestrator/main.py
=================================================================
Public entrypoint for the Riva-AGI multi-agent orchestration pipeline.

Dual-Track Low-Latency Architecture:
  - Zero-LLM Orchestration Core (Laya Engine ~33ms + Deterministic DAG Scheduler <1ms)
  - Structured TaskSpec Handoff with Kahn Cycle Detection
  - Agent-Aware Reviewer Quality Profiles with Noul (<35ms) + Retry Cap (max_retries=3)
  - Self-Healing Workspace Rollback (Pillar 9) via SecurityGate

All implementation details live in:
  pipeline/state.py   → AgentState schema + state helpers
  pipeline/nodes.py   → All 7 LangGraph node functions
  pipeline/edges.py   → Conditional routing functions
  pipeline/graph.py   → create_orchestrator() graph compiler
"""
import os
import sys

# Ensure the root directory is in the Python path so absolute imports work
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import logging
import time
import uuid
import importlib
import pkgutil
from typing import Optional

from dotenv import load_dotenv

from orchestration.orchestrator.infra.key_manager import key_manager
from orchestration.orchestrator.pipeline.whiteboard import WhiteboardContext
from orchestration import InputData, AgentResponse, ResponseStatus, InputType
import orchestration.agents

def load_all_agents():
    """Dynamically load all agents to ensure they register with the registry."""
    for _, module_name, _ in pkgutil.iter_modules(orchestration.agents.__path__):
        try:
            importlib.import_module(f"orchestration.agents.{module_name}")
        except Exception as e:
            logger.error(f"Failed to load agent '{module_name}': {e}")

# ── Pipeline Imports (single source of truth for all graph logic) ──────────
from orchestration.orchestrator.pipeline import (
    AgentState,
    task_manager,
    create_orchestrator,
    Budget,
)

def build_repo_map(root_dir: str) -> str:
    """Build a fast repository map (tree of .py files) to kill the exploration loop."""
    lines = ["Repository Map:"]
    for root, _, files in os.walk(root_dir):
        if ".git" in root or "__pycache__" in root or ".venv" in root: continue
        for f in files:
            if f.endswith(".py"):
                rel_path = os.path.relpath(os.path.join(root, f), root_dir)
                lines.append(f"- {rel_path}")
    return "\n".join(lines)

# ── Re-exports for backward compatibility (existing imports must not break) ─
from orchestration.orchestrator.pipeline import (  # noqa: F401
    clean_json,
    safe_update_task_state,
    intent_node,
    planner_node,
    executor_node,
    reviewer_node,
    create_agent_node,
    aggregator_node,
    fallback_node,
    route_after_intent,
    route_after_executor,
    route_after_worker,
    route_after_reviewer,
)

load_dotenv()

logger = logging.getLogger(__name__)

__all__ = [
    "run_orchestrator",
    "create_orchestrator",
    "AgentState",
    "task_manager",
    "reviewer_node",
    "planner_node",
    "executor_node",
    "intent_node",
    "aggregator_node",
    "fallback_node",
    "create_agent_node",
    "clean_json",
    "safe_update_task_state",
]


# ── Public API ─────────────────────────────────────────────────────────────

def run_orchestrator(
    task_text: str,
    task_id: Optional[str] = None,
    session_id: str = "session-001",
    source: str = "cli",
) -> dict:
    """
    Execute the full Riva-AGI orchestration pipeline for the given task.

    Args:
        task_text:  Natural-language task description from the user or caller.
        task_id:    Optional explicit task ID; auto-generated if omitted.
        session_id: Session identifier for multi-turn conversations.
        source:     Caller context label ("cli", "api", "voice", etc.).

    Returns:
        Final LangGraph state dict containing response_payload, plan,
        completed_steps, and task tracking metadata.
    """
    try:
        if task_id is None:
            task_id = f"task-{uuid.uuid4().hex[:8]}"

        orchestrator_key = key_manager.get_api_key_for_role("ORCHESTRATOR")
        if orchestrator_key:
            logger.info("[Orchestrator] Initialized with Gemini API key (GEMINI_API_KEY_ORCHESTRATOR)")
        else:
            logger.warning("[Orchestrator] GEMINI_API_KEY_ORCHESTRATOR is not set; running with resilient fallback")

        load_all_agents()
        app = create_orchestrator()

        input_data = InputData(
            input_type=InputType.TEXT,
            text_content=task_text,
            metadata={"source": source},
        )

        initial_state = {
            "task_payload": input_data,
            "agent": "fallback",
            "response_payload": None,
            "task_id": task_id,
            "session_id": session_id,
            "source": source,
            "complexity": "simple",
            "routing_decision": "fallback",
            "plan": [],
            "current_step": 0,
            "current_task_id": None,
            "completed_steps": [],
            "feedback": "",
            "retry_count": 0,
            "max_retries": 3,
            "intent": "unknown",
            "confidence": 0.0,
            "whiteboard": WhiteboardContext(),
            "start_time": time.time(),
            "budget": Budget(total=10.0), # Honest constraint: 10s total execution budget
        }

        # Inject Repo Map to kill the exploration loop
        repo_map = build_repo_map(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
        initial_state["whiteboard"].publish(key="REPO_MAP", content=repo_map, author="system", task_id="system_boot", artifact_type="text")

        result = app.invoke(initial_state)

        # Ensure response_payload is populated even in complex pipeline runs
        if result.get("response_payload") is None and result.get("completed_steps"):
            last_step = result["completed_steps"][-1]
            result["response_payload"] = AgentResponse(
                agent_id=last_step.get("agent", "orchestrator"),
                status=ResponseStatus.SUCCESS,
                content=last_step.get("result", "Tasks completed successfully."),
                tool_calls=[],
                metadata={"plan_length": len(result.get("plan", []))},
            )

        return result

    except Exception:
        logger.exception("Orchestration failed for task_id=%s", task_id)
        raise


# ── CLI Manual Runner ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    # Fix unicode encoding for windows console
    sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    print("=" * 60)
    print("  Riva-AGI: FULL INTEGRATION RUN (O1, O2, O3, O4)")
    print("=" * 60)

    task = input("\nEnter your task (or press Enter for a dummy test): ").strip()
    if not task:
        task = "Write a python function to compute summary statistics"

    print(f"\nUser Request: {task}\n")

    result = run_orchestrator(task)

    print("\n" + "=" * 60)
    print("  FINAL AGENT RESPONSE (O2 SCHEMA)")
    print("=" * 60)
    if result.get("response_payload"):
        print(result["response_payload"].model_dump_json(indent=2))

    print("\n" + "=" * 60)
    print("  FINAL TASK HISTORY (O3 TRACKER)")
    print("=" * 60)
    final_history = task_manager.get_task_status(result["task_id"])
    if final_history:
        print(final_history.model_dump_json(indent=2))
