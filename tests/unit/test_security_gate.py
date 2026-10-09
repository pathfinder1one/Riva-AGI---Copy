import pytest
from orchestration.orchestrator.infra.security import SecurityGate, RiskTier

def test_security_gate_risk_tiers():
    gate = SecurityGate()
    assert gate.get_risk_tier("read_file") == RiskTier.READ_ONLY
    assert gate.get_risk_tier("list_directory") == RiskTier.READ_ONLY
    assert gate.get_risk_tier("write_file") == RiskTier.REVERSIBLE
    assert gate.get_risk_tier("execute_command") == RiskTier.IRREVERSIBLE

def test_security_gate_file_operations():
    gate = SecurityGate()
    
    # Valid relative file
    allowed, msg = gate.evaluate_tool_call("write_file", {"file_path": "output.py", "content": "print(1)"}, "coder")
    assert allowed

    # Empty path blocked
    allowed, msg = gate.evaluate_tool_call("write_file", {"file_path": "", "content": "test"}, "coder")
    assert not allowed
    assert "Missing file_path" in msg

def test_security_gate_command_blacklist():
    gate = SecurityGate()

    # Blacklisted destructive command
    allowed, msg = gate.evaluate_tool_call("execute_command", {"command": "rm -rf /"}, "devops")
    assert not allowed
    assert "blacklist" in msg.lower()

    # Safe command
    allowed, msg = gate.evaluate_tool_call("execute_command", {"command": "echo 'Hello'"}, "devops")
    assert allowed
