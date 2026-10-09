"""
API Key Manager — orchestration/orchestrator/config.py
======================================================
Manages Gemini API keys with 3-tier hierarchical resolution:
  1. Exact Named Role (GEMINI_API_KEY_SEO_SPECIALIST, GEMINI_API_KEY_CODER)
  2. Numbered Worker Fallback (GEMINI_API_KEY_WORKER_10)
  3. Universal Orchestrator Fallback (GEMINI_API_KEY_ORCHESTRATOR / GEMINI_API_KEY)

Resolves Issue #4 (seo_specialist and dummy_system_agent WORKER_10 collision).
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Literal, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "models.json"

AgentLevel = Literal["CEO", "MANAGER", "TASK_DOER"]


def _load_role_aliases() -> Dict[str, List[str]]:
    """Loads dynamic role key alias mapping from models.json configuration or returns configurable defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                aliases = cfg.get("role_key_aliases")
                if isinstance(aliases, dict):
                    return aliases
        except Exception as e:
            logger.debug(f"Failed to load role_key_aliases from {CONFIG_PATH}: {e}")

    # Standard configurable defaults
    return {
        "SEO_SPECIALIST": ["GEMINI_API_KEY_SEO_SPECIALIST", "GEMINI_API_KEY_WORKER_10"],
        "DUMMY_SYSTEM": ["GEMINI_API_KEY_DUMMY_SYSTEM"],
        "DESIGNER": ["GEMINI_API_KEY_DESIGNER", "GEMINI_API_KEY_WORKER_5"],
        "QA_TESTER": ["GEMINI_API_KEY_QA_TESTER", "GEMINI_API_KEY_WORKER_6"],
        "DATA_ANALYST": ["GEMINI_API_KEY_DATA_ANALYST", "GEMINI_API_KEY_WORKER_7"],
        "DEVOPS": ["GEMINI_API_KEY_DEVOPS", "GEMINI_API_KEY_WORKER_8"],
        "SECURITY_AUDITOR": ["GEMINI_API_KEY_SECURITY_AUDITOR", "GEMINI_API_KEY_WORKER_9"],
        "CODER": ["GEMINI_API_KEY_CODER"],
        "RESEARCHER": ["GEMINI_API_KEY_RESEARCHER"],
        "WRITER": ["GEMINI_API_KEY_WRITER"],
        "REASONER": ["GEMINI_API_KEY_REASONER"],
        "INTENT": ["GEMINI_API_KEY_INTENT"],
        "PLANNER": ["GEMINI_API_KEY_PLANNER"],
        "EXECUTOR": ["GEMINI_API_KEY_EXECUTOR"],
        "REVIEWER": ["GEMINI_API_KEY_REVIEWER"],
        "ORCHESTRATOR": ["GEMINI_API_KEY_ORCHESTRATOR"],
    }


class KeyManager:
    """
    Manages the Gemini API keys assigned to the Agentic Company hierarchy.
    Dynamically discovers keys from configuration (models.json) and environment,
    with 3-tier fallback to resolve key collisions.
    """

    def __init__(self):
        self._custom_aliases: Optional[Dict[str, List[str]]] = None

    @property
    def ROLE_ALIASES(self) -> Dict[str, List[str]]:
        return self._custom_aliases if self._custom_aliases is not None else _load_role_aliases()

    @ROLE_ALIASES.setter
    def ROLE_ALIASES(self, value: Dict[str, List[str]]):
        self._custom_aliases = value

    def get_api_key_for_role(self, role: str) -> str:
        """
        Retrieves the API key for a given role using 3-tier fallback:
        1. Try each candidate env var in ROLE_ALIASES for this role.
        2. Try the generic GEMINI_API_KEY_{ROLE} pattern.
        3. Fall back to the universal GEMINI_API_KEY_ORCHESTRATOR or GEMINI_API_KEY.
        """
        clean_role = role.upper().strip()

        # Tier 1 & 2: Check configured aliases for this role
        candidates = self.ROLE_ALIASES.get(
            clean_role, [f"GEMINI_API_KEY_{clean_role}"]
        )
        for env_var in candidates:
            val = os.getenv(env_var, "").strip()
            if val:
                return val

        # Tier 3: Universal fallback
        return (
            os.getenv("GEMINI_API_KEY_ORCHESTRATOR", "")
            or os.getenv("GEMINI_API_KEY", "")
        )

    def get_all_available_keys(self) -> List[str]:
        """Returns all non-empty Gemini API keys configured in the environment."""
        keys = []
        for k, v in os.environ.items():
            if k.startswith("GEMINI_API_KEY") and v.strip() and v.strip() not in keys:
                keys.append(v.strip())
        return keys

    def get_groq_api_key(self) -> str:
        """Retrieves the Groq API key from environment."""
        return os.getenv("GROQ_API_KEY", "").strip()

    def get_fallback_keys(self, current_key: str) -> List[str]:
        """Returns other available keys excluding the current one for quota rotation."""
        return [k for k in self.get_all_available_keys() if k != current_key]


# Singleton instance used by the Orchestrator and Agent Factory
key_manager = KeyManager()
