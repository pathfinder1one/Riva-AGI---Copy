import pytest
from orchestration.orchestrator.schemas.task_spec import TaskSpec, TaskStatus
from orchestration.orchestrator.planning.dag_scheduler import DAGScheduler

def test_dag_scheduler_acyclic():
    tasks = [
        TaskSpec(task_id="t1", agent="coder", subtask="Step 1", depends_on=[]),
        TaskSpec(task_id="t2", agent="qa_tester", subtask="Step 2", depends_on=["t1"]),
        TaskSpec(task_id="t3", agent="devops", subtask="Step 3", depends_on=["t2"])
    ]
    assert not DAGScheduler.detect_cycle(tasks)

def test_dag_scheduler_cycle_detection():
    cyclic_tasks = [
        TaskSpec(task_id="t1", agent="coder", subtask="Step 1", depends_on=["t2"]),
        TaskSpec(task_id="t2", agent="coder", subtask="Step 2", depends_on=["t1"])
    ]
    assert DAGScheduler.detect_cycle(cyclic_tasks)

def test_dag_scheduler_readiness_resolution():
    tasks = [
        TaskSpec(task_id="t1", agent="coder", subtask="Step 1", status=TaskStatus.PENDING),
        TaskSpec(task_id="t2", agent="researcher", subtask="Step 2", status=TaskStatus.PENDING),
        TaskSpec(task_id="t3", agent="qa_tester", subtask="Step 3", depends_on=["t1", "t2"], status=TaskStatus.PENDING)
    ]
    
    # Both t1 and t2 should be ready initially
    ready = DAGScheduler.get_ready_tasks(tasks)
    ready_ids = [t.task_id for t in ready]
    assert "t1" in ready_ids
    assert "t2" in ready_ids
    assert "t3" not in ready_ids

    # Complete t1
    tasks[0].status = TaskStatus.COMPLETED
    ready = DAGScheduler.get_ready_tasks(tasks)
    ready_ids = [t.task_id for t in ready]
    assert "t2" in ready_ids
    assert "t3" not in ready_ids

    # Complete t2
    tasks[1].status = TaskStatus.COMPLETED
    ready = DAGScheduler.get_ready_tasks(tasks)
    ready_ids = [t.task_id for t in ready]
    assert "t3" in ready_ids
