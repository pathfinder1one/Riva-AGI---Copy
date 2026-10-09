"""
Pipeline State — orchestration/orchestrator/pipeline/state.py
=============================================================
AgentState TypedDict schema and shared state helper utilities
used by all LangGraph nodes in the Riva-AGI orchestration pipeline.
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from typing import TypedDict, List, Dict, Any, Optional
import time

class Budget:
    def __init__(self, total: float = 10.0):
        self.end = time.monotonic() + total
    
    def left(self) -> float:
        return max(0.0, self.end - time.monotonic())

from orchestration.orchestrator.pipeline.state_manager import TaskStateManager, TaskStatus
from orchestration.orchestrator.pipeline.whiteboard import WhiteboardContext
from orchestration import InputData, AgentResponse

# ---------------------------------------------------------------------------
# Single global State Manager (shared across the entire orchestration run)
# ---------------------------------------------------------------------------
task_manager = TaskStateManager()


# ---------------------------------------------------------------------------
# AgentState — LangGraph State Schema
# ---------------------------------------------------------------------------
from typing import TypedDict, List, Dict, Any, Optional, Annotated

def merge_completed_steps(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not old: return new
    merged = {f"{item.get('task_id')}_{item.get('agent')}": item for item in old}
    for item in new:
        merged[f"{item.get('task_id')}_{item.get('agent')}"] = item
    return list(merged.values())

def merge_plans(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not old: return new
    merged = {item.get('task_id'): item for item in old}
    for item in new:
        merged[item.get('task_id')] = item
    return list(merged.values())

def merge_response(old: Optional[AgentResponse], new: Optional[AgentResponse]) -> Optional[AgentResponse]:
    return new if new is not None else old

def merge_str(old: str, new: str) -> str:
    return new

def merge_whiteboard(old: WhiteboardContext, new: WhiteboardContext) -> WhiteboardContext:
    if old is None: return new
    if new is None: return old
    for k, v in new._histories.items():
        if k not in old._histories:
            old._histories[k] = v
        else:
            old._histories[k].revisions.extend(v.revisions)
    return old

class AgentState(TypedDict):
    task_payload: InputData
    agent: Annotated[str, merge_str]
    response_payload: Annotated[Optional[AgentResponse], merge_response]
    task_id: str
    session_id: str
    source: str

    complexity: Annotated[str, merge_str]
    routing_decision: Annotated[str, merge_str]
    plan: Annotated[List[Dict[str, Any]], merge_plans]
    current_step: int
    current_task_id: Optional[str]
    completed_steps: Annotated[List[Dict[str, Any]], merge_completed_steps]
    feedback: Annotated[str, merge_str]
    retry_count: int
    max_retries: int
    intent: Annotated[str, merge_str]
    confidence: float
    whiteboard: Annotated[WhiteboardContext, merge_whiteboard]
    start_time: float
    budget: Budget


# ---------------------------------------------------------------------------
# State Helper Utilities
# ---------------------------------------------------------------------------
def clean_json(text: str) -> str:
    """Strip markdown code fences from a JSON string returned by an LLM."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def safe_update_task_state(
    task_id: str,
    owner: str,
    status: TaskStatus,
    initial_text: str = "",
) -> None:
    """
    Update task state safely, auto-starting the task record if it doesn't exist yet.
    Swallows all errors to prevent state-tracking failures from breaking the pipeline.
    """
    try:
        task_manager.update_task_state(task_id, owner, status)
    except ValueError:
        try:
            task_manager.start_task(task_id=task_id, initial_data={"task": initial_text})
            task_manager.update_task_state(task_id, owner, status)
        except Exception:
            pass
