"""
Tool Permission & Security Gate — orchestration/orchestrator/security.py
========================================================================
Enforces 3-tier risk policies (READ_ONLY, REVERSIBLE, IRREVERSIBLE)
with Laya risk assessment, command pattern blacklisting, and path traversal prevention.
"""
import json
import logging
import os
import re
from enum import Enum
from typing import Dict, Any, Tuple, List, Optional
try:
    from orchestration.orchestrator.routing.laya_engine import laya_engine
except ImportError:
    laya_engine = None

logger = logging.getLogger(__name__)

class RiskTier(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"

class WorkspaceTransactionManager:
    """
    Transactional Workspace Manager (Pillar 9: Self-Healing & Auto-Rollback).
    Takes shadow pre-state snapshots of files before modification and rolls back on failure.
    """
    def __init__(self):
        self._snapshots: Dict[str, Optional[str]] = {}

    def record_pre_state(self, file_path: str) -> None:
        abs_path = os.path.abspath(file_path)
        if abs_path not in self._snapshots:
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        self._snapshots[abs_path] = f.read()
                except Exception as e:
                    logger.warning(f"[TransactionManager] Failed to read pre-state of {abs_path}: {e}")
            else:
                self._snapshots[abs_path] = None  # Indicates newly created file

    def rollback(self) -> List[str]:
        """Restores files to their pre-transaction states or removes newly created files."""
        restored = []
        for path, original_content in list(self._snapshots.items()):
            try:
                if original_content is None:
                    if os.path.exists(path):
                        os.remove(path)
                        restored.append(f"Deleted new file: {os.path.basename(path)}")
                else:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(original_content)
                    restored.append(f"Restored modified file: {os.path.basename(path)}")
            except Exception as e:
                logger.error(f"[TransactionManager] Error rolling back {path}: {e}")
        self._snapshots.clear()
        if restored:
            logger.info(f"[SecurityGate] Rolled back {len(restored)} workspace file(s): {restored}")
        return restored

    def commit(self) -> None:
        """Commits changes and clears transaction shadow snapshots."""
        self._snapshots.clear()

    @property
    def pending_count(self) -> int:
        return len(self._snapshots)


class SecurityGate:
    def __init__(self, policy_path: str = None):
        if policy_path is None:
            policy_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "security_policy.json")
        self.policy = self._load_policy(policy_path)
        self.blacklist = [re.compile(p, re.IGNORECASE) for p in self.policy.get("command_blacklist_patterns", [])]
        self.transaction_mgr = WorkspaceTransactionManager()

    def rollback_workspace(self) -> List[str]:
        """Rolls back all pending workspace file mutations."""
        return self.transaction_mgr.rollback()

    def commit_workspace(self) -> None:
        """Commits all pending workspace mutations."""
        self.transaction_mgr.commit()

    def _load_policy(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load security policy from {path}: {e}. Using defaults.")
            return {
                "risk_tiers": {
                    "read_only": ["read_file", "list_directory", "web_search"],
                    "reversible": ["write_file", "fetch_web_page"],
                    "irreversible": ["execute_command", "edit_file"]
                }
            }

    def get_risk_tier(self, tool_name: str) -> RiskTier:
        tiers = self.policy.get("risk_tiers", {})
        if tool_name in tiers.get("read_only", []):
            return RiskTier.READ_ONLY
        if tool_name in tiers.get("reversible", []):
            return RiskTier.REVERSIBLE
        return RiskTier.IRREVERSIBLE

    def evaluate_tool_call(self, tool_name: str, args: Dict[str, Any], caller_agent: str) -> Tuple[bool, str]:
        tier = self.get_risk_tier(tool_name)
        logger.info(f"[SecurityGate] Evaluating tool [{tool_name}] (Tier: {tier.value}) called by [{caller_agent}]")

        if tier == RiskTier.READ_ONLY:
            return True, f"Permitted read-only operation: {tool_name}"

        if tool_name in ["write_file", "edit_file"]:
            file_path = args.get("file_path", "")
            if not file_path:
                return False, "Blocked: Missing file_path argument."
            normalized = os.path.normpath(file_path)
            # Prevent path traversal
            if ".." in normalized or normalized.startswith("/") or normalized.startswith("\\") or (len(normalized) > 1 and normalized[1] == ":"):
                # Check if it's within current workspace
                cwd = os.getcwd()
                abs_path = os.path.abspath(file_path)
                if not abs_path.startswith(cwd):
                    return False, f"Blocked: Target file path attempts escape from workspace: {file_path}"

            # Record shadow pre-state for self-healing rollback (Pillar 9)
            self.transaction_mgr.record_pre_state(file_path)
            return True, f"Permitted workspace file operation on {file_path}"

        if tool_name == "execute_command":
            command = args.get("command", "")
            if not command:
                return False, "Blocked: Missing command argument."
            
            for pattern in self.blacklist:
                if pattern.search(command):
                    logger.critical(f"[SecurityGate] BLOCKED Blacklisted command pattern: {command}")
                    return False, f"Blocked: Command matches security blacklist pattern: {command}"

            risk_score = laya_engine.evaluate_risk(command) if laya_engine is not None else 0.0
            logger.info(f"[SecurityGate] Laya risk score for '{command}': {risk_score:.2f}")
            if risk_score > 0.70:
                logger.critical(f"[SecurityGate] BLOCKED High-Risk command (score {risk_score}): {command}")
                return False, f"Blocked: Command failed dynamic risk assessment (Risk score {risk_score:.2f} > 0.70)"

            return True, f"Permitted command execution (Risk score: {risk_score:.2f})"

        return True, "Permitted standard operation"

security_gate = SecurityGate()

