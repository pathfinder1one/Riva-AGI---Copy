import json
import pytest
from unittest.mock import MagicMock, patch

from orchestration.orchestrator.infra.llm import (
    get_provider_for_agent,
    get_model_for_agent,
    _tool_to_openai_schema,
    _call_groq,
    call_gemini
)
from orchestration.orchestrator.schemas.tool import ToolCall
from orchestration.tools import tool_registry


def test_get_provider_for_agent_resolution():
    assert get_provider_for_agent("coder") == "groq"
    assert get_provider_for_agent("writer") == "groq"
    assert get_provider_for_agent("planner") == "groq"
    assert get_provider_for_agent("researcher") == "gemini"
    assert get_provider_for_agent("knowledge_agent") == "gemini"
    assert get_provider_for_agent("intent_classifier") == "laya"
    assert get_provider_for_agent("unknown_agent_xyz") == "gemini"


def test_tool_to_openai_schema_conversion():
    schema = _tool_to_openai_schema("write_file", tool_registry.get_tool("write_file"))
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "write_file"
    assert "parameters" in fn
    props = fn["parameters"]["properties"]
    assert "file_path" in props
    assert "content" in props
    assert "overwrite" in props
    assert props["file_path"]["type"] == "string"
    assert props["overwrite"]["type"] == "boolean"
    assert "file_path" in fn["parameters"]["required"]
    assert "content" in fn["parameters"]["required"]


def test_call_groq_success_mock():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Groq LPU successfully generated response."
                }
            }
        ]
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = _call_groq(
            model_name="qwen/qwen3.8-27b",
            prompt="Write a fibonacci function",
            system_instruction="You are a coder.",
            groq_api_key="mock_groq_key"
        )

        assert result == "Groq LPU successfully generated response."
        assert mock_client.post.called
        call_args = mock_client.post.call_args[1]
        assert call_args["json"]["model"] == "qwen/qwen3.8-27b"
        assert call_args["json"]["max_tokens"] == 600


def test_call_groq_tool_execution_mock():
    # First response triggers tool call, second response returns final summary
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "mock_tool",
                                "arguments": json.dumps({"arg1": "value1"})
                            }
                        }
                    ]
                }
            }
        ]
    }

    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Tool executed and task completed."
                }
            }
        ]
    }

    executed_tools = []
    mock_tool_fn = MagicMock(return_value="mock_tool_output")
    mock_tool_fn.__name__ = "mock_tool"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.side_effect = [resp1, resp2]
        mock_client_cls.return_value = mock_client

        result = _call_groq(
            model_name="qwen/qwen3.8-27b",
            prompt="Run mock tool",
            tools=[mock_tool_fn],
            executed_tools=executed_tools,
            groq_api_key="mock_groq_key"
        )

        assert result == "Tool executed and task completed."
        assert mock_tool_fn.called
        assert len(executed_tools) == 1
        assert executed_tools[0].tool_name == "mock_tool"


def test_call_groq_rate_limit_fallback():
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Rate limit reached for requests per minute"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        # Returns None on failure so caller falls back to Gemini
        result = _call_groq(
            model_name="qwen/qwen3.8-27b",
            prompt="Hello",
            groq_api_key="mock_groq_key"
        )
        assert result is None


def test_call_gemini_routes_to_groq():
    with patch("orchestration.orchestrator.infra.llm._call_groq") as mock_groq:
        mock_groq.return_value = "Generated by Groq"

        result = call_gemini(
            prompt="Hello",
            agent_id="coder",
            provider="groq"
        )
        assert result == "Generated by Groq"
        assert mock_groq.called
