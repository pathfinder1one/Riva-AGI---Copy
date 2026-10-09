"""
Pipeline Graph — orchestration/orchestrator/pipeline/graph.py
=============================================================
Builds and compiles the Riva-AGI LangGraph StateGraph.

  create_orchestrator() — single factory that wires all nodes, conditional
                          edges, and agent worker nodes, then compiles the graph.
"""
from langgraph.graph import StateGraph, START, END

from orchestration.orchestrator.infra.registry import registry

from .state import AgentState
from .nodes import (
    intent_node,
    planner_node,
    executor_node,
    reviewer_node,
    create_agent_node,
    aggregator_node,
    fallback_node,
)
from .edges import (
    route_after_intent,
    route_after_executor,
    route_after_worker,
    route_after_reviewer,
)


def create_orchestrator():
    """
    Compile and return the Riva-AGI LangGraph dynamic orchestration graph.
    """
    graph = StateGraph(AgentState)

    # ── Core Orchestration Nodes ───────────────────────────────────────────
    graph.add_node("intent", intent_node)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("fallback", fallback_node)

    # ── Dynamic Worker Agent Nodes ─────────────────────────────────────────
    registered_agents = list(registry.get_all_capabilities().keys())
    excluded = ["intent_classifier", "intent", "planner", "executor", "reviewer", "aggregator", "fallback"]
    workers = [a for a in registered_agents if a not in excluded]

    standard_workers = [
        "coder", "researcher", "reasoner", "data_analyst", "designer",
        "security_auditor", "qa_tester", "qa", "devops", "seo_specialist", "writer"
    ]
    for sw in standard_workers:
        if sw not in workers:
            workers.append(sw)

    for agent_name in workers:
        graph.add_node(agent_name, create_agent_node(agent_name))

    # ── Topology: START -> Intent ──────────────────────────────────────────
    graph.add_edge(START, "intent")

    # ── Intent Routing (Complex -> Planner, Simple -> Direct Worker, Unknown -> Fallback)
    intent_map = {"planner": "planner", "fallback": "fallback"}
    for w in workers:
        intent_map[w] = w
    graph.add_conditional_edges("intent", route_after_intent, intent_map)

    # ── Planner -> Executor ────────────────────────────────────────────────
    graph.add_edge("planner", "executor")

    # ── Executor Routing (Next Ready Subtask -> Worker, or Done -> Reviewer)
    exec_map = {w: w for w in workers}
    exec_map["reviewer"] = "reviewer"
    exec_map["fallback"] = "fallback"
    graph.add_conditional_edges("executor", route_after_executor, exec_map)

    # ── Worker Routing (Complex -> Back to Executor, Simple -> Reviewer) ───
    for agent_name in workers:
        graph.add_conditional_edges(
            agent_name,
            route_after_worker,
            {"executor": "executor", "reviewer": "reviewer"}
        )

    # ── Reviewer Routing (Approved -> Aggregator/END, Rejected -> Retry) ───
    rev_targets = {
        "executor": "executor",
        "fallback": "fallback",
        "aggregator": "aggregator",
        END: END,
    }
    for w in workers:
        rev_targets[w] = w
    graph.add_conditional_edges("reviewer", route_after_reviewer, rev_targets)

    # ── Terminal Edges ─────────────────────────────────────────────────────
    graph.add_edge("aggregator", END)
    graph.add_edge("fallback", END)

    return graph.compile()
