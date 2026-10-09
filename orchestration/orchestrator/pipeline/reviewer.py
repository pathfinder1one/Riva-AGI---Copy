"""
Reviewer Agent — orchestration/orchestrator/reviewer.py
========================================================
Agent-Aware Quality Gate leveraging Laya non-autoregressive verification (<35ms)
across 10 Domain Profiles with fallback critique generation.
"""
import json
import logging
import time
from orchestration.orchestrator.infra.registry import registry, AgentCapabilities
from orchestration import InputData, AgentResponse, ResponseStatus
from orchestration.orchestrator.infra.key_manager import key_manager
from orchestration.orchestrator.infra.llm import call_gemini
from orchestration.orchestrator.routing.laya_engine import laya_engine

logger = logging.getLogger(__name__)

def clean_json_text(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()

import os
import ast

def check_python_syntax(text: str) -> tuple[bool, str]:
    """Extract filenames from the payload and run compile() and pyflakes."""
    lines = text.split('\n')
    
    files_to_check = []
    for line in lines:
        if line.startswith("### `") and line.endswith("`"):
            fname = line.replace("### `", "").replace("`", "").strip()
            if fname.endswith(".py") and os.path.exists(fname):
                files_to_check.append(fname)
                
    if not files_to_check:
        return True, ""
        
    all_errors = []
    import io
    try:
        from pyflakes.api import check
        from pyflakes.reporter import Reporter
        has_pyflakes = True
    except ImportError:
        has_pyflakes = False

    for fname in files_to_check:
        try:
            with open(fname, "r", encoding="utf-8") as f:
                src = f.read()
            compile(src, fname, "exec")
            
            if has_pyflakes:
                out = io.StringIO()
                check(src, fname, Reporter(out, out))
                errors = [l for l in out.getvalue().splitlines() if "undefined name" in l]
                if errors:
                    all_errors.extend(errors)
        except SyntaxError as e:
            all_errors.append(f"{fname}:{e.lineno}: {e.msg}")
        except Exception as e:
            all_errors.append(f"Check failed for {fname}: {e}")
            
    if all_errors:
        return False, "; ".join(all_errors[:3])  # Return first 3 errors to prevent huge payloads
    return True, ""

@registry.register("reviewer", AgentCapabilities(description="Reviews generated content and code using domain verification profiles.", tools=["review"], agent_level="MANAGER"))
def reviewer_agent(task_data: InputData) -> AgentResponse:
    logger.info("Routing to Reviewer Agent")
    start_time = time.time()
    
    text = task_data.text_content or ""
    metadata = task_data.metadata or {}
    target_agent = metadata.get("target_agent", "coder")
    goal = metadata.get("goal", text)

    # 1. Fast-Path Domain Verification with Laya Noul (<35ms)
    is_valid, score = laya_engine.verify_domain_output(output=text, agent_name=target_agent, goal=goal)
    logger.info(f"[Reviewer] Domain check for [{target_agent}]: valid={is_valid}, score={score:.2f}")

    # 1.5. Code-aware instant Syntax check (for coder)
    if target_agent == "coder":
        syntax_ok, syntax_err = check_python_syntax(text)
        if not syntax_ok:
            logger.warning(f"[Reviewer] Instant Syntax Check Failed: {syntax_err}")
            is_valid = False
            score = 0.0
            res_data = {
                "status": "rejected",
                "feedback": f"Python code contains syntax errors: {syntax_err}. Please fix it."
            }
            content = json.dumps(res_data, indent=2)

    if is_valid:
        res_data = {
            "status": "approved",
            "feedback": f"Output satisfies domain verification criteria for [{target_agent}] (confidence: {score:.2f})."
        }
        content = json.dumps(res_data, indent=2)
        logger.info("[Reviewer] Verdict: APPROVED (No errors found)")
    elif target_agent != "coder" or score > 0.0:
        # If domain verification failed, use LLM or generate targeted critique
        my_key = key_manager.get_api_key_for_role("REVIEWER")
        sys_prompt = f"""You are the Reviewer Manager for Riva-AGI.
Evaluate if the worker output for [{target_agent}] fulfills the goal: '{goal}'.
Output ONLY valid JSON:
{{
  "status": "approved" | "rejected",
  "feedback": "constructive feedback"
}}"""
        try:
            llm_res = call_gemini(
                prompt=f"Goal: {goal}\nOutput to review:\n{text}",
                api_key=my_key,
                system_instruction=sys_prompt,
                agent_id="reviewer"
            )
            parsed = json.loads(clean_json_text(llm_res))
            content = json.dumps(parsed, indent=2)
            logger.info(f"[Reviewer] Verdict: {parsed.get('status', 'unknown').upper()} (LLM)")
        except Exception as e:
            logger.warning(f"Reviewer LLM evaluation fallback: {e}")
            res_data = {
                "status": "rejected",
                "feedback": f"Output for [{target_agent}] requires revision to meet domain quality standards."
            }
            content = json.dumps(res_data, indent=2)
            logger.info("[Reviewer] Verdict: REJECTED (Fallback)")
    else:
        logger.info("[Reviewer] Verdict: REJECTED (Syntax Errors)")

    execution_time = (time.time() - start_time) * 1000
    
    return AgentResponse(
        agent_id="reviewer",
        status=ResponseStatus.SUCCESS,
        content=content,
        tool_calls=[],
        execution_time_ms=execution_time,
        metadata={"processed_modality": task_data.input_type.value}
    )
