"""
Unit Tests for Bottom-Up Multi-Agent Aggregator & Deliverable Synthesis (Pillars 4 & 8)
"""

import pytest
from orchestration.orchestrator.pipeline.aggregator import (
    format_executive_deliverable,
    extract_file_paths,
    aggregator_agent
)
from orchestration.orchestrator.pipeline.whiteboard import WhiteboardContext
from orchestration import InputData, ResponseStatus, InputType


def test_extract_file_paths():
    sample_text = (
        "I have created the files models/user.py and tests/test_user.py. "
        "Also updated config.json and README.md. See http://example.com for info."
    )
    files = extract_file_paths(sample_text)
    assert "models/user.py" in files
    assert "tests/test_user.py" in files
    assert "config.json" in files
    assert "README.md" in files
    assert "http://example.com" not in files


def test_format_executive_deliverable_structure():
    completed_steps = [
        {
            "task_id": "task_01",
            "agent": "coder",
            "subtask": "Write redis cache client",
            "result": "Created redis_cache.py successfully.",
            "status": "completed"
        },
        {
            "task_id": "task_02",
            "agent": "qa_tester",
            "subtask": "Run unit tests",
            "result": "Tested redis_cache.py, 10/10 tests passed.",
            "status": "completed"
        }
    ]

    wb = WhiteboardContext()
    wb.publish("cache_code", "import redis", "coder", "task_01", "code")

    report = format_executive_deliverable(
        user_goal="Deploy Redis caching layer",
        completed_steps=completed_steps,
        whiteboard=wb,
        total_latency_ms=1250.5,
        confidence_score=0.96
    )

    assert "# 🚀 Riva-AGI Execution Deliverable: Deploy Redis caching layer" in report
    assert "completed **2 subtasks**" in report
    assert "| `task_01` | **coder** |" in report
    assert "| `task_02` | **qa_tester** |" in report
    assert "[`redis_cache.py`](file:///redis_cache.py)" in report
    assert "### 🧠 Shared Whiteboard Artifacts" in report
    assert "cache_code" in report
    assert "1250.50 ms" in report


def test_aggregator_agent_execution():
    data = InputData(
        input_type=InputType.TEXT,
        text_content="Build REST endpoints",
        metadata={
            "completed_steps": [
                {"task_id": "t1", "agent": "coder", "subtask": "endpoints", "result": "routes.py created"}
            ],
            "latency_ms": 500.0
        }
    )
    response = aggregator_agent(data)
    assert response.status == ResponseStatus.SUCCESS
    assert "routes.py" in response.content
    assert response.metadata["total_tasks_aggregated"] == 1
