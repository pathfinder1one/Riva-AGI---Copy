"""
Pipeline Nodes — orchestration/orchestrator/pipeline/nodes.py
=============================================================
All 7 LangGraph node functions for the Riva-AGI orchestration pipeline:

  intent_node       — Sub-35ms Zero-LLM intent & complexity classifier (Laya Engine)
  planner_node      — Hierarchical DAG task decomposer (Kahn cycle detection)
  executor_node     — Deterministic DAG scheduler (<1ms, zero-LLM)
  reviewer_node     — Agent-aware quality gate with retry cap (Noul <35ms)
  create_agent_node — Dynamic worker factory: dispatches any registered agent +
                      injects Whiteboard context + publishes output artifact
  aggregator_node   — Bottom-up multi-agent deliverable synthesiser (Pillars 4 & 8)
  fallback_node     — Graceful failure containment node
"""
import json
import logging
import time
from typing import Dict, Any, List, Optional

from orchestration.orchestrator.infra.registry import registry
from orchestration.orchestrator.pipeline.state_manager import TaskStatus
from orchestration.orchestrator.routing.laya_engine import laya_engine
from orchestration.orchestrator.planning.dag_scheduler import DAGScheduler
from orchestration.orchestrator.schemas.task_spec import TaskSpec, TaskStatus as SpecTaskStatus
from orchestration.orchestrator.pipeline.whiteboard import WhiteboardContext
from orchestration.orchestrator.pipeline.aggregator import format_executive_deliverable
from orchestration.orchestrator.infra.security import security_gate
from orchestration import InputData, AgentResponse, ResponseStatus, InputType

