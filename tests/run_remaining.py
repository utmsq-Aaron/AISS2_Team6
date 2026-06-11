"""Run only queries 20-25 from the viz quality test (the ones that didn't complete).

Writes results to tests/logs/remaining_<timestamp>.json and a summary to stdout.
"""

import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

LOG_DIR = ROOT / "tests" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
TS = datetime.now().strftime("%Y%m%d_%H%M")
OUT = LOG_DIR / f"remaining_{TS}.json"

QUERIES = [
    {
        "id": "explore_trails_viz",
        "q":  "Find me 3 hiking trails within 15 km of Karlsruhe",
        "expect_tools": ["explore_trails"],
        "expect_map":   True,
        "expect_viz_metric": None,
    },
    {
        "id": "elevation_gain_hint",
        "q":  "Which of my recent hikes had the most elevation gain? Rank them.",
        "expect_tools": ["get_activities"],
        "expect_map":   False,
        "expect_viz_metric": "elevation_gain_m",
    },
    {
        "id": "pace_metric_hint",
        "q":  "Show me my last 10 runs ranked by pace (fastest first)",
        "expect_tools": ["get_activities"],
        "expect_map":   False,
        "expect_viz_metric": "pace_min_per_km",
    },
    {
        "id": "hr_metric_hint",
        "q":  "Which runs had my highest average heart rate?",
        "expect_tools": ["get_activities"],
        "expect_map":   False,
        "expect_viz_metric": "avg_heart_rate",
    },
    {
        "id": "suffer_score_hint",
        "q":  "Which activities had the highest suffer score this year?",
        "expect_tools": ["get_activities"],
        "expect_map":   False,
        "expect_viz_metric": "suffer_score",
    },
    {
        "id": "recovery_multi_chart",
        "q":  "Full recovery check: HRV, body battery, and last night's sleep - "
               "am I ready for a hard workout?",
        "expect_tools": ["get_garmin_hrv_status", "get_garmin_body_battery", "get_garmin_sleep"],
        "expect_map":   False,
        "expect_viz_metric": None,
    },
]


def render_png(tool_name: str, result_json: str, user_query: str) -> dict:
    try:
        from core.viz_telegram import can_render, render_chart_png
    except ImportError:
        return {"can_render": False, "png_bytes": 0, "error": "import failed"}
    bare = tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name
    if not can_render(bare):
        return {"can_render": False, "png_bytes": 0}
    t0 = time.time()
    try:
        png = render_chart_png(tool_name, result_json, user_query)
        return {"can_render": True, "png_bytes": len(png) if png else 0,
                "render_ms": int((time.time()-t0)*1000), "error": None if png else "returned None"}
    except Exception as exc:
        return {"can_render": True, "png_bytes": 0,
                "render_ms": int((time.time()-t0)*1000), "error": str(exc)[:200]}


from core.orchestrator import FitDashOrchestrator

print("=" * 70)
print(f"FitDash Remaining Query Test — {TS}")
print("=" * 70)

orch = FitDashOrchestrator()
results = []

for i, q in enumerate(QUERIES, 1):
    print(f"\n[{i}/{len(QUERIES)}] {q['id']}: {q['q'][:60]}...")
    t0 = time.time()

    try:
        answer, trace = orch.run(q["q"], [], None)
        elapsed = time.time() - t0
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"  CRASH ({elapsed:.0f}s): {exc}")
        results.append({
            "id": q["id"], "elapsed_s": round(elapsed, 1),
            "crashed": True, "error": str(exc),
        })
        continue

    tools_called = [tc["tool"] for tc in (trace.get("tool_calls") or [])]
    bare_tools = [t.split("__", 1)[-1] if "__" in t else t for t in tools_called]
    route_data = trace.get("route_data")
    viz_hints = trace.get("viz_hints") or {}

    # Chart renders
    charts = []
    for tc in (trace.get("tool_calls") or []):
        if tc.get("error"):
            continue
        ri = render_png(tc["tool"], tc.get("result", ""), q["q"])
        ri["tool"] = tc["tool"]
        charts.append(ri)

    # Grading
    issues = []
    expected = q.get("expect_tools", [])
    if expected and not any(e in bare_tools for e in expected):
        issues.append(f"WRONG_TOOL: expected {expected}, got {bare_tools}")
    if q.get("expect_map") and not route_data:
        issues.append("NO_MAP_DATA: expected route_data but got None")
    expected_metric = q.get("expect_viz_metric")
    if expected_metric:
        actual_metric = viz_hints.get("metric")
        if actual_metric != expected_metric:
            issues.append(f"WRONG_VIZ_METRIC: expected '{expected_metric}', got '{actual_metric}'")
    non_empty_charts = [c for c in charts if c.get("png_bytes", 0) > 1000]

    row = {
        "id":             q["id"],
        "elapsed_s":      round(elapsed, 1),
        "tools_called":   tools_called,
        "bare_tools":     bare_tools,
        "has_route_data": bool(route_data),
        "viz_metric":     viz_hints.get("metric"),
        "expect_metric":  expected_metric,
        "metric_ok":      (viz_hints.get("metric") == expected_metric) if expected_metric else None,
        "charts":         charts,
        "n_good_charts":  len(non_empty_charts),
        "issues":         issues,
        "answer_preview": answer[:200].replace("\n", " "),
    }
    results.append(row)

    # Summary print
    metric_note = ""
    if expected_metric:
        got = viz_hints.get("metric")
        metric_note = f" | metric={'OK' if got==expected_metric else f'WRONG({got})'}"
    map_note = " | MAP=OK" if route_data else (" | MAP=MISSING" if q.get("expect_map") else "")
    chart_note = f" | charts: {len(non_empty_charts)}/{len(charts)} non-empty"
    print(f"  {elapsed:.0f}s | {len(tools_called)} tools: {bare_tools[:3]}{metric_note}{map_note}{chart_note}")
    if issues:
        for iss in issues:
            print(f"  !! {iss}")
    print(f"  A: {answer[:100].replace(chr(10),' ')}")

# Write output
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"ts": TS, "results": results}, f, ensure_ascii=False, indent=2)
print(f"\n{'='*70}")
print(f"Results written to {OUT}")
total_issues = sum(len(r.get("issues", [])) for r in results)
total_good = sum(1 for r in results if not r.get("issues") and not r.get("crashed"))
print(f"Summary: {total_good}/{len(QUERIES)} clean, {total_issues} issues total")
