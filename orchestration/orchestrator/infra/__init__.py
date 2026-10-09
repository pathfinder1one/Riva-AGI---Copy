"""
Infra Package — orchestration/orchestrator/infra/
=================================================
Shared infrastructure components used across the entire orchestration system.

  key_manager  — API key rotation & role-based selection (Gemini, Groq, etc.)
  llm          — Unified LLM adapter (Gemini Flash + Groq unified client)
  registry     — Agent capability registry (register, lookup, list agents)
  security     — SecurityGate tool authorization & workspace rollback
"""
from .key_manager import key_manager, KeyManager
from .registry import registry, AgentCapabilities
from .security import security_gate, SecurityGate, RiskTier
from .llm import (
    call_gemini,
    get_model_for_agent,
    get_provider_for_agent,
    load_models_config,
)

__all__ = [
    "key_manager",
    "KeyManager",
    "registry",
    "AgentCapabilities",
    "security_gate",
    "SecurityGate",
    "RiskTier",
    "call_gemini",
    "get_model_for_agent",
    "get_provider_for_agent",
    "load_models_config",
]