from .state import AgentState, task_manager, clean_json, safe_update_task_state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# intent_node
# ---------------------------------------------------------------------------
def intent_node(state: AgentState):
    """
    Sub-35ms Non-Autoregressive Intent & Complexity Classifier.
    Eliminates Gemini LLM call from intent classification.
    """
    task_id = state["task_id"]
    task_text = state["task_payload"].text_content or ""
    task_manager.start_task(task_id=task_id, initial_data={"task": task_text})
    task_manager.update_task_state(task_id, "intent_classifier", TaskStatus.IN_PROGRESS)

    registered_agents = registry.get_all_capabilities().keys()
    worker_candidates = [
        a for a in registered_agents
        if a not in ["intent_classifier", "planner", "executor", "reviewer"]
    ]

    target_agent, complexity, confidence = laya_engine.route_intent(task_text, worker_candidates)
    logger.info(f"[IntentNode] Decision: target={target_agent}, complexity={complexity}, conf={confidence:.2f}")

    if target_agent not in worker_candidates and target_agent not in ["planner", "fallback"]:
        target_agent = "fallback"

    task_manager.update_task_state(task_id, "intent_classifier", TaskStatus.COMPLETED)

    routing_decision = "planner" if complexity == "complex" else target_agent

    return {
        "complexity": complexity,
        "routing_decision": routing_decision,
        "agent": target_agent,
        "intent": "coding" if target_agent == "coder" else (
            "research" if target_agent == "researcher" else target_agent
        ),
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# planner_node
# ---------------------------------------------------------------------------
def planner_node(state: AgentState):
    """
    Constructs a structured DAG ExecutionPlan with TaskSpecs.
    Uses Kahn's algorithm to detect and break dependency cycles.
    """
    task_id = state["task_id"]
    safe_update_task_state(task_id, "planner", TaskStatus.IN_PROGRESS, state["task_payload"].text_content or "")

    handler = registry.get_agent("planner")
    response = handler(state["task_payload"])

    tasks_list: List[Dict[str, Any]] = []
    try:
        raw_plan = json.loads(clean_json(response.content))
        if isinstance(raw_plan, list):
            for idx, item in enumerate(raw_plan):
                tid = item.get("task_id", f"task_{idx+1:02d}")
                agent = item.get("agent", "coder")
                subtask = item.get("subtask") or item.get("task", state["task_payload"].text_content)
                deps = item.get("depends_on", [])
                tasks_list.append(
                    TaskSpec(
                        task_id=tid,
                        agent=agent,
                        subtask=subtask,
                        depends_on=deps,
                        status=SpecTaskStatus.PENDING
                    ).model_dump()
                )
    except Exception as e:
        logger.warning(f"[PlannerNode] Failed to parse plan JSON ({e}). Falling back to single-task DAG.")

    if not tasks_list:
        tasks_list = [
            TaskSpec(
                task_id="task_01",
                agent=state.get("agent", "coder"),
                subtask=state["task_payload"].text_content or "",
                depends_on=[],
                status=SpecTaskStatus.PENDING
            ).model_dump()
        ]

    # Validate cycles using Kahn's algorithm
    task_specs = [TaskSpec(**t) for t in tasks_list]
    if DAGScheduler.detect_cycle(task_specs):
        logger.warning("[PlannerNode] Cycle detected in plan dependencies. Linearizing tasks.")
        for i in range(len(tasks_list)):
            tasks_list[i]["depends_on"] = [tasks_list[i-1]["task_id"]] if i > 0 else []

    safe_update_task_state(task_id, "planner", TaskStatus.COMPLETED)
    return {"plan": tasks_list, "current_step": 0, "completed_steps": []}


# ---------------------------------------------------------------------------
# executor_node
# ---------------------------------------------------------------------------
def executor_node(state: AgentState):
    """
    Zero-LLM Deterministic DAG Scheduler (<1ms).
    Resolves next ready TaskSpec without LLM overhead.
    """
    task_id = state["task_id"]
    safe_update_task_state(task_id, "executor", TaskStatus.IN_PROGRESS)

    plan_dicts = state.get("plan", [])
    tasks = [TaskSpec(**t) for t in plan_dicts]

    next_task = DAGScheduler.get_next_task(tasks)

    if next_task:
        next_task.status = SpecTaskStatus.IN_PROGRESS
        updated_plan = [t.model_dump() for t in tasks]
        logger.info(f"[DAGExecutor] Next ready subtask: [{next_task.task_id}] -> Agent: [{next_task.agent}]")
        safe_update_task_state(task_id, "executor", TaskStatus.COMPLETED)
        return {
            "routing_decision": next_task.agent,
            "agent": next_task.agent,
            "current_task_id": next_task.task_id,
            "plan": updated_plan,
        }
    else:
        # All tasks completed — hand off to Reviewer Quality Gate
        logger.info("[DAGExecutor] All DAG tasks completed. Routing to Reviewer Quality Gate.")
        safe_update_task_state(task_id, "executor", TaskStatus.COMPLETED)
        return {
            "routing_decision": "reviewer",
            "agent": "reviewer",
        }


# ---------------------------------------------------------------------------
# reviewer_node
# ---------------------------------------------------------------------------
def reviewer_node(state: AgentState):
    """
    Agent-Aware Universal Quality Gate.
    Verifies domain profile satisfaction (<35ms with Laya Noul) and manages retries.
    Triggers Self-Healing Workspace Rollback (Pillar 9) when max_retries is exceeded.
    """
    task_id = state["task_id"]
    safe_update_task_state(task_id, "reviewer", TaskStatus.IN_PROGRESS)

    completed_steps = state.get("completed_steps", [])
    last_output = ""
    last_agent = state.get("agent", "coder")

    if completed_steps:
        last_step = completed_steps[-1]
        last_output = last_step.get("result", "")
        last_agent = last_step.get("agent", last_agent)
    elif state.get("response_payload"):
        last_output = state["response_payload"].content

    review_input = InputData(
        input_type=InputType.TEXT,
        text_content=last_output,
        metadata={"target_agent": last_agent, "goal": state["task_payload"].text_content}
    )

    handler = registry.get_agent("reviewer")
    response = handler(review_input)

    try:
        data = json.loads(clean_json(response.content))
        status = data.get("status", "approved")
        feedback = data.get("feedback", "")
    except Exception:
        status = "approved"
        feedback = ""

    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)
    plan_dicts = list(state.get("plan", []))

    if status == "rejected":
        retry_count += 1
        logger.warning(f"[Reviewer] Task rejected on attempt {retry_count}/{max_retries}. Feedback: {feedback}")
        if retry_count >= max_retries:
            logger.error(
                f"[Reviewer] Exceeded max retries ({max_retries}). "
                "Triggering Self-Healing Workspace Rollback (Pillar 9)."
            )
            rolled_back = security_gate.rollback_workspace()
            if rolled_back:
                logger.warning(f"[SecurityGate] Rolled back workspace changes: {rolled_back}")
            safe_update_task_state(task_id, "reviewer", TaskStatus.FAILED)
            return {
                "routing_decision": "fallback",
                "feedback": feedback,
                "retry_count": retry_count,
            }
        else:
            # Reopen the failed subtask in the plan and inject reviewer feedback
            if plan_dicts:
                last_task_dict = plan_dicts[-1]
                last_task_dict["status"] = SpecTaskStatus.PENDING.value
                last_task_dict["feedback"] = feedback
            safe_update_task_state(task_id, "reviewer", TaskStatus.COMPLETED)
            return {
                "routing_decision": "rejected",
                "feedback": feedback,
                "retry_count": retry_count,
                "plan": plan_dicts,
            }

    # Task approved — commit workspace file changes
    security_gate.commit_workspace()
    safe_update_task_state(task_id, "reviewer", TaskStatus.COMPLETED)
    return {
        "routing_decision": "approved",
        "feedback": feedback,
        "retry_count": retry_count,
    }


