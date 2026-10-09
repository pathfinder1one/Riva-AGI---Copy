"""
DAG Dependency Scheduler — orchestration/orchestrator/dag_scheduler.py
======================================================================
Pure Python deterministic 0ms Directed Acyclic Graph scheduler.
Resolves dependency order, detects circular deadlocks, and identifies
parallel-ready tasks for concurrent dispatch.

Replaces the LLM-based executor step selection (Issue #7 & #10).
"""

import logging
from typing import List, Dict, Set, Optional

from orchestration.orchestrator.schemas.task_spec import TaskSpec, TaskStatus

logger = logging.getLogger(__name__)


class DAGScheduler:
    """
    Deterministic 0ms Directed Acyclic Graph Scheduler.

    Responsibilities:
    - Validate plan for circular dependencies (Kahn's algorithm).
    - Return all parallel-ready tasks whose dependencies are satisfied.
    - Return the single next executable task for sequential dispatch.

    Performance: < 1ms for plans with up to 50 tasks. Zero LLM calls.
    """

    @staticmethod
    def detect_cycle(tasks: List[TaskSpec]) -> bool:
        """
        Uses Kahn's algorithm to detect circular dependencies.
        Returns True if a cycle exists (deadlock), False if the DAG is valid.
        """
        if not tasks:
            return False

        in_degree: Dict[str, int] = {t.task_id: 0 for t in tasks}
        adj: Dict[str, List[str]] = {t.task_id: [] for t in tasks}
        task_ids = set(in_degree.keys())

        for t in tasks:
            for dep in t.depends_on:
                if dep in task_ids:
                    adj[dep].append(t.task_id)
                    in_degree[t.task_id] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited_count = 0

        while queue:
            node = queue.pop(0)
            visited_count += 1
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        has_cycle = visited_count != len(tasks)
        if has_cycle:
            logger.error("[DAGScheduler] Circular dependency detected in execution plan!")
        return has_cycle

    @staticmethod
    def get_ready_tasks(tasks: List[TaskSpec]) -> List[TaskSpec]:
        """
        Returns all tasks whose status is PENDING and all dependencies
        are COMPLETED. These tasks can be executed immediately in parallel.
        """
        completed_ids: Set[str] = {
            t.task_id for t in tasks if t.status == TaskStatus.COMPLETED
        }
        ready = []
        for t in tasks:
            if t.status == TaskStatus.PENDING:
                if all(dep in completed_ids for dep in t.depends_on):
                    ready.append(t)
        return ready

    @staticmethod
    def get_next_task(tasks: List[TaskSpec]) -> Optional[TaskSpec]:
        """
        Returns the first executable task for sequential / single-step dispatch.
        Returns None if no tasks are ready (all blocked or completed).
        """
        ready = DAGScheduler.get_ready_tasks(tasks)
        if ready:
            logger.info(
                f"[DAGScheduler] {len(ready)} task(s) ready. "
                f"Dispatching: {ready[0].task_id} -> {ready[0].agent}"
            )
            return ready[0]
        return None

    @staticmethod
    def all_completed(tasks: List[TaskSpec]) -> bool:
        """Returns True if every task in the plan has completed or been skipped."""
        return all(
            t.status in [TaskStatus.COMPLETED, TaskStatus.SKIPPED]
            for t in tasks
        )

    @staticmethod
    def has_failed(tasks: List[TaskSpec]) -> bool:
        """Returns True if any task in the plan has permanently failed."""
        return any(t.status == TaskStatus.FAILED for t in tasks)

    @staticmethod
    def compute_execution_waves(tasks: List[TaskSpec]) -> List[List[TaskSpec]]:
        """
        Partitions tasks into ordered execution waves (Topological Generation Slicing - Pillar 1).
        Tasks inside each wave are mutually independent and can execute concurrently.
        Raises ValueError if a circular dependency is detected.
        """
        if not tasks:
            return []

        if DAGScheduler.detect_cycle(tasks):
            raise ValueError("Cycle detected in DAG. Cannot partition into execution waves.")

        waves: List[List[TaskSpec]] = []
        completed_ids: Set[str] = set()
        remaining: List[TaskSpec] = list(tasks)

        while remaining:
            current_wave = [
                t for t in remaining
                if all(dep in completed_ids for dep in t.depends_on)
            ]
            if not current_wave:
                raise ValueError("Unresolvable dependency or deadlock detected in DAG.")

            waves.append(current_wave)
            for t in current_wave:
                completed_ids.add(t.task_id)
                remaining.remove(t)

        return waves
