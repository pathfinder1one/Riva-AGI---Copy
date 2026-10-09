"""
Laya Non-Autoregressive Decision Engine — orchestration/orchestrator/laya_engine.py
===================================================================================
Sub-35ms Non-Autoregressive Decision Engine.
Uses Laya to predict typed decisions (Choice, Score, Noul) without token generation.
Supports agent routing, risk scoring, and 10-domain verification assertions.
"""
import logging
from typing import List, Tuple, Dict

logger = logging.getLogger(__name__)

class LayaEngine:
    """
    Sub-35ms Non-Autoregressive Decision Engine.
    Uses Laya to predict typed decisions (Choice, Score, Noul) without token generation.
    Supports agent routing, risk scoring, and 10-domain verification assertions.
    """
    DOMAIN_CRITERIA: Dict[str, str] = {
        "coder": "Code is syntactically valid, complete, and contains no missing placeholder blocks",
        "researcher": "Factual information is thoroughly covered with historical trends and data points",
        "writer": "Content is well-structured, grammatically correct, and matches the requested tone",
        "devops": "Configuration or shell commands are safe, standard, and structurally correct",
        "qa_tester": "Test cases adequately cover edge cases and verify assertions cleanly",
        "data_analyst": "Calculations and metric summaries are numerically consistent and accurate",
        "seo_specialist": "Target search intent, keywords, and meta tags are properly optimized",
        "reasoner": "Logical steps are coherent without internal contradictions",
        "security_auditor": "Security assessment identifies risks and adheres to safe architecture",
        "designer": "UI structure, component hierarchy, and design specifications are complete",
    }

    def __init__(self, model_name: str = "laya-base-english", enabled: bool = None):
        if enabled is None:
            try:
                from orchestration.orchestrator.infra.llm import load_models_config
                cfg = load_models_config().get("routing_engine", {})
                self.enabled = cfg.get("enabled", False)
                self.model_name = cfg.get("model_name", model_name)
            except Exception:
                self.enabled = False
                self.model_name = model_name
        else:
            self.enabled = enabled
            self.model_name = model_name
        self._model = None
        self._initialized = False

    def _lazy_init(self):
        if self._initialized:
            return
        self._initialized = True
        if not self.enabled:
            logger.info("[LayaEngine] Laya disabled via config. Using rule-based fallback.")
            return

        try:
            import laya
            self._model = laya.load(self.model_name)
            logger.info(f"[LayaEngine] Loaded Laya model: {self.model_name}")
        except ImportError:
            logger.warning("[LayaEngine] 'laya' package not installed. Operating in fallback mode.")
        except Exception as e:
            logger.warning(f"[LayaEngine] Failed to load Laya model ({e}). Operating in fallback mode.")

    def route_intent(self, text: str, candidates: List[str]) -> Tuple[str, str, float]:
        self._lazy_init()
        clean_text = (text or "").strip()
        if not clean_text:
            return "fallback", "simple", 0.0

        if self._model and hasattr(self._model, "choice"):
            try:
                agent_decision = self._model.choice(clean_text, options=candidates)
                target_agent = str(agent_decision.label)
                confidence = float(getattr(agent_decision, "confidence", 0.95))

                complexity_decision = self._model.choice(
                    clean_text, 
                    options=["simple: single agent can answer directly", "complex: requires multi-step planning and file generation"]
                )
                complexity = "complex" if "complex" in str(complexity_decision.label).lower() else "simple"
                return target_agent, complexity, confidence
            except Exception as e:
                logger.error(f"[LayaEngine] Choice execution error: {e}. Falling back to rules.")

        from orchestration.orchestrator.routing.router import classify_intent
        rule_res = classify_intent(clean_text)
        agent = rule_res.get("agent", "fallback")
        confidence = float(rule_res.get("confidence", 0.0))
        if agent == "fallback":
            return "fallback", "simple", confidence
        if agent not in candidates and candidates:
            agent = "coder" if "coder" in candidates else candidates[0]
        # Multi-domain workstream indicators for complexity (Pillar 2)
        complex_keywords = [
            "pipeline", "develop system", "multi-step", "full stack", "architecture",
            "and test", "and verify", "and document", "and write", "end to end",
            "end-to-end", "step by step", "unit tests", "documentation and", "verify it"
        ]
        has_complex_keyword = any(w in clean_text.lower() for w in complex_keywords)

        # Check if query spans multiple technical domains
        multi_domain = False
        try:
            from orchestration.orchestrator.planning.planner import detect_workstreams
            multi_domain = len(detect_workstreams(clean_text)) > 1
        except Exception:
            pass

        complexity = "complex" if (has_complex_keyword or multi_domain) else "simple"
        return agent, complexity, confidence

    def evaluate_risk(self, command: str) -> float:
        self._lazy_init()
        if self._model and hasattr(self._model, "score"):
            try:
                score = self._model.score(command, criterion="destructive, irreversible, or hazardous system impact")
                return float(score.value)
            except Exception as e:
                logger.error(f"[LayaEngine] Score execution error: {e}")

        dangerous_keywords = ["rm", "del", "format", "shutdown", "mkfs", "chmod", "drop", "> /dev", "dd if="]
        if any(kw in command.lower() for kw in dangerous_keywords):
            return 0.95
        return 0.10

    def verify_domain_output(self, output: str, agent_name: str, goal: str) -> Tuple[bool, float]:
        """
        Uses Laya Noul to evaluate domain satisfaction in < 35ms.
        Returns: (is_satisfactory: bool, confidence: float)
        """
        self._lazy_init()
        criterion = self.DOMAIN_CRITERIA.get(agent_name, "Output satisfies the user request comprehensively")
        statement = f"The output satisfies the goal '{goal}': {criterion}"

        if self._model and hasattr(self._model, "noul"):
            try:
                res = self._model.noul(context=output, statement=statement)
                prob = float(res.probability)
                return prob >= 0.75, prob
            except Exception as e:
                logger.error(f"[LayaEngine] Noul verification error: {e}")

        # Deterministic heuristic fallback
        clean_output = (output or "").strip()
        error_indicators = ["traceback (most recent", "exception:", "syntaxerror:", "runtimeerror:", "failed to execute", "cannot find module"]
        refusal_phrases = [
            "do not have access to real-time", "don't have access to real-time",
            "cannot browse the internet", "as an ai language model", "as a language model",
            "my knowledge cutoff", "unable to perform real-time", "i do not possess real-time"
        ]
        has_error = any(err in clean_output.lower() for err in error_indicators)
        has_refusal = any(rp in clean_output.lower() for rp in refusal_phrases)
        is_valid = len(clean_output) > 20 and not has_error and not has_refusal
        return is_valid, (0.85 if is_valid else 0.20)

laya_engine = LayaEngine()
