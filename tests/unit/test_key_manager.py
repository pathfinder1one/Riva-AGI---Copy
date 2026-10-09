import os
import pytest
from orchestration.orchestrator.infra.key_manager import KeyManager

def test_key_manager_exact_role(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY_CODER", "coder_secret_123")
    km = KeyManager()
    assert km.get_api_key_for_role("CODER") == "coder_secret_123"

def test_key_manager_alias_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY_SEO_SPECIALIST", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_WORKER_10", "worker_10_secret")
    km = KeyManager()
    assert km.get_api_key_for_role("SEO_SPECIALIST") == "worker_10_secret"

def test_key_manager_orchestrator_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY_RESEARCHER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_ORCHESTRATOR", "universal_orch_key")
    km = KeyManager()
    assert km.get_api_key_for_role("RESEARCHER") == "universal_orch_key"
