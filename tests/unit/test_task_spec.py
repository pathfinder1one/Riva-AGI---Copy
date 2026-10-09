import pytest
from orchestration.orchestrator.schemas.task_spec import TaskSpec, TaskStatus, ExecutionPlan

def test_task_spec_creation():
    task = TaskSpec(
        task_id="t1",
        agent="coder",
        subtask="Write sorting algorithm in sort.py"
    )
    assert task.task_id == "t1"
    assert task.agent == "coder"
    assert task.status == TaskStatus.PENDING
    assert task.depends_on == []
    assert task.feedback is None

def test_task_spec_status_transitions():
    task = TaskSpec(
        task_id="t2",
        agent="writer",
        subtask="Draft documentation"
    )
    task.status = TaskStatus.IN_PROGRESS
    assert task.status == TaskStatus.IN_PROGRESS
    task.status = TaskStatus.COMPLETED
    task.result = "Documentation content"
    assert task.status == TaskStatus.COMPLETED
    assert task.result == "Documentation content"

def test_execution_plan_completion():
    plan = ExecutionPlan(
        plan_id="p1",
        goal="Develop feature",
        tasks=[
            TaskSpec(task_id="t1", agent="coder", subtask="Write code", status=TaskStatus.COMPLETED),
            TaskSpec(task_id="t2", agent="qa_tester", subtask="Test code", depends_on=["t1"], status=TaskStatus.PENDING)
        ]
    )
    assert not plan.is_complete()
    assert plan.get_task("t1").status == TaskStatus.COMPLETED
    assert plan.get_task("t2").status == TaskStatus.PENDING
    assert plan.get_task("nonexistent") is None

    plan.get_task("t2").status = TaskStatus.COMPLETED
    assert plan.is_complete()
