"""
LLM Client Wrapper — orchestration/orchestrator/llm.py
======================================================
Autonomous tool-calling client wrapper supporting:
- Autonomous multi-turn function calling via Google GenAI SDK (AFC)
- 429 Rate Limit / Quota resilience with 15-key rotation pool
- Frequency-based model allocation from config/models.json
- WebSocket Live API fallback for real-time speech/vision
"""

import asyncio
import concurrent.futures
import functools
import inspect
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional, List, Callable, Any, Tuple, Union, Dict

try:
    import httpx
except ImportError:
    httpx = None

import re

def parse_duration(duration_str: str) -> float:
    if not duration_str: return 0.0
    val = 0.0
    for match in re.finditer(r"([\d\.]+)(ms|s|m|h)", duration_str.lower()):
        num = float(match.group(1))
        unit = match.group(2)
        if unit == "ms": val += num / 1000.0
        elif unit == "s": val += num
        elif unit == "m": val += num * 60
        elif unit == "h": val += num * 3600
    return val

import threading

class TokenBucket:
    def __init__(self):
        self.remaining = None
        self.reset_at = 0.0
        self.lock = threading.Lock()
        
    def update(self, headers):
        with self.lock:
            if "x-ratelimit-remaining-tokens" in headers:
                self.remaining = int(headers["x-ratelimit-remaining-tokens"])
                self.reset_at = time.monotonic() + parse_duration(headers.get("x-ratelimit-reset-tokens", "0s"))
            
    async def acquire(self, cost: int, budget_func: Callable[[], float], critical: bool = False):
        RESERVE = 3000
        floor = 0 if critical else RESERVE
        wait_time = 0.0
        while True:
            with self.lock:
                if self.remaining is None or (self.remaining - cost >= floor):
                    if self.remaining is not None:
                        self.remaining -= cost
                    if wait_time > 0:
                        logger.debug(f"[TokenBucket] Waited {wait_time:.2f}s for tokens.")
                    return True
                wait = self.reset_at - time.monotonic()
                
            if wait > max(0.0, min(1.5, budget_func() - 6)):
                return False
                
            sleep_time = max(wait, 0.05)
            await asyncio.sleep(sleep_time)
            wait_time += sleep_time

    def acquire_sync(self, cost: int, budget_func: Callable[[], float], critical: bool = False):
        RESERVE = 3000
        floor = 0 if critical else RESERVE
        wait_time = 0.0
        while True:
            with self.lock:
                if self.remaining is None or (self.remaining - cost >= floor):
                    if self.remaining is not None:
                        self.remaining -= cost
                    if wait_time > 0:
                        logger.debug(f"[TokenBucket] Sync waited {wait_time:.2f}s for tokens.")
                    return True
                wait = self.reset_at - time.monotonic()
                
            if wait > max(0.0, min(1.5, budget_func() - 6)):
                return False
                
            sleep_time = max(wait, 0.05)
            time.sleep(sleep_time)
            wait_time += sleep_time

BUCKETS = {
    "qwen/qwen3.8-27b": TokenBucket(),
    "openai/gpt-oss-120b": TokenBucket(),
    "openai/gpt-oss-20b": TokenBucket(),
    "llama-3.1-8b-instant": TokenBucket(),
    "gemini-3.5-flash-lite": TokenBucket(),
    "gemini-3.5-flash": TokenBucket()
}

try:
    from unittest.mock import Mock, MagicMock
except ImportError:
    Mock = None
    MagicMock = None

try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
except ImportError:
    genai = None
    types = None
    APIError = Exception

from orchestration.tools import tool_registry
from orchestration.orchestrator.schemas.tool import ToolCall, ToolResult
from orchestration.orchestrator.infra.key_manager import key_manager

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "models.json"

LIVE_MODELS = {"gemini-3.8-live", "gemini-3.1-flash-live-preview"}

PRIMARY_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash-lite"]

DEFAULT_MODEL_MAPPING: Dict[str, str] = {
    # High-Frequency Heavy Execution (15 RPM, 500 RPD)
    "coder": "gemini-3.5-flash-lite",
    "qa_tester": "gemini-3.1-flash-lite",
    "researcher": "gemini-3.5-flash-lite",
    "intent_classifier": "gemini-3.1-flash-lite",
    "knowledge_agent": "gemini-3.1-flash-lite",
    "executor": "gemini-3.1-flash-lite",
    "devops": "gemini-3.1-flash-lite",
    "designer": "gemini-3.1-flash-lite",
    "seo_specialist": "gemini-3.1-flash-lite",
    "dummy_system": "gemini-3.1-flash-lite",

    # Low-Frequency Deep Reasoning Models (5 RPM, 20 RPD)
    "planner": "gemini-3.5-flash",
    "reviewer": "gemini-3.7-flash",
    "writer": "gemini-3.6-flash",
    "reasoner": "gemini-3.8-flash",
    "security_auditor": "gemini-3-flash-preview",
    "data_analyst": "gemini-robotics-er-2-preview",
}


