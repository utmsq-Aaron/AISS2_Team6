"""Multi-tool synthesis + planning query tests."""
import sys, json, time, traceback
sys.path.insert(0, '.')

from core.orchestrator import FitDashOrchestrator
from core.viz_telegram import can_render, render_chart_png

QUERIES = [
    ("Recovery",       "How recovered am I today? Should I train hard or rest?"),
    ("Wellness week",  "Give me a wellness overview of the past week — sleep, stress, steps, body battery."),
    ("Weather run",    "Is the weather good for a run this afternoon in Karlsruhe?"),
    ("Yearly stats",   "Show me my all-time Strava statistics and year-over-year progress."),
    ("Best times",     "What are my personal bests for running distances?"),
]

orch = FitDashOrchestrator()
print(f"Tools: {len(orch._discover())}\n")

for label, query in QUERIES:
    print(f"=== [{label}] ===")
    t0 = time.perf_counter()
    try:
        answer, trace = orch.run(query, [], None)
        elapsed = time.perf_counter() - t0

        tcs = trace.get("tool_calls") or []
        print(f"  Tools called ({len(tcs)}):")
        for r in tcs:
            icon = "OK " if not r.get("error") else "ERR"
            print(f"    {icon} {r['tool']} ({r.get('duration_ms',0)}ms)")
            if r.get("error"):
                print(f"       -> {r['error'][:100]}")

        charts = []
        for r in tcs:
            if r.get("error"):
                continue
            bare = r["tool"].split("__", 1)[-1]
            if can_render(bare):
                try:
                    png = render_chart_png(r["tool"], r.get("result","{}"))
                    sz = len(png) // 1024 if png else 0
                    charts.append(f"{bare}:{sz}KB")
                except Exception as e:
                    charts.append(f"{bare}:ERR({e})")
        if charts:
            print(f"  Charts: {', '.join(charts)}")

        print(f"  Answer ({elapsed:.1f}s): {answer[:300]}")
    except Exception as e:
        print(f"  EXCEPTION: {e}")
        traceback.print_exc()
    print()

print("Done.")
