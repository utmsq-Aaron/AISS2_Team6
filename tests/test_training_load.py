"""Test: training load query with Garmin fallback."""
import sys, json, time, traceback
sys.path.insert(0, '.')

from core.orchestrator import FitDashOrchestrator
from core.viz_telegram import can_render, render_chart_png

orch = FitDashOrchestrator()
print(f"Discovered {len(orch._discover())} tools\n")

query = "What is my current training load? Am I overtraining?"
print(f"Query: {query}")
t0 = time.perf_counter()
try:
    answer, trace = orch.run(query, [], None)
    elapsed = time.perf_counter() - t0

    tool_calls = trace.get("tool_calls") or []
    print(f"\nTools called ({len(tool_calls)}):")
    for r in tool_calls:
        icon = "OK " if not r.get("error") else "ERR"
        print(f"  {icon} {r['tool']} ({r.get('duration_ms', 0)}ms)")
        if r.get("error"):
            print(f"    -> {r['error'][:120]}")

    print(f"\nCharts:")
    for r in tool_calls:
        if r.get("error"):
            continue
        bare = r["tool"].split("__", 1)[-1]
        if can_render(bare):
            try:
                png = render_chart_png(r["tool"], r.get("result", "{}"))
                sz = len(png) // 1024 if png else 0
                print(f"  {bare}: {sz} KB")
            except Exception as e:
                print(f"  ERR {bare}: {e}")

    print(f"\nAnswer ({elapsed:.1f}s):\n{answer}")
except Exception as e:
    print(f"EXCEPTION: {e}")
    traceback.print_exc()
