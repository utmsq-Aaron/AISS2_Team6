"""Mini orchestrator test — remaining queries from full test."""
import sys, json, time, traceback
sys.path.insert(0, '.')

from core.orchestrator import FitDashOrchestrator
from core.viz_telegram import can_render, render_chart_png

QUERIES = [
    ("HRV status",      "What's my HRV status today? Am I well recovered?"),
    ("Recent runs",     "Show me my last 15 runs with pace and distance."),
    ("Training load",   "What's my training load right now? Am I overtraining?"),
    ("Performance",     "How has my running pace improved over the past 3 months?"),
    ("Delete confirm",  "Delete my last Strava activity."),
]

orch = FitDashOrchestrator()
print("Tools:", len(orch._discover()))

for label, query in QUERIES:
    print(f"\n=== [{label}] ===")
    t0 = time.perf_counter()
    try:
        answer, trace = orch.run(query, [], None)
        elapsed = time.perf_counter() - t0

        tool_calls = trace.get("tool_calls") or []
        for r in tool_calls:
            icon = "OK" if not r.get("error") else "ERR"
            print(f"  {icon} {r['tool']} ({r.get('duration_ms', 0)}ms)")
            if r.get("error"):
                print(f"    -> {r['error'][:80]}")

        for r in tool_calls:
            if r.get("error"): continue
            bare = r["tool"].split("__", 1)[-1]
            if can_render(bare):
                try:
                    png = render_chart_png(r["tool"], r.get("result", "{}"))
                    print(f"  CHART {bare}: {len(png)//1024 if png else 0} KB")
                except Exception as e:
                    print(f"  CHART ERR {bare}: {e}")

        print(f"Answer ({elapsed:.1f}s): {answer[:200]}")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"EXCEPTION after {elapsed:.1f}s: {e}")
        traceback.print_exc()

print("\nDone.")
