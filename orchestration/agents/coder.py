"""
Coder Agent — orchestration/agents/coder.py
============================================
Autonomous coding agent that generates code via Gemini LLM,
invokes filesystem & execution tools, authorizes operations against
SecurityGate, and persists files to disk.
"""
import ast
import json
import logging
import re
import time
import uuid
import asyncio
import httpx
import os
from typing import List, Optional, Dict, Any

from orchestration import InputData, AgentResponse, ResponseStatus
from orchestration.orchestrator.schemas.tool import ToolCall
from orchestration.orchestrator.infra import (
    registry,
    AgentCapabilities,
    key_manager,
    call_gemini,
    security_gate,
)
from orchestration.tools.builtin.file_tools import write_file

logger = logging.getLogger(__name__)

CODER_TOOLS = [
    "read_file",
    "write_file",
    "edit_file",
    "list_directory",
    "execute_command",
    "inspect_browser_dom",
    "stream_code_to_editor",
]


def _extract_filename(text: str) -> Optional[str]:
    """Detects a filename if explicitly requested in the prompt or instruction."""
    match = re.search(r"['\"]?([a-zA-Z0-9_\-\.\/\\]+\.(?:py|json|md|txt|html|css|js|ts|sh|yaml|yml))['\"]?", text, re.IGNORECASE)
    if match:
        return match.group(1).replace("'", "").replace('"', "")
    return None


def _extract_code(content: str) -> Optional[str]:
    """Extracts code blocks from markdown fences if present."""
    code_match = re.search(r"```(?:[a-zA-Z0-9_-]+)?\s*\n(.*?)\n```", content, re.DOTALL)
    if code_match:
        return code_match.group(1).strip()
    return None