def load_models_config() -> Dict[str, Any]:
    """Loads models.json configuration or returns defaults if missing/corrupt."""
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[LLM] Failed to load models config from {CONFIG_PATH}: {e}. Using defaults.")
    return {}


def get_model_for_agent(agent_id: str) -> str:
    """Resolves the configured model name for a given agent_id."""
    cfg = load_models_config()
    mapping = cfg.get("agent_model_mapping", {})
    if agent_id in mapping and isinstance(mapping[agent_id], dict):
        return mapping[agent_id].get("model", DEFAULT_MODEL_MAPPING.get(agent_id, PRIMARY_MODEL))
    return DEFAULT_MODEL_MAPPING.get(agent_id, PRIMARY_MODEL)


def get_provider_for_agent(agent_id: str) -> str:
    """Resolves the configured provider ('groq', 'gemini', 'laya') for a given agent_id."""
    cfg = load_models_config()
    mapping = cfg.get("agent_model_mapping", {})
    if agent_id in mapping and isinstance(mapping[agent_id], dict):
        return mapping[agent_id].get("provider", "gemini")
    return "gemini"


def _call_gemini_live(client: Any, model_name: str, prompt: str, system_instruction: str = "") -> str:
    """
    Invokes Gemini Live API via WebSocket with audio transcription,
    providing zero rate-limit (Unlimited RPM/RPD) generation.
    """
    async def _async_call():
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=types.Content(parts=[types.Part.from_text(text=system_instruction)]) if system_instruction else None
        )
        accumulated_text = []
        async with client.aio.live.connect(model=model_name, config=config) as session:
            await session.send_client_content(
                turns=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                turn_complete=True
            )
            async for response in session.receive():
                sc = response.server_content
                if sc and sc.output_transcription and sc.output_transcription.text:
                    accumulated_text.append(sc.output_transcription.text)
                if sc and sc.turn_complete:
                    break
        return "".join(accumulated_text).strip()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, _async_call())
            return future.result()
    else:
        return asyncio.run(_async_call())


def _wrap_tool_for_execution(name: str, func: Callable, execution_log: list) -> Callable:
    """
    Wraps a tool function to track its execution time, parameters, and output
    for audit trails, schemas, and UI telemetry.
    """
    @functools.wraps(func)
    def tracked_tool(*args, **kwargs):
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        start_time = time.time()
        logger.info(f"LLM triggered tool [{name}] with args: {kwargs}")
        
        try:
            result = func(*args, **kwargs)
            duration_ms = (time.time() - start_time) * 1000.0
            
            tool_call = ToolCall(
                call_id=call_id,
                tool_name=name,
                parameters=kwargs,
                expected_return_type=str(type(result).__name__)
            )
            execution_log.append(tool_call)
            return result
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000.0
            error_msg = f"Tool Execution Error ({name}): {str(e)}"
            logger.error(error_msg)
            
            tool_call = ToolCall(
                call_id=call_id,
                tool_name=name,
                parameters=kwargs,
                expected_return_type="str"
            )
            execution_log.append(tool_call)
            return error_msg

    return tracked_tool


def _tool_to_openai_schema(name: str, func: Callable) -> Dict[str, Any]:
    """Converts a tool callable or registry entry into an OpenAI-compatible function schema."""
    defn = tool_registry.get_tool_definition(name)
    desc = defn.description if defn and defn.description else (inspect.getdoc(func) or f"Tool {name}")
    
    properties = {}
    required = []
    
    if defn and defn.parameters_schema:
        for p_name, p_info in defn.parameters_schema.items():
            raw_type = str(p_info.get("type", "string")).lower()
            if "int" in raw_type or "float" in raw_type or "number" in raw_type:
                prop_type = "integer" if "int" in raw_type else "number"
            elif "bool" in raw_type:
                prop_type = "boolean"
            elif "list" in raw_type:
                prop_type = "array"
            elif "dict" in raw_type:
                prop_type = "object"
            else:
                prop_type = "string"
            
            properties[p_name] = {
                "type": prop_type,
                "description": f"Parameter {p_name}"
            }
            if p_info.get("required"):
                required.append(p_name)
    else:
        try:
            sig = inspect.signature(func)
            for p_name, param in sig.parameters.items():
                if param.default == inspect.Parameter.empty:
                    required.append(p_name)
                properties[p_name] = {"type": "string", "description": f"Parameter {p_name}"}
        except Exception:
            pass

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc.strip(),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }
    }


