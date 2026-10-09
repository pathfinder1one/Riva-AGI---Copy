"""
Routing Package — orchestration/orchestrator/routing/
=====================================================
Zero-LLM intent detection and routing decision layer.

  laya_engine        — Sub-35ms Non-Autoregressive intent & complexity classifier
  intent_classifier  — High-level intent classification wrapper (registers with registry)
  router             — classify_intent() public function for external consumers
"""
from .laya_engine import laya_engine, LayaEngine
from .router import classify_intent

__all__ = [
    "laya_engine",
    "LayaEngine",
    "classify_intent",
]
