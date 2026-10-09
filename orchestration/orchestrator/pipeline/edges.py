"""
Pipeline Edges — orchestration/orchestrator/pipeline/edges.py
=============================================================
Conditional routing functions for the Riva-AGI LangGraph orchestrator.

Each function receives the current AgentState and returns a string
key that LangGraph uses to determine the next node.

  route_after_intent    — After intent classification: complex→planner, else→worker/fallback
  route_after_executor  — After DAG executor: next-worker or reviewer or fallback
  route_after_worker    — After any worker agent: complex→executor loop, simple→reviewer
  route_after_reviewer  — After quality gate: retry/re-execute, aggregate, or END
"""
from langgraph.graph import END

from .state import AgentState


def route_after_intent(state: AgentState) -> str:
    """
    Route after the intent node.
    - complex task  → planner (for DAG decomposition)
    - fallback      → fallback (invalid/unknown intent)
    - simple task   → directly to the target worker agent
    """
    if state["complexity"] == "complex":
        return "planner"
    if state["agent"] == "fallback":
        return "fallback"
    return state["routing_decision"]


def route_after_executor(state: AgentState) -> str:
    """
    Route after the DAG executor node.
    - reviewer   → all subtasks done, send to quality gate
    - fallback   → unrecoverable scheduling failure
    - <agent>    → next ready subtask's assigned worker agent
    """
    if state["routing_decision"] == "reviewer":
        return "reviewer"
    if state["routing_decision"] == "fallback":
        return "fallback"
    return state["routing_decision"]


def route_after_worker(state: AgentState) -> str:
    """
    Route after any worker agent node completes.
    - complex  → back to executor (to pick next DAG subtask)
    - simple   → directly to reviewer (single-step quality check)
    """
    if state["complexity"] == "complex":
        return "executor"
    return "reviewer"


def route_after_reviewer(state: AgentState) -> str:
    """
    Route after the reviewer quality gate.
    - rejected + complex  → executor (retry via DAG loop with feedback injected into plan)
    - rejected + simple   → back to the worker agent that produced the output
    - fallback            → fallback (max retries exceeded)
    - complex + done      → aggregator (synthesise full executive deliverable)
    - simple + approved   → END
    """
    if state["routing_decision"] == "rejected":
        if state["complexity"] == "complex":
            return "executor"
        return state.get("agent", "fallback")
    if state["routing_decision"] == "fallback":
        return "fallback"
    if state.get("complexity") == "complex" and len(state.get("completed_steps", [])) >= 1:
        return "aggregator"
    return END