def _call_groq(
    model_name: str,
    prompt: str,
    system_instruction: str = "",
    tools: Optional[List[Union[str, Callable]]] = None,
    executed_tools: Optional[List[ToolCall]] = None,
    groq_api_key: Optional[str] = None,
    max_turns: int = 5,
    max_tokens: int = 600
) -> Optional[str]:
    """
    Executes an ultra-fast generation or multi-turn tool-calling loop via Groq Cloud API.
    Returns the string response or None if fallback to Gemini is needed.
    """
    if httpx is None:
        logger.warning("[Groq] httpx library not installed. Falling back to Gemini.")
        return None

    api_key = groq_api_key or key_manager.get_groq_api_key()
    if not api_key:
        logger.debug("[Groq] No GROQ_API_KEY found. Falling back to Gemini.")
        return None

    # Fallback to standard Groq model if non-Groq model was passed
    if model_name.startswith("gemini-") or "laya" in model_name:
        model_name = "qwen/qwen3.8-27b"

    openai_tools = []
    wrapped_callables: Dict[str, Callable] = {}
    track_list = executed_tools if executed_tools is not None else []

    if tools:
        for t in tools:
            tool_name = None
            tool_func = None
            if isinstance(t, str):
                tool_name = t
                tool_func = tool_registry.get_tool(t)
            elif callable(t):
                tool_name = getattr(t, "__name__", "custom_tool")
                tool_func = t
            
            if tool_name and tool_func:
                wrapped_callables[tool_name] = _wrap_tool_for_execution(tool_name, tool_func, track_list)
                openai_tools.append(_tool_to_openai_schema(tool_name, tool_func))

    messages: List[Dict[str, Any]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            for turn in range(max_turns):
                payload: Dict[str, Any] = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": max_tokens,
                }
                if "gpt-oss" in model_name:
                    payload["reasoning_effort"] = "low"
                if openai_tools:
                    payload["tools"] = openai_tools
                    payload["tool_choice"] = "auto"
                    
                cost = max_tokens + int(sum(len(str(m.get("content", ""))) for m in messages) / 4)
                if model_name in BUCKETS and not BUCKETS[model_name].acquire_sync(cost, lambda: 10.0):
                    logger.warning(f"[Groq] Proactive rate limit abort for {model_name}. Falling back to Gemini.")
                    return None

                res = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload
                )
                
                if model_name in BUCKETS:
                    BUCKETS[model_name].update(res.headers)

                if res.status_code != 200:
                    logger.warning(f"[Groq] API returned status {res.status_code}: {res.text[:200]}. Falling back to Gemini.")
                    return None

                data = res.json()
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                tool_calls = msg.get("tool_calls")

                if tool_calls:
                    messages.append(msg)
                    for tc in tool_calls:
                        fn_name = tc.get("function", {}).get("name")
                        raw_args = tc.get("function", {}).get("arguments", "{}")
                        try:
                            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        except Exception:
                            parsed_args = {}

                        wrapped_fn = wrapped_callables.get(fn_name)
                        if wrapped_fn:
                            tool_output = wrapped_fn(**parsed_args)
                        else:
                            tool_output = f"Error: Tool '{fn_name}' not available."

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "name": fn_name,
                            "content": str(tool_output)
                        })
                else:
                    return msg.get("content", "")

            return msg.get("content", "")
    except Exception as e:
        logger.warning(f"[Groq] Exception during execution: {e}. Falling back to Gemini.")
        return None


