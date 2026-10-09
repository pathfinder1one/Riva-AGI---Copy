"""
Fast-Lane Benchmark — scripts/benchmark_fast_lane.py
=====================================================
Measures pass rate and latency for:
  1. Back-to-Back Fast-Lane Runs (zero delay between runs)
  2. Spaced Fast-Lane Runs (bucket refill simulated or spaced)
"""
import os
import sys
import time
import statistics

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from orchestration.orchestrator.main import run_orchestrator, load_all_agents

load_all_agents()

query = "Write a python function to compute factorial of n"

print("=" * 70)
print("             FAST-LANE BENCHMARK: BACK-TO-BACK RUNS (NO DELAY)")
print("=" * 70)
b2b_times = []
b2b_statuses = []

for i in range(5):
    t0 = time.perf_counter()
    try:
        res = run_orchestrator(query)
        elapsed = (time.perf_counter() - t0) * 1000
        content = res.get("response_payload").content if res.get("response_payload") else ""
        passed = res.get("agent") == "coder" and len(content) > 10 and elapsed < 10000
        status = "PASS" if passed else "FAIL"
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        status = f"ERROR ({e})"
    b2b_times.append(elapsed)
    b2b_statuses.append(status)
    print(f"  Run {i+1}: {status:<6} in {elapsed:7.1f} ms | Output: {len(content)} chars")

print("-" * 70)
passed_cnt = b2b_statuses.count("PASS")
print(f"  Back-to-Back Pass Rate : {passed_cnt}/{len(b2b_statuses)} ({passed_cnt/len(b2b_statuses)*100:.0f}%)")
print(f"  Back-to-Back p50        : {statistics.median(b2b_times):.1f} ms")
print(f"  Back-to-Back p95        : {sorted(b2b_times)[int(0.95*len(b2b_times))]:.1f} ms")
print(f"  Back-to-Back Max        : {max(b2b_times):.1f} ms")
print("=" * 70)


print("\n" + "=" * 70)
print("             FAST-LANE BENCHMARK: SPACED RUNS (SIMULATED REFILL)")
print("=" * 70)
# To test 10 runs without waiting 300 seconds of real-time sleep,
# we reset the token bucket timestamps between runs to simulate true 30s spacing.
from orchestration.orchestrator.infra.llm import BUCKETS

spaced_times = []
spaced_statuses = []

for i in range(10):
    # Reset token buckets to simulate full bucket refill
    for b in BUCKETS.values():
        b.remaining = 8000
        b.reset_at = time.monotonic()

    t0 = time.perf_counter()
    try:
        res = run_orchestrator(query)
        elapsed = (time.perf_counter() - t0) * 1000
        content = res.get("response_payload").content if res.get("response_payload") else ""
        passed = res.get("agent") == "coder" and len(content) > 10 and elapsed < 10000
        status = "PASS" if passed else "FAIL"
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        status = f"ERROR ({e})"
    spaced_times.append(elapsed)
    spaced_statuses.append(status)
    print(f"  Run {i+1:02d}: {status:<6} in {elapsed:7.1f} ms | Output: {len(content)} chars")

print("-" * 70)
sp_passed_cnt = spaced_statuses.count("PASS")
print(f"  Spaced Pass Rate        : {sp_passed_cnt}/{len(spaced_statuses)} ({sp_passed_cnt/len(spaced_statuses)*100:.0f}%)")
print(f"  Spaced p50              : {statistics.median(spaced_times):.1f} ms")
print(f"  Spaced p95              : {sorted(spaced_times)[int(0.95*len(spaced_times))]:.1f} ms")
print(f"  Spaced Min / Max        : {min(spaced_times):.1f} ms / {max(spaced_times):.1f} ms")
print("=" * 70)
