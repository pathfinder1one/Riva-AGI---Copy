"""
Unit Tests for WhiteboardContext & Artifact History (Pillars 3 & 7)
"""

import pytest
import threading
from orchestration.orchestrator.pipeline.whiteboard import (
    WhiteboardContext,
    WhiteboardArtifact,
    ArtifactHistory,
)


def test_publish_and_retrieve_latest():
    wb = WhiteboardContext()
    artifact = wb.publish(
        key="user_model",
        content="class User:\n    id: int\n    name: str",
        author="coder",
        task_id="task_01",
        artifact_type="code"
    )

    assert artifact.version == 1
    assert artifact.key == "user_model"
    assert wb.get("user_model") == "class User:\n    id: int\n    name: str"
    assert "user_model" in wb.list_keys()


def test_versioning_and_diff():
    wb = WhiteboardContext()
    
    # Version 1
    wb.publish(
        key="config_yaml",
        content="port: 8000\ndebug: true\n",
        author="devops",
        task_id="task_01"
    )
    
    # Version 2 (modified)
    wb.publish(
        key="config_yaml",
        content="port: 8000\ndebug: false\nssl: true\n",
        author="devops",
        task_id="task_02"
    )

    v1_art = wb.get_artifact("config_yaml", version=1)
    v2_art = wb.get_artifact("config_yaml", version=2)
    latest_art = wb.get_artifact("config_yaml")

    assert v1_art is not None and v1_art.version == 1
    assert v2_art is not None and v2_art.version == 2
    assert latest_art.version == 2

    # Compute Diff
    diff = wb.get_diff("config_yaml", rev_a_idx=0, rev_b_idx=1)
    assert "-debug: true" in diff
    assert "+debug: false" in diff
    assert "+ssl: true" in diff


def test_get_artifacts_for_tasks():
    wb = WhiteboardContext()
    wb.publish("spec_doc", "API Spec 1.0", "researcher", "task_01")
    wb.publish("auth_code", "def auth(): pass", "coder", "task_02")
    wb.publish("test_plan", "Run 10 tests", "qa_tester", "task_03")

    # Upstream tasks: task_01 and task_02
    upstream = wb.get_artifacts_for_tasks(["task_01", "task_02"])
    assert "spec_doc" in upstream
    assert "auth_code" in upstream
    assert "test_plan" not in upstream


def test_format_context_for_prompt():
    wb = WhiteboardContext()
    wb.publish(
        key="database_schema",
        content="CREATE TABLE users (id SERIAL PRIMARY KEY);",
        author="coder",
        task_id="task_01",
        artifact_type="code"
    )

    prompt_context = wb.format_context_for_prompt(["task_01"])
    assert "### Upstream Artifact: `database_schema`" in prompt_context
    assert "CREATE TABLE users" in prompt_context
    assert "Type: code" in prompt_context


def test_thread_safety_concurrent_publishes():
    wb = WhiteboardContext()
    num_threads = 10
    updates_per_thread = 20

    def worker(agent_idx: int):
        for i in range(updates_per_thread):
            wb.publish(
                key=f"artifact_{agent_idx}",
                content=f"payload_{i}",
                author=f"agent_{agent_idx}",
                task_id=f"task_{agent_idx}_{i}"
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify all keys exist and have exact revision counts
    for i in range(num_threads):
        key = f"artifact_{i}"
        art = wb.get_artifact(key)
        assert art is not None
        assert art.version == updates_per_thread


def test_serialization_roundtrip():
    wb = WhiteboardContext()
    wb.publish("k1", "data 1", "agent_a", "task_01")
    wb.publish("k1", "data 2", "agent_a", "task_02")
    wb.publish("k2", {"status": "ok"}, "agent_b", "task_03", artifact_type="json")

    data_dict = wb.to_dict()
    restored_wb = WhiteboardContext.from_dict(data_dict)

    assert restored_wb.get("k1") == "data 2"
    assert restored_wb.get_artifact("k1", version=1).content == "data 1"
    assert restored_wb.get("k2") == {"status": "ok"}