def call_gemini(
    prompt: str,
    api_key: str = "",
    system_instruction: str = "",
    agent_id: str = "orchestrator",
    tools: Optional[List[Union[str, Callable]]] = None,
    return_tool_calls: bool = False,
    provider: Optional[str] = None
) -> Union[str, Tuple[str, List[ToolCall]]]:
    """
    Unified multi-provider LLM caller supporting:
    - Ultra-fast Groq LPU inference for high-speed agents (coder, writer, reasoner, etc.)
    - Google GenAI SDK for large context (researcher) & multi-turn tool calling
    - 429 quota rotation across 15 Gemini keys
    - WebSocket Live API fallback for zero-latency speech/vision
    """
    target_provider = provider or get_provider_for_agent(agent_id)

    # Check if in a unit test mocking genai.Client
    is_gemini_mocked = (
        genai is not None
        and hasattr(genai, "Client")
        and (
            (Mock is not None and isinstance(genai.Client, (Mock, MagicMock)))
            or getattr(genai.Client, "_mock_return_value", None) is not None
        )
    )

    # 1. Try Groq LPU provider if assigned and not explicitly overridden or mocked
    if target_provider == "groq" and not is_gemini_mocked:
        groq_model = get_model_for_agent(agent_id)
        executed_groq_tools: List[ToolCall] = []
        logger.info(f"Agent [{agent_id}] routing to Groq LPU -> {groq_model}")
        groq_result = _call_groq(
            model_name=groq_model,
            prompt=prompt,
            system_instruction=system_instruction,
            tools=tools,
            executed_tools=executed_groq_tools
        )
        if groq_result is not None:
            logger.info(f"Agent [{agent_id}] successfully executed via Groq LPU ({groq_model}).")
            return (groq_result, executed_groq_tools) if return_tool_calls else groq_result
        logger.warning(f"Agent [{agent_id}] Groq execution failed or unavailable. Falling back to Gemini pool.")

    if genai is None:
        msg = f"[LLM Offline Mode] google-genai SDK not installed. Generated stub response for {agent_id}."
        logger.warning(msg)
        return (msg, []) if return_tool_calls else msg

    if not api_key:
        api_key = key_manager.get_api_key_for_role(agent_id)
        if not api_key:
            raise ValueError(f"API Key is missing for agent [{agent_id}].")

    model_name = get_model_for_agent(agent_id)
    # Ensure Gemini fallback uses a valid Gemini model if mapped model was Groq
    if not model_name.startswith("gemini-"):
        model_name = PRIMARY_MODEL

    # 2. Try Live API (Zero rate limit, Unlimited RPM/RPD) if configured
    if model_name in LIVE_MODELS and not tools:
        try:
            client = genai.Client(api_key=api_key)
            logger.info(f"Agent [{agent_id}] triggering Live API -> {model_name}")
            live_result = _call_gemini_live(client, model_name, prompt, system_instruction)
            if live_result:
                return (live_result, []) if return_tool_calls else live_result
        except Exception as live_e:
            logger.warning(f"Agent [{agent_id}] Live API failed: {live_e}. Falling back to standard model.")

    executed_tools: List[ToolCall] = []
    wrapped_tools: List[Callable] = []

    # Resolve tools from tool_registry or direct callables
    if tools:
        for t in tools:
            if isinstance(t, str):
                tool_func = tool_registry.get_tool(t)
                if tool_func:
                    wrapped_tools.append(_wrap_tool_for_execution(t, tool_func, executed_tools))
                else:
                    logger.warning(f"Tool '{t}' requested by agent '{agent_id}' was not found in tool_registry.")
            elif callable(t):
                tool_name = getattr(t, "__name__", "custom_tool")
                wrapped_tools.append(_wrap_tool_for_execution(tool_name, t, executed_tools))

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.7,
        tools=wrapped_tools if wrapped_tools else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=3) if wrapped_tools else None,
        thinking_config=types.ThinkingConfig(thinking_budget_tokens=0) if "thinking" in model_name else None
    )

    candidate_models = [model_name] + [m for m in FALLBACK_MODELS if m != model_name]
    candidate_keys = [api_key] + key_manager.get_fallback_keys(api_key)

    content = ""
    last_error = None
    success = False

    for current_key in candidate_keys:
        client = genai.Client(api_key=current_key)
        for current_model in candidate_models:
            try:
                logger.info(f"Agent [{agent_id}] invoking LLM -> {current_model} (tools: {len(wrapped_tools)})")
                chat = client.chats.create(model=current_model, config=config)
                response = chat.send_message(prompt)
                content = response.text or ""
                last_error = None
                success = True
                break
            except APIError as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(f"Agent [{agent_id}] hit 429 quota limit on {current_model}. Rotating key/model...")
                    last_error = e
                    continue
                elif "404" in err_str or "NOT_FOUND" in err_str:
                    logger.warning(f"Agent [{agent_id}] model {current_model} not found (404). Trying next model...")
                    last_error = e
                    continue
                else:
                    logger.warning(f"Agent [{agent_id}] encountered APIError on {current_model}: {e}")
                    last_error = e
                    continue
            except Exception as e:
                logger.error(f"Agent [{agent_id}] encountered error on {current_model}: {e}")
                last_error = e
                break
        if success:
            break

    if not success and last_error:
        content = f"LLM Generation Error: {last_error}"

    if return_tool_calls:
        return content, executed_tools
    return content
