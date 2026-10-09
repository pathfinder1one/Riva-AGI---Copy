"""
Planning Package — orchestration/orchestrator/planning/
=======================================================
Hierarchical task decomposition and DAG scheduling layer.

  planner        — Decomposes complex goals into structured TaskSpec DAGs
  dag_scheduler  — Deterministic topological task ordering with Kahn cycle detection
"""
from .dag_scheduler import DAGScheduler
from .planner import plan_hierarchical_tasks, detect_workstreams

__all__ = [
    "DAGScheduler",
    "plan_hierarchical_tasks",
    "detect_workstreams",
]
