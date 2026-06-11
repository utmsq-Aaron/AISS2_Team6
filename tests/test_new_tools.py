"""Test newly-added renderers: weather, gear, personal bests, activity stats."""
import sys, json, time, traceback
sys.path.insert(0, '.')

from core.orchestrator import FitDashOrchestrator
from core.viz_telegram import can_render, render_chart_png

QUERIES = [
    ("Weather",         "Is the weather good for a long run this week in Karlsruhe?"),
    ("Gear",            "How many km are on my running shoes?"),
    ("Personal Bests",  "What are my personal bests? Longest run and fastest pace?"),
    ("All-time stats",  "Show me my all-time Strava statistics and sport breakdown."),
    ("Yearly progress", "How has my training volume changed year over year?"),
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
        print(f"  Tools ({len(tcs)}):")
        for r in tcs:
            icon = "OK " if not r.get("error") else "ERR"
            print(f"    {icon} {r['tool']} ({r.get('duration_ms', 0)}ms)")
            if r.get("error"):
                print(f"       -> {r['error'][:120]}")

        charts = []
        for r in tcs:
            if r.get("error"):
                continue
            bare = r["tool"].split("__", 1)[-1]
            if can_render(bare):
                try:
                    png = render_chart_png(r["tool"], r.get("result", "{}"))
                    charts.append(f"{bare}:{len(png) // 1024 if png else 0}KB")
                except Exception as e:
                    charts.append(f"{bare}:ERR({e})")
        if charts:
            print(f"  Charts: {', '.join(charts)}")
        if trace.get("route_data"):
            print(f"  Route map: {trace['route_data']['tool']}")

        print(f"  Answer ({elapsed:.1f}s): {answer[:350]}")
    except Exception as e:
        print(f"  EXCEPTION: {e}")
        traceback.print_exc()
    print()

print("Done.")
