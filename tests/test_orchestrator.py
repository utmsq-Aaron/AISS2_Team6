"""End-to-end orchestrator test — runs 10 realistic queries and reports results.

Usage:
    conda run -n aiss2026 python test_orchestrator.py
"""

import json
import sys
import time

sys.path.insert(0, ".")

from core.orchestrator import FitDashOrchestrator
from core.viz_telegram import can_render, render_chart_png

_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"


TEST_QUERIES = [
    ("Sleep last night",       "How did I sleep last night? Show me the sleep stages."),
    ("Steps today",            "How many steps did I take today and yesterday? Show me the intraday step pattern."),
    ("Stress today",           "What was my stress level like today? When was I most stressed?"),
    ("Body Battery week",      "Show me my Body Battery for the last 7 days."),
    ("HRV status",             "What's my HRV status today? Am I well recovered?"),
    ("Recent runs",            "Show me my last 20 runs with pace and distance."),
    ("Wellness trends 14d",    "Show me my wellness trends for the past 14 days — sleep, steps, stress."),
    ("Run HR map",             "Show me my last run on a map with heart rate overlay."),
    ("Training load",          "What's my training load right now? Am I overtraining?"),
    ("Performance trends",     "How has my running pace improved over the past 3 months?"),
    ("Delete confirm test",    "Delete my last Strava activity."),
]


def _sep(label: str = "") -> None:
    print(f"\n{'-' * 70}")
    if label:
        print(f"{_BOLD}{label}{_RESET}")
        print("-" * 70)


def _run_query(orch: FitDashOrchestrator, label: str, query: str) -> None:
    _sep(f"[{label}]  {query}")
    t0 = time.perf_counter()
    answer, trace = orch.run(query, [], None)
    elapsed = time.perf_counter() - t0

    tool_calls = trace.get("tool_calls") or []
    errors = [r for r in tool_calls if r.get("error")]

    # ── Tool calls ──────────────────────────────────────────────────────────
    if tool_calls:
        print(f"\n{_BOLD}Tools called ({len(tool_calls)}):{_RESET}")
        for r in tool_calls:
            dur   = r.get("duration_ms", 0)
            err   = r.get("error")
            icon  = _RED + "✗" if err else _GREEN + "✓"
            print(f"  {icon}{_RESET} {r['tool']:<45} {_DIM}{dur:>5} ms{_RESET}")
            if err:
                print(f"    {_RED}↳ {err[:100]}{_RESET}")
    else:
        print(f"\n{_YELLOW}⚠  No tools called (direct answer){_RESET}")

    # ── Visualizations ────────────────────────────────────────────────────
    print(f"\n{_BOLD}Visualizations:{_RESET}")
    rendered = 0
    for r in tool_calls:
        bare = r["tool"].split("__", 1)[-1] if "__" in r["tool"] else r["tool"]
        if can_render(bare):
            if r.get("error"):
                print(f"  {_RED}✗{_RESET} {bare} — skipped (tool error)")
            else:
                png = render_chart_png(r["tool"], r.get("result", "{}"))
                if png:
                    size_kb = len(png) // 1024
                    print(f"  {_GREEN}✓{_RESET} {bare} → {size_kb} KB PNG")
                    rendered += 1
                else:
                    print(f"  {_YELLOW}⚠{_RESET} {bare} — can_render=True but render returned None")
        else:
            print(f"  {_DIM}–{_RESET} {bare} — no renderer registered")

    if not tool_calls:
        print(f"  {_DIM}(no tools, no charts){_RESET}")

    # ── Answer ─────────────────────────────────────────────────────────────
    print(f"\n{_BOLD}Answer ({elapsed:.1f}s):{_RESET}")
    print(answer[:400])
    if len(answer) > 400:
        print(f"  {_DIM}… ({len(answer)} chars total){_RESET}")

    # ── Route data ──────────────────────────────────────────────────────────
    rd = trace.get("route_data")
    if rd:
        tool = rd.get("tool", "?")
        data = rd.get("data") or {}
        pts  = data.get("points") or []
        print(f"\n{_GREEN}[MAP] Route data: tool={tool}, {len(pts)} GPS points{_RESET}")


def main() -> None:
    print(f"{_BOLD}FitDash Orchestrator — End-to-End Test{_RESET}")
    print(f"Starting orchestrator (discovers tools from running MCP servers)…")
    orch = FitDashOrchestrator()
    tools = orch._discover()
    tool_names = [t["function"]["name"] for t in tools]

    _sep("Discovered Tools")
    for n in sorted(tool_names):
        has_viz = _GREEN + "📊" if can_render(n.split("__", 1)[-1]) else _DIM + "  "
        print(f"  {has_viz}{_RESET} {n}")

    print(f"\nTotal: {len(tools)} tools from MCP servers")
    print(f"Total with visualizers: {sum(1 for n in tool_names if can_render(n.split('__', 1)[-1]))}")

    _sep("Running Queries")
    for label, query in TEST_QUERIES:
        _run_query(orch, label, query)

    _sep("Done")


if __name__ == "__main__":
    main()
