"""
10-Agent Stress Test & Latency Profiler — scripts/benchmark_10_agent_stress_test.py
===================================================================================
Executes an end-to-end multi-agent orchestration task engaging all 10 specialized agents:
  1. researcher
  2. reasoner
  3. data_analyst
  4. designer
  5. coder
  6. security_auditor
  7. qa_tester
  8. devops
  9. seo_specialist
  10. writer

Profiles exact latency per node, Whiteboard artifact sharing, and DAG scheduling waves.
"""
import os
import sys
import time
import json
from typing import Dict, Any, List

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from orchestration.orchestrator.main import (
    load_all_agents,
    create_orchestrator,
    task_manager,
    build_repo_map
)
from orchestration.orchestrator.infra.registry import registry
from orchestration.orchestrator.infra.key_manager import key_manager
from orchestration.orchestrator.pipeline.whiteboard import WhiteboardContext
from orchestration.orchestrator.pipeline.state import Budget
from orchestration.orchestrator.schemas.task_spec import TaskSpec, TaskStatus as SpecTaskStatus
from orchestration.orchestrator.planning.dag_scheduler import DAGScheduler
from orchestration import InputData, AgentResponse, ResponseStatus, InputType


def run_10_agent_benchmark():
    print("=" * 80)
    print("      RIVA-AGI 10-AGENT MULTI-ORCHESTRATION LATENCY STRESS TEST")
    print("=" * 80)
    load_all_agents()

    all_registered = list(registry.get_all_capabilities().keys())
    print(f"[*] Registered Agents ({len(all_registered)}): {all_registered}")

    ten_agents = [
        "researcher",
        "reasoner",
        "data_analyst",
        "designer",
        "coder",
        "security_auditor",
        "qa_tester",
        "devops",
        "seo_specialist",
        "writer"
    ]

    for a in ten_agents:
        if a not in all_registered and not (a == "qa_tester" and "qa" in all_registered):
            print(f"[!] Warning: Agent '{a}' is not in registry!")

    task_goal = (
        "Architect, implement, secure, test, deploy, optimize, and document "
        "an enterprise-grade real-time audio telemetry and analytics microservice."
    )
    print(f"\n[*] Global Goal: {task_goal}\n")

    # Define a clean 10-task DAG plan covering all 10 agents with multi-layer dependencies
    dag_plan_specs = [
        # Wave 1: Independent discovery & foundational analysis
        TaskSpec(
            task_id="t01_research",
            agent="researcher",
            subtask="Analyze low-latency WebSocket audio streaming standards (WebRTC vs Opus vs PCM) and state-of-the-art architectures.",
            depends_on=[]
        ),
        TaskSpec(
            task_id="t02_reason",
            agent="reasoner",
            subtask="Formulate algorithmic trade-offs for ring-buffer memory limits, thread pooling, and CPU vs memory bottlenecks.",
            depends_on=[]
        ),
        TaskSpec(
            task_id="t03_data",
            agent="data_analyst",
            subtask="Design real-time telemetry metrics schema (p50/p95/p99 latency, jitter, frame drops, throughput).",
            depends_on=[]
        ),
        TaskSpec(
            task_id="t04_design",
            agent="designer",
            subtask="Design UI layout and JSON component tree for the real-time audio analytics dashboard.",
            depends_on=[]
        ),
        # Wave 2: Implementation & Code-dependent analysis (depends on Wave 1)
        TaskSpec(
            task_id="t05_code",
            agent="coder",
            subtask="Write Python FastAPI WebSocket endpoint with asynchronous ring buffer and telemetry event emitter.",
            depends_on=["t01_research", "t02_reason"]
        ),
        TaskSpec(
            task_id="t06_security",
            agent="security_auditor",
            subtask="Audit the WebSocket endpoint for OWASP Top 10 vulnerabilities, DoS via buffer overflow, and auth validation.",
            depends_on=["t05_code"]
        ),
        TaskSpec(
            task_id="t07_qa",
            agent="qa_tester",
            subtask="Write pytest test suite with asyncio fixtures covering normal streaming, packet drop, and disconnect scenarios.",
            depends_on=["t05_code"]
        ),
        # Wave 3: Operations, SEO, and Documentation
        TaskSpec(
            task_id="t08_devops",
            agent="devops",
            subtask="Generate production Dockerfile (multi-stage build) and Kubernetes deployment with readiness/liveness probes.",
            depends_on=["t06_security", "t07_qa"]
        ),
        TaskSpec(
            task_id="t09_seo",
            agent="seo_specialist",
            subtask="Formulate SEO strategy, OpenGraph tags, schema.org Microdata, and performance crawlability specs for the portal.",
            depends_on=["t04_design"]
        ),
        TaskSpec(
            task_id="t10_writer",
            agent="writer",
            subtask="Synthesize complete technical documentation, API specifications, and executive briefing for production handoff.",
            depends_on=["t08_devops", "t09_seo"]
        )
    ]

    # Calculate topological waves
    waves = DAGScheduler.compute_execution_waves(dag_plan_specs)
    print("=" * 80)
    print("                     TOPOLOGICAL EXECUTION WAVES")
    print("=" * 80)
    for idx, wave in enumerate(waves):
        wave_str = ", ".join(f"[{t.task_id}: {t.agent}]" for t in wave)
        print(f"  Wave {idx + 1} ({len(wave)} tasks in parallel): {wave_str}")
    print("=" * 80)

    # Initialize Whiteboard & State
    wb = WhiteboardContext()
    repo_map = build_repo_map(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    wb.publish(key="REPO_MAP", content=repo_map, author="system", task_id="system_boot", artifact_type="text")

    task_id = "stress-test-10-agent"
    task_manager.start_task(task_id, {"goal": task_goal})

    app = create_orchestrator()

    initial_state = {
        "task_payload": InputData(input_type=InputType.TEXT, text_content=task_goal, metadata={"source": "benchmark"}),
        "agent": "fallback",
        "response_payload": None,
        "task_id": task_id,
        "session_id": "session_benchmark",
        "source": "benchmark",
        "complexity": "complex",
        "routing_decision": "executor",
        "plan": [t.model_dump() for t in dag_plan_specs],
        "current_step": 0,
        "current_task_id": None,
        "completed_steps": [],
        "feedback": "",
        "retry_count": 0,
        "max_retries": 3,
        "intent": "full_orchestration",
        "confidence": 1.0,
        "whiteboard": wb,
        "start_time": time.time(),
        # Grant realistic 180s budget to allow profiling all 10 agents
        "budget": Budget(total=180.0),
    }

    print("\n[*] Starting DAG Pipeline Execution...")
    overall_start = time.perf_counter()

    # Step through nodes using LangGraph or direct node invocation to collect granular timings
    timings = {}
    tokens_est = {}

    for t_spec in dag_plan_specs:
        agent_name = t_spec.agent
        print(f"\n--> Running [{t_spec.task_id}] with Agent: [{agent_name}]...")
        t_start = time.perf_counter()

        # Upstream whiteboard context injection
        upstream_ctx = wb.format_context_for_prompt(t_spec.depends_on) if t_spec.depends_on else ""
        prompt_with_ctx = t_spec.subtask
        if upstream_ctx:
            prompt_with_ctx += f"\n\n[UPSTREAM ARTIFACTS FROM WHITEBOARD]:\n{upstream_ctx[:2000]}"

        handler = registry.get_agent(agent_name)
        if not handler and agent_name == "qa_tester":
            handler = registry.get_agent("qa")
        if not handler and agent_name == "qa":
            handler = registry.get_agent("qa_tester")

        worker_input = InputData(
            input_type=InputType.TEXT,
            text_content=prompt_with_ctx,
            metadata={"task_id": t_spec.task_id, "source": "benchmark", "budget_left": 180.0}
        )

        resp = handler(worker_input)
        duration_ms = (time.perf_counter() - t_start) * 1000
        timings[agent_name] = duration_ms

        content_str = resp.content if isinstance(resp.content, str) else str(resp.content)
        tokens_est[agent_name] = len(content_str.split())

        # Publish to Whiteboard
        art_type = "code" if agent_name in ("coder", "devops") else ("json_schema" if agent_name == "designer" else "text")
        wb.publish(
            key=f"{t_spec.task_id}_{agent_name}_output",
            content=content_str,
            author=agent_name,
            task_id=t_spec.task_id,
            artifact_type=art_type
        )

        initial_state["completed_steps"].append({
            "task_id": t_spec.task_id,
            "agent": agent_name,
            "result": content_str[:400] + ("..." if len(content_str) > 400 else "")
        })

        print(f"    [COMPLETED] in {duration_ms:8.2f} ms | Words Generated: {tokens_est[agent_name]} | Status: {resp.status.value}")

    overall_ms = (time.perf_counter() - overall_start) * 1000

    # Reviewer Quality Gate Timing
    print("\n--> Running Quality Gate: [reviewer]...")
    t_rev_start = time.perf_counter()
    rev_handler = registry.get_agent("reviewer")
    rev_resp = rev_handler(InputData(
        input_type=InputType.TEXT,
        text_content=initial_state["completed_steps"][-1]["result"],
        metadata={"target_agent": "writer", "goal": task_goal}
    ))
    rev_ms = (time.perf_counter() - t_rev_start) * 1000
    timings["reviewer"] = rev_ms
    print(f"    [COMPLETED] in {rev_ms:8.2f} ms | Status: {rev_resp.status.value}")

    # Aggregator Node Timing
    print("\n--> Running Synthesizer: [aggregator]...")
    from orchestration.orchestrator.pipeline.aggregator import format_executive_deliverable
    t_agg_start = time.perf_counter()
    deliverable = format_executive_deliverable(
        user_goal=task_goal,
        completed_steps=initial_state["completed_steps"],
        whiteboard=wb,
        total_latency_ms=overall_ms
    )
    agg_ms = (time.perf_counter() - t_agg_start) * 1000
    timings["aggregator"] = agg_ms
    print(f"    [COMPLETED] in {agg_ms:8.2f} ms | Deliverable Length: {len(deliverable)} chars")

    # Latency Profile & Analysis Report
    print("\n" + "=" * 80)
    print("                   10-AGENT LATENCY PROFILE & BOTTLENECK AUDIT")
    print("=" * 80)
    print(f" {'AGENT / NODE':<20} | {'LATENCY (ms)':<15} | {'LATENCY (sec)':<15} | {'SHARE OF TOTAL':<15}")
    print("-" * 80)

    total_sum_ms = sum(timings.values())
    sorted_timings = sorted(timings.items(), key=lambda x: x[1], reverse=True)

    for agent, lat in sorted_timings:
        share = (lat / total_sum_ms) * 100 if total_sum_ms > 0 else 0
        flag = " [CRITICAL BOTTLENECK]" if share > 25.0 else (" [HIGH]" if share > 15.0 else "")
        print(f" {agent:<20} | {lat:12.2f} ms | {lat/1000:12.2f} s | {share:12.1f}%{flag}")

    print("-" * 80)
    print(f" {'TOTAL SERIAL TIME':<20} | {total_sum_ms:12.2f} ms | {total_sum_ms/1000:12.2f} s | 100.0%")
    print("=" * 80)

    # Theoretical Parallel Execution Calculation
    wave_durations = []
    for idx, wave in enumerate(waves):
        wave_agents = [t.agent for t in wave]
        wave_max = max(timings.get(a, 0.0) for a in wave_agents)
        wave_durations.append(wave_max)

    parallel_estimate_ms = sum(wave_durations) + timings.get("reviewer", 0.0) + timings.get("aggregator", 0.0)
    parallel_savings_pct = ((total_sum_ms - parallel_estimate_ms) / total_sum_ms) * 100

    print("\n" + "=" * 80)
    print("                   PARALLEL DAG SPEEDUP POTENTIAL")
    print("=" * 80)
    for idx, (wave, w_dur) in enumerate(zip(waves, wave_durations)):
        agents_str = ", ".join(t.agent for t in wave)
        print(f"  Wave {idx+1} Bottleneck : {w_dur/1000:6.2f} s  (Agents: {agents_str})")
    print(f"  Sequential Duration   : {total_sum_ms/1000:6.2f} s")
    print(f"  Parallel Wave Duration: {parallel_estimate_ms/1000:6.2f} s")
    print(f"  Projected Latency Cut : {parallel_savings_pct:.1f}% SPEEDUP! 🚀")
    print("=" * 80)

    # Whiteboard Verification
    print(f"\n[*] Whiteboard Total Artifacts Stored: {len(wb._artifacts)}")
    for k, v in wb._artifacts.items():
        if k != "REPO_MAP":
            print(f"    - Key: {k:<35} | Type: {v.artifact_type:<10} | Author: {v.author}")

    print("\n[+] Benchmark finished successfully.")


if __name__ == "__main__":
    run_10_agent_benchmark()