# ---------------------------------------------------------------------------
# create_agent_node  (Dynamic Worker Factory)
# ---------------------------------------------------------------------------
from dataclasses import dataclass
from typing import Any
import asyncio

@dataclass
class P: 
    critical: bool
    cap: float
    max_tokens: int
    chain: Any
    rounds: int = 1

PROFILES = {
  "intent":           P(True,  0.5, 50,   None),            
  "planner":          P(True,  0.2, 0,    None),            
  "researcher":       P(False, 3.0, 300,  None),
  "designer":         P(False, 3.0, 600,  None),
  "coder":            P(True,  7.0, 1500, None),            
  "qa":               P(False, 2.5, 800,  None),
  "qa_tester":        P(False, 2.5, 800,  None),
  "reviewer":         P(True,  0.5, 0,    None),            
  "devops":           P(False, 2.5, 300,  None),
  "seo_specialist":   P(False, 2.5, 400,  None),
  "writer":           P(True,  1.0, 400,  None),
  "reasoner":         P(False, 2.5, 500,  None),
  "data_analyst":     P(False, 2.5, 500,  None),
  "security_auditor": P(False, 2.5, 500,  None),
  "knowledge_agent":  P(False, 2.5, 500,  None),
}

def create_agent_node(agent_name: str):
    def node_func(state: AgentState):
        task_id = state["task_id"]
        safe_update_task_state(task_id, agent_name, TaskStatus.IN_PROGRESS)
        
        p = PROFILES.get(agent_name, P(False, 2.5, 300, None))
        budget = state.get("budget")
        if not p.critical and budget and budget.left() < p.cap:
            logger.info(f"[{agent_name}] Skipped due to low budget ({budget.left():.2f}s < {p.cap}s)")
            safe_update_task_state(task_id, agent_name, TaskStatus.COMPLETED) # or skipped
            return {"routing_decision": "approved", "agent": agent_name}

        handler = registry.get_agent(agent_name)
        if not handler and agent_name == "qa":
            handler = registry.get_agent("qa_tester")
        if not handler and agent_name == "qa_tester":
            handler = registry.get_agent("qa")
        if not handler:
            logger.error(f"Agent [{agent_name}] not found in registry.")
            safe_update_task_state(task_id, agent_name, TaskStatus.FAILED)
            return {"routing_decision": "fallback", "agent": "fallback"}

        # Resolve active subtask from the DAG plan
        plan_dicts = list(state.get("plan", []))
        current_tid = state.get("current_task_id")
        active_task: Optional[Dict[str, Any]] = None

        if state.get("complexity") == "complex" and plan_dicts:
            for t in plan_dicts:
                if t.get("task_id") == current_tid:
                    active_task = t
                    break

        wb: WhiteboardContext = state.get("whiteboard")
        if wb is None:
            wb = WhiteboardContext()

        budget_remaining = budget.left() if budget else 10.0
        # Build the worker prompt (subtask-aware or direct)
        if active_task:
            subtask_prompt = active_task.get("subtask", state["task_payload"].text_content or "")
            if active_task.get("feedback"):
                subtask_prompt += (
                    f"\n\n[REVIEWER CRITIQUE FROM PREVIOUS ATTEMPT]: {active_task['feedback']}"
                    "\nPlease fix these issues."
                )

            # Inject context of upstream dependencies from the Whiteboard
            deps = active_task.get("depends_on", [])
            upstream_artifacts = wb.format_context_for_prompt(deps) if deps else ""
            if upstream_artifacts:
                subtask_prompt += f"\n\n[UPSTREAM ARTIFACTS FROM WHITEBOARD]:\n{upstream_artifacts}"
            else:
                # Fallback: inject prior completed step outputs if whiteboard is empty
                completed = state.get("completed_steps", [])
                if completed:
                    subtask_prompt += "\n\n[PRIOR COMPLETED ARTIFACTS]:\n" + "\n".join(
                        f"### Agent {c.get('agent')} Output:\n{c.get('result', '')}\n"
                        for c in completed
                    )

            worker_payload = InputData(
                input_type=InputType.TEXT,
                text_content=subtask_prompt,
                metadata={"task_id": current_tid, "source": state.get("source", "cli"), "budget_left": budget_remaining}
            )
        else:
            worker_payload = state["task_payload"]
            worker_payload.metadata["budget_left"] = budget_remaining

        # Execute the worker agent
        response = handler(worker_payload)

        # Publish output artifact to Whiteboard
        tid = active_task.get("task_id", "direct_task") if active_task else "direct_task"
        art_type = (
            "code" if agent_name in ("coder", "devops")
            else ("json_schema" if agent_name == "designer" else "text")
        )
        wb.publish(
            key=f"{tid}_{agent_name}_output",
            content=response.content,
            author=agent_name,
            task_id=tid,
            artifact_type=art_type,
        )

        # Record completed step in plan & completed_steps list
        completed_steps = list(state.get("completed_steps", []))
        if active_task:
            active_task["status"] = SpecTaskStatus.COMPLETED.value
            active_task["result"] = response.content
            completed_steps.append({
                "task_id": active_task.get("task_id"),
                "agent": agent_name,
                "result": response.content,
            })
        else:
            completed_steps.append({
                "task_id": "direct_task",
                "agent": agent_name,
                "result": response.content,
            })

        safe_update_task_state(task_id, agent_name, TaskStatus.COMPLETED)
        return {
            "response_payload": response,
            "completed_steps": completed_steps,
            "plan": plan_dicts,
            "whiteboard": wb,
        }

    return node_func