@registry.register("coder", AgentCapabilities(description="Handles high-speed parallel code generation.", tools=CODER_TOOLS, agent_level="TASK_DOER"))
def coder_agent(task_data: InputData) -> AgentResponse:
    logger.info("Routing to Coder Agent (Async Sharded Execution)")
    start_time = time.time()
    
    prompt_text = task_data.text_content or ""
    my_key = key_manager.get_groq_api_key()
    
    provided_budget = task_data.metadata.get("budget_left")
    if provided_budget is None:
        end_time = start_time + 40.0
        def get_budget_left():
            return max(0.1, end_time - time.time())
    else:
        def get_budget_left():
            return max(0.1, provided_budget - (time.time() - start_time))
        
    contract_sys_prompt = (
        "You are the Lead Architect. Break down the user's request into specific Python modules.\n"
        "Output one module per line, exactly in this format:\n"
        "path.py | one-line purpose | signature1; signature2; signature3\n"
        "No prose, no JSON, no docstrings. Max 4 modules, ≤150 lines each, no comments."
    )
    
    class ContractTruncated(Exception): pass
    class ContractEmpty(Exception): pass
    
    def parse_contract(text: str):
        mods = []
        for line in text.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 3 and parts[0].endswith(".py"):
                mods.append({"module_name": parts[0], "description": parts[1], "signatures": [s.strip() for s in parts[2].split(";")]})
        if not mods:
            raise ContractEmpty()
        return mods

    modules = []
    # Try contract generation
    for attempt in range(2):
        try:
            contract_res, tool_calls = call_gemini(
                prompt=prompt_text,
                api_key=key_manager.get_api_key_for_role("CODER"),
                system_instruction=contract_sys_prompt,
                agent_id="coder",
                provider="groq",
                return_tool_calls=True
            )
            # Assuming call_gemini returns a dict in metadata if we adapted it, but let's parse text
            text = contract_res or ""
            if tool_calls and not any("|" in line for line in text.splitlines()):
                execution_time = (time.time() - start_time) * 1000
                return AgentResponse(
                    agent_id="coder",
                    status=ResponseStatus.SUCCESS,
                    content=text,
                    tool_calls=tool_calls,
                    execution_time_ms=execution_time,
                    metadata={"processed_modality": task_data.input_type.value}
                )
            modules = parse_contract(text)
            break
        except Exception as e:
            if attempt == 1:
                logger.warning(f"[Coder] Contract phase failed twice: {e}. Falling back to monolithic.")
                modules = [{"module_name": "main.py", "description": prompt_text, "signatures": []}]

    from orchestration.orchestrator.infra.llm import BUCKETS
    
    CHAIN = [
        ("groq", "qwen/qwen3.8-27b"),
        ("groq", "openai/gpt-oss-120b"),
        ("groq", "openai/gpt-oss-20b")
    ]
    GEMINI_TOTAL_P50 = 8.0

    class RateLimited(Exception): pass

    def signature_stub(mod: dict) -> str:
        sigs = "\\n".join(mod.get("signatures", []))
        return f"# Degraded: Generation failed, preserving interface\\n{sigs}\\n    pass"

    import hashlib
    import sys
    PROMPT_CACHE = getattr(sys.modules[__name__], "PROMPT_CACHE", {})
    setattr(sys.modules[__name__], "PROMPT_CACHE", PROMPT_CACHE)

    async def stream_shard(client: httpx.AsyncClient, prov: str, model: str, mod: dict, budget, shard_ts: dict) -> tuple[str, str, float]:
        mod_name = mod.get("module_name", "output.py")
        mod_desc = mod.get("description", "")
        sigs = "\\n".join(mod.get("signatures", []))
        
        prompt = f"Global context: {prompt_text}\nModule: {mod_desc}\nSignatures:\n{sigs}"
        mod_sys = (
            f"Write the complete, production-ready Python code for '{mod_name}'. Output ONLY raw python in a markdown block. "
            "No docstrings, no comments, ≤150 lines. No code that runs at import except definitions. "
            "All modules are flat files in one directory. Import sibling modules as `from sibling import Symbol`, NEVER use relative imports like `from .sibling`."
        )
        
        cache_key = hashlib.md5((mod_sys + prompt).encode()).hexdigest()
        if cache_key in PROMPT_CACHE:
            logger.info(f"[Coder Shard] {mod_name} (CACHE HIT) | Wait: 0ms | TTFT: 0ms | Total: 0ms")
            return PROMPT_CACHE[cache_key], 0.0, 0.0
            
        ttft = None
        output_tokens = 0
        chunks = []
        
        if prov == "groq":
            headers = {"Authorization": f"Bearer {my_key}", "Content-Type": "application/json"}
            payload = {
                "model": model,
                "messages": [{"role": "system", "content": mod_sys}, {"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 1500,
                "stream": True,
                "stream_options": {"include_usage": True}
            }
            if "gpt-oss" in model:
                payload["reasoning_effort"] = "low"
            async with asyncio.timeout(budget()):
                request = client.build_request("POST", "https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                shard_ts["request_sent"] = time.time()
                response = await client.send(request, stream=True)
                BUCKETS[model].update(response.headers)
                
                if response.status_code == 429:
                    rem = response.headers.get('x-ratelimit-remaining-tokens')
                    reset = response.headers.get('x-ratelimit-reset-tokens')
                    logger.debug(f"[Groq Headers] {model} | Remaining: {rem} | Reset: {reset}")
                    raise RateLimited()
                    
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        data = json.loads(line[6:])
                        if "usage" in data and data["usage"]:
                            output_tokens = data["usage"].get("completion_tokens", output_tokens)
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0]["delta"].get("content", "")
                            if delta:
                                if ttft is None:
                                    ttft = (time.time() - shard_ts["request_sent"]) * 1000
                                    shard_ts["first_chunk"] = time.time()
                                chunks.append(delta)
        else:
            shard_ts["request_sent"] = time.time()
            gemini_key = key_manager.get_api_key_for_role("CODER")
            payload = {
                "systemInstruction": {"parts": [{"text": mod_sys}]},
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1500}
            }
            async with asyncio.timeout(budget()):
                request = client.build_request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={gemini_key}", json=payload)
                response = await client.send(request, stream=True)
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        if "usageMetadata" in data:
                            output_tokens = data["usageMetadata"].get("candidatesTokenCount", output_tokens)
                        try:
                            delta = data["candidates"][0]["content"]["parts"][0]["text"]
                            if ttft is None:
                                ttft = (time.time() - shard_ts["request_sent"]) * 1000
                                shard_ts["first_chunk"] = time.time()
                            chunks.append(delta)
                        except (KeyError, IndexError):
                            pass

        full_time = (time.time() - shard_ts["start"]) * 1000
        bucket_wait = (shard_ts.get("request_sent", shard_ts["start"]) - shard_ts["start"]) * 1000
        logger.info(f"[Coder Shard] {mod_name} ({model}) | Wait: {bucket_wait:.1f}ms | TTFT: {ttft or 0:.1f}ms | Tokens: ~{output_tokens} | Total: {full_time:.1f}ms")
        raw_output = "".join(chunks)
        parsed = _extract_code(raw_output) or raw_output
        PROMPT_CACHE[cache_key] = parsed
        return parsed, ttft, full_time

    async def run_shard(client: httpx.AsyncClient, mod: dict) -> tuple[str, str]:
        shard_ts = {"start": time.time()}
        mod_name = mod.get("module_name", "output.py")
        mod_desc = mod.get("description", "")
        
        prompt = f"Global context: {prompt_text}\nModule: {mod_desc}"
        cost = int(len(prompt) / 4) + 1500
        
        for prov, model in CHAIN:
            if prov == "gemini" and get_budget_left() < GEMINI_TOTAL_P50:
                logger.info(f"[Coder Shard] {mod_name} skipping Gemini (not enough budget: {get_budget_left():.1f}s left)")
                break
            if not await BUCKETS[model].acquire(cost, get_budget_left, critical=True):
                continue
            try:
                code, ttft, full_time = await stream_shard(client, prov, model, mod, get_budget_left, shard_ts)
                return mod_name, code
            except RateLimited:
                if prov == "groq": shard_ts["groq_fail"] = time.time()
                continue
            except Exception as e:
                logger.warning(f"[Coder Shard] {mod_name} failed on {model}: {repr(e)}")
                if prov == "groq": shard_ts["groq_fail"] = time.time()
                continue
                
        logger.error(f"[Coder Shard] {mod_name} exhausted all providers.")
        return mod_name, signature_stub(mod)

    async def run_parallel_shards():
        async with httpx.AsyncClient(timeout=20.0) as client:
            tasks = {}
            for m in modules:
                tasks[asyncio.create_task(run_shard(client, m))] = m
                await asyncio.sleep(0.15)
                
            timeout_cap = min(7.5, get_budget_left() - 0.5) # Soft deadline before absolute budget dies
            done, pending = await asyncio.wait(tasks.keys(), timeout=timeout_cap)
            for t in pending: t.cancel()
            
            gen = {}
            for t in done:
                if not t.exception():
                    res = t.result()
                    if isinstance(res, tuple) and len(res) == 2:
                        gen[res[0]] = res[1]
                        
            # Use stubs for pending
            for t in pending:
                mod = tasks[t]
                mod_name = mod.get("module_name", "output.py")
                gen[mod_name] = signature_stub(mod)
                
            return gen, pending

    def review(src, fname):
        try:
            compile(src, fname, "exec")
        except SyntaxError as e:
            return [f"{fname}:{e.lineno}: {e.msg}"]
            
        errs = []
        import io
        try:
            import pyflakes.api, pyflakes.reporter
            out = io.StringIO()
            pyflakes.api.check(src, fname, pyflakes.reporter.Reporter(out, out))
            errs.extend([l for l in out.getvalue().splitlines() if "undefined name" in l])
        except ImportError:
            pass
            
        import ast
        try:
            tree = ast.parse(src)
            # 1. lint_pitfalls
            for n in ast.walk(tree):
                if (isinstance(n, ast.keyword) and n.arg == "default_factory"
                        and isinstance(n.value, (ast.Dict, ast.List, ast.Set, ast.Tuple, ast.Constant))):
                    errs.append(f"{fname}:{n.value.lineno}: default_factory needs a callable (dict, list or a lambda)")
            # 2. lint_import_time
            BLOCKING = {"run", "input", "sleep", "run_until_complete", "serve_forever", "start"}
            for n in tree.body:
                if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test):
                    continue
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
                    continue
                calls = (getattr(c.func, "attr", getattr(c.func, "id", ""))
                         for c in ast.walk(n) if isinstance(c, ast.Call))
                if isinstance(n, (ast.Expr, ast.While, ast.For, ast.With)) or any(c in BLOCKING for c in calls):
                    errs.append(f"{fname}:{n.lineno}: code runs at import ({type(n).__name__})")
        except Exception:
            pass
            
        return errs

    def top_level_names(tree):
        names = set()
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                names |= {t.id for t in targets if isinstance(t, ast.Name)}
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names |= {(a.asname or a.name).split(".")[0] for a in n.names}
        return names

    def lint_cross_module(files: Dict[str, str]) -> Dict[str, List[str]]:
        import collections
        trees = {}
        for f, src in files.items():
            try:
                trees[f] = ast.parse(src)
            except SyntaxError:
                pass
        tops = {f[:-3]: top_level_names(t) for f, t in trees.items() if f.endswith(".py")}
        file_errs = collections.defaultdict(list)
        for f, t in trees.items():
            for n in ast.walk(t):
                if not isinstance(n, ast.ImportFrom):
                    continue
                if n.level:
                    file_errs[f].append(f"{f}:{n.lineno}: relative import; use `from {n.module or 'sibling'} import ...`")
                if n.module in tops:
                    for a in n.names:
                        if a.name != "*" and a.name not in tops[n.module]:
                            file_errs[f].append(f"{f}:{n.lineno}: {n.module} has no top-level '{a.name}'")
        return dict(file_errs)
            
    async def repair_shard(client: httpx.AsyncClient, fname: str, src: str, errs: list, budget) -> str:
        err_text = "\\n".join(errs)
        sys_prompt = f"Fix the following Python code in '{fname}'. Only output the raw repaired code. DO NOT wrap with markdown. The code had these errors:\\n{err_text}"
        cost = int(len(src + sys_prompt) / 4) + 1500
        for prov, model in CHAIN:
            if prov == "gemini" and budget() < GEMINI_TOTAL_P50:
                break
            if not await BUCKETS[model].acquire(cost, budget, critical=True):
                continue
            try:
                if prov == "groq":
                    headers = {"Authorization": f"Bearer {my_key}", "Content-Type": "application/json"}
                    payload = {"model": model, "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": src}], "temperature": 0.3, "max_tokens": 1500}
                    if "gpt-oss" in model:
                        payload["reasoning_effort"] = "low"
                    request = client.build_request("POST", "https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                else:
                    gemini_key = key_manager.get_api_key_for_role("CODER")
                    payload = {"systemInstruction": {"parts": [{"text": sys_prompt}]}, "contents": [{"parts": [{"text": src}]}], "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1500}}
                    request = client.build_request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}", json=payload)
                
                async with asyncio.timeout(budget()):
                    response = await client.send(request)
                    if prov == "groq":
                        BUCKETS[model].update(response.headers)
                        if response.status_code == 429: continue
                        response.raise_for_status()
                        ans = response.json()["choices"][0]["message"]["content"]
                    else:
                        response.raise_for_status()
                        ans = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                return _extract_code(ans) or ans
            except Exception as e:
                logger.warning(f"[Coder Repair] {fname} failed on {model}: {e}")
                continue
        return src

    import uuid
    run_id = task_data.metadata.get("task_id", uuid.uuid4().hex[:8])
    gen_dir = os.path.join("generated", run_id)
    os.makedirs(gen_dir, exist_ok=True)
    
    generated_files, pending = asyncio.run(run_parallel_shards())
            
    bad = {f: errs for f, src in generated_files.items() if (errs := review(src, f))}
    cross_errs = lint_cross_module(generated_files)
    for f, errs in cross_errs.items():
        if any("relative import" in e for e in errs):
            generated_files[f] = re.sub(r"from\s+\.([a-zA-Z0-9_]+)\s+import", r"from \1 import", generated_files[f])

    cross_errs = lint_cross_module(generated_files)
    for f, errs in cross_errs.items():
        bad.setdefault(f, []).extend(errs)

    if bad and get_budget_left() > 3.0:
        logger.info(f"[Coder Repair] Attempting to fix {len(bad)} modules.")
        async def do_repairs():
            async with httpx.AsyncClient(timeout=15.0) as client:
                tasks = [repair_shard(client, f, generated_files[f], errs, get_budget_left) for f, errs in bad.items()]
                return await asyncio.gather(*tasks)
        fixed = asyncio.run(do_repairs())
        generated_files.update(dict(zip(bad, fixed)))
        bad = {f: errs for f in bad if (errs := review(generated_files[f], f))}
        
    for name, code in generated_files.items():
        try:
            write_file(file_path=os.path.join(gen_dir, name), content=code, overwrite=True)
        except Exception:
            pass

    status = "SUCCESS" if not pending else "DEGRADED"

    merged_output = []
    for name, code in generated_files.items():
        merged_output.append(f"### `{name}`\n```python\n{code}\n```")
        
    final_content = "\n\n".join(merged_output)
    execution_time = (time.time() - start_time) * 1000
    
    return AgentResponse(
        agent_id="coder",
        status=ResponseStatus.SUCCESS,
        content=final_content,
        tool_calls=[],
        execution_time_ms=execution_time,
        metadata={"processed_modality": task_data.input_type.value, "modules_generated": len(generated_files)}
    )