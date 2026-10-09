"""
Whiteboard Context & Artifact Versioning Engine — orchestrator/whiteboard.py
=============================================================================
Thread-safe in-memory Blackboard system (Pillars 3 & 7) providing:
- Cross-agent typed artifact sharing (code, schema, table, markdown, text)
- Append-only revision history per artifact key
- Automated unified diff tracking via difflib
- Dependency-scoped upstream artifact formatting for agent prompts
"""

import time
import difflib
import threading
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class WhiteboardArtifact(BaseModel):
    """A single versioned artifact published to the Whiteboard."""
    key: str = Field(..., description="Unique key or identifier for the artifact")
    content: Any = Field(..., description="The artifact payload (string, dict, code, etc.)")
    author_agent: str = Field(..., description="Name of the agent that produced this artifact")
    task_id: str = Field(..., description="The specific subtask ID that generated this artifact")
    timestamp: float = Field(default_factory=time.time, description="Unix timestamp of publication")
    artifact_type: str = Field(default="text", description="Type: code, json_schema, data_table, markdown_doc, text")
    version: int = Field(default=1, description="Sequential revision number (1-indexed)")


class ArtifactHistory(BaseModel):
    """Maintains an append-only revision history for a given artifact key."""
    key: str
    revisions: List[WhiteboardArtifact] = Field(default_factory=list)

    def add_revision(self, artifact: WhiteboardArtifact) -> None:
        """Appends a new revision, ensuring sequential version numbering."""
        next_ver = len(self.revisions) + 1
        artifact.version = next_ver
        self.revisions.append(artifact)

    def get_latest(self) -> Optional[WhiteboardArtifact]:
        """Returns the most recent revision of the artifact."""
        if not self.revisions:
            return None
        return self.revisions[-1]

    def get_version(self, version: int) -> Optional[WhiteboardArtifact]:
        """Retrieves a specific 1-indexed version."""
        for rev in self.revisions:
            if rev.version == version:
                return rev
        return None

    def get_diff(self, rev_a_idx: int = -2, rev_b_idx: int = -1) -> str:
        """
        Computes a unified diff between two revisions using standard difflib.
        rev_a_idx defaults to second-to-last, rev_b_idx defaults to latest.
        """
        if len(self.revisions) < 2:
            return ""
        try:
            rev_a = self.revisions[rev_a_idx]
            rev_b = self.revisions[rev_b_idx]
        except IndexError:
            return ""

        text_a = str(rev_a.content).splitlines(keepends=True)
        text_b = str(rev_b.content).splitlines(keepends=True)

        diff_lines = list(difflib.unified_diff(
            text_a,
            text_b,
            fromfile=f"{self.key}_v{rev_a.version}",
            tofile=f"{self.key}_v{rev_b.version}",
        ))
        return "".join(diff_lines)


class WhiteboardContext:
    """
    Central Thread-Safe Cross-Agent Blackboard Memory.
    Enables agents to publish and consume intermediate state across DAG waves.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._histories: Dict[str, ArtifactHistory] = {}

    def publish(
        self,
        key: str,
        content: Any,
        author: str,
        task_id: str,
        artifact_type: str = "text"
    ) -> WhiteboardArtifact:
        """Publishes or updates an artifact with version incrementation."""
        with self._lock:
            if key not in self._histories:
                self._histories[key] = ArtifactHistory(key=key)

            artifact = WhiteboardArtifact(
                key=key,
                content=content,
                author_agent=author,
                task_id=task_id,
                artifact_type=artifact_type
            )
            self._histories[key].add_revision(artifact)
            return artifact

    def get(self, key: str, version: Optional[int] = None) -> Optional[Any]:
        """Retrieves the content of an artifact (latest or specific version)."""
        artifact = self.get_artifact(key, version)
        return artifact.content if artifact else None

    def get_artifact(self, key: str, version: Optional[int] = None) -> Optional[WhiteboardArtifact]:
        """Retrieves the WhiteboardArtifact object."""
        with self._lock:
            history = self._histories.get(key)
            if not history:
                return None
            if version is not None:
                return history.get_version(version)
            return history.get_latest()

    def get_diff(self, key: str, rev_a_idx: int = -2, rev_b_idx: int = -1) -> str:
        """Retrieves the unified diff between two revisions of an artifact."""
        with self._lock:
            history = self._histories.get(key)
            if not history:
                return ""
            return history.get_diff(rev_a_idx, rev_b_idx)

    def get_artifacts_for_tasks(self, task_ids: List[str]) -> Dict[str, WhiteboardArtifact]:
        """Retrieves all latest artifacts published by a given list of task IDs."""
        with self._lock:
            result = {}
            for key, history in self._histories.items():
                latest = history.get_latest()
                if latest and latest.task_id in task_ids:
                    result[key] = latest
            return result

    def list_keys(self) -> List[str]:
        """Returns all registered artifact keys."""
        with self._lock:
            return list(self._histories.keys())

    def list_artifacts(self) -> List[WhiteboardArtifact]:
        """Returns all latest artifacts across all keys."""
        with self._lock:
            return [h.get_latest() for h in self._histories.values() if h.get_latest() is not None]

    def format_context_for_prompt(self, task_ids: List[str]) -> str:
        """
        Formats all upstream artifacts published by prerequisite tasks into a
        structured Markdown block ready for prompt injection.
        """
        matching = self.get_artifacts_for_tasks(task_ids)
        if not matching:
            return ""

        sections = []
        for key, artifact in matching.items():
            header = f"### Upstream Artifact: `{key}` (Type: {artifact.artifact_type}, Author: {artifact.author_agent}, Version: {artifact.version})"
            content_str = str(artifact.content).strip()
            
            if artifact.artifact_type == "code":
                body = f"```\n{content_str}\n```"
            elif artifact.artifact_type in ("json_schema", "json"):
                body = f"```json\n{content_str}\n```"
            else:
                body = content_str

            sections.append(f"{header}\n{body}")

        return "\n\n".join(sections)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes whiteboard state to a standard Python dictionary."""
        with self._lock:
            return {
                key: [rev.model_dump() for rev in history.revisions]
                for key, history in self._histories.items()
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WhiteboardContext":
        """Deserializes a dictionary back into a WhiteboardContext instance."""
        instance = cls()
        with instance._lock:
            for key, rev_list in data.items():
                history = ArtifactHistory(key=key)
                for rev_dict in rev_list:
                    artifact = WhiteboardArtifact(**rev_dict)
                    history.revisions.append(artifact)
                instance._histories[key] = history
        return instance
