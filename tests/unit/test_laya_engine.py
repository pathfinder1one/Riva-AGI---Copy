import pytest
from orchestration.orchestrator.routing.laya_engine import LayaEngine

def test_laya_engine_route_intent():
    engine = LayaEngine(enabled=False) # Test fallback / heuristic
    candidates = ["coder", "researcher", "writer", "devops"]
    
    agent, complexity, conf = engine.route_intent("Write a python sorting function", candidates)
    assert agent == "coder"
    assert conf > 0.0

    agent, complexity, conf = engine.route_intent("What is the history of AI?", candidates)
    assert agent == "researcher"

    agent, complexity, conf = engine.route_intent("completely unknown random gibberish 123", candidates)
    assert agent == "fallback"

def test_laya_engine_risk_scoring():
    engine = LayaEngine(enabled=False)
    
    safe_score = engine.evaluate_risk("ls -la")
    assert safe_score <= 0.20

    dangerous_score = engine.evaluate_risk("rm -rf /")
    assert dangerous_score >= 0.80

def test_laya_engine_domain_verification():
    engine = LayaEngine(enabled=False)
    
    valid_output = "Here is the complete implementation with all requested features and error handling."
    is_valid, score = engine.verify_domain_output(valid_output, "coder", "build feature")
    assert is_valid
    assert score > 0.70

    error_output = "Traceback (most recent call last): failed to run"
    is_valid, score = engine.verify_domain_output(error_output, "coder", "build feature")
    assert not is_valid