# ---------------------------------------------------------------------------
# aggregator_node
# ---------------------------------------------------------------------------
def aggregator_node(state: AgentState):
    """
    Bottom-Up Multi-Agent Aggregator Node (Pillars 4 & 8).
    Synthesises outputs from all executed DAG steps into an executive deliverable.
    """
    task_id = state["task_id"]
    safe_update_task_state(task_id, "aggregator", TaskStatus.IN_PROGRESS)

    goal = state["task_payload"].text_content or "Orchestration Goal"
    completed = state.get("completed_steps", [])
    wb = state.get("whiteboard")

    elapsed_ms = (time.time() - state.get("start_time", time.time())) * 1000
    deliverable = format_executive_deliverable(
        user_goal=goal,
        completed_steps=completed,
        whiteboard=wb,
        total_latency_ms=elapsed_ms,
    )

    response = AgentResponse(
        agent_id="aggregator",
        status=ResponseStatus.SUCCESS,
        content=deliverable,
        tool_calls=[],
        metadata={"total_completed_steps": len(completed)},
    )

    safe_update_task_state(task_id, "aggregator", TaskStatus.COMPLETED)
    return {
        "response_payload": response,
        "routing_decision": "approved",
        "agent": "aggregator",
    }


# ---------------------------------------------------------------------------
# fallback_node
# ---------------------------------------------------------------------------
def fallback_node(state: AgentState):
    """
    Graceful failure containment node.
    Reached when routing is invalid, retries are exhausted, or agent is missing.
    """
    task_id = state["task_id"]
    safe_update_task_state(task_id, "fallback", TaskStatus.FAILED)

    fallback_response = AgentResponse(
        agent_id="fallback",
        status=ResponseStatus.FAILURE,
        content="Fallback agent reached due to invalid routing, exceeded retries, or missing capabilities.",
        tool_calls=[],
        error_message="Fallback reached.",
    )
    return {
        "routing_decision": "approved",
        "agent": "fallback",
        "response_payload": fallback_response,
    }
