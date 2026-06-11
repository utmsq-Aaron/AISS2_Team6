"""Final verification: test key queries end-to-end with real LLM + MCP servers."""
import sys, json, time, traceback
sys.path.insert(0, '.')

from core.orchestrator import FitDashOrchestrator
from core.viz_telegram import can_render, render_chart_png

QUERIES = [
    ("Activity list",    "Show me my last 5 activities with pace and distance."),
    ("Sleep",            "How did I sleep last night?"),
    ("Performance",      "How has my running pace improved over the past 2 months?"),
    ("Training load",    "What is my current training load? Am I overtraining?"),
    ("Activity detail",  "Tell me about the details of my most recent run — lap splits and HR zones."),
]

orch = FitDashOrchestrator()
tools = orch._discover()
print(f"Discovered {len(tools)} tools. Starting queries...\n")

for label, query in QUERIES:
    print(f"=== [{label}] ===")
    print(f"Query: {query}")
    t0 = time.perf_counter()
    try:
        answer, trace = orch.run(query, [], None)
        elapsed = time.perf_counter() - t0

        tool_calls = trace.get("tool_calls") or []
        for r in tool_calls:
            icon = "OK " if not r.get("error") else "ERR"
            print(f"  {icon} {r['tool']} ({r.get('duration_ms', 0)}ms)")
            if r.get("error"):
                print(f"    -> {r['error'][:100]}")

        for r in tool_calls:
            if r.get("error"):
                continue
            bare = r["tool"].split("__", 1)[-1]
            if can_render(bare):
                try:
                    png = render_chart_png(r["tool"], r.get("result", "{}"))
                    sz = len(png) // 1024 if png else 0
                    print(f"  CHART {bare}: {sz} KB")
                except Exception as e:
                    print(f"  CHART ERR {bare}: {e}")

        print(f"Answer ({elapsed:.1f}s): {answer[:250]}")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"EXCEPTION after {elapsed:.1f}s: {e}")
        traceback.print_exc()
    print()

print("Test complete.")
