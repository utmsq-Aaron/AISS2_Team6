"""Visualization quality test for FitDash chat.

Fires queries at the orchestrator, then:
1. Verifies the right tool was called
2. Runs the actual chart renderer (core/viz_telegram.py) on each tool result
3. Records whether each PNG is non-empty
4. Cross-checks chart TYPE against query INTENT (LLM-based in Telegram)
5. Checks route_data quality (GPS point count, not just presence)

Results are written to tests/logs/viz_quality_<timestamp>.json and a Markdown report.
"""

import sys
import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path

# Encoding safety on Windows
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)

# ── Output directory ──────────────────────────────────────────────────────────
LOG_DIR = ROOT / "tests" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
TS = datetime.now().strftime("%Y%m%d_%H%M")

# ── Queries to test ───────────────────────────────────────────────────────────
# Focus on: viz-heavy, previously untested, or types that had issues

QUERIES = [
    # ── Previously broken: HR zones via Garmin ──────────────────────────────
    {
        "id": "hr_zones_garmin_fix",
        "q":  "Show me the heart rate zone distribution of my last run",
        "expect_tools": ["get_garmin_activity_detail"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "HR zones bar chart from Garmin activity detail",
    },
    # ── Previously broken: GPS hike map ──────────────────────────────────────
    {
        "id": "gps_hike_fix",
        "q":  "Show me the GPS route of my last hike on the map",
        "expect_tools": ["get_activity_streams", "get_activity_gps_track"],
        "expect_chart": True,
        "expect_map":   True,
        "intent":       "GPS route map with elevation coloring",
    },
    # ── New: lap splits for a run ─────────────────────────────────────────────
    {
        "id": "lap_splits_run",
        "q":  "Show me the lap splits and cadence of my last run",
        "expect_tools": ["get_garmin_activity_detail"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Lap splits bar chart with HR overlay",
    },
    # ── New: weather forecast ─────────────────────────────────────────────────
    {
        "id": "weather_forecast_viz",
        "q":  "What is the weather forecast for Karlsruhe over the next 7 days? "
               "Show me temperature and rain probability.",
        "expect_tools": ["get_weather_forecast"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Temperature band + rain probability chart",
    },
    # ── New: plan a route ─────────────────────────────────────────────────────
    {
        "id": "plan_route_map",
        "q":  "Plan a 10km running route starting from Karlsruhe city centre as a loop",
        "expect_tools": ["plan_circular_route"],
        "expect_chart": False,
        "expect_map":   True,
        "intent":       "Folium map with planned route polyline",
    },
    # ── New: training metrics ─────────────────────────────────────────────────
    {
        "id": "training_metrics_viz",
        "q":  "What is my VO2max, training status, and predicted marathon time?",
        "expect_tools": ["get_garmin_training_metrics"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "VO2max gauge + race prediction metrics",
    },
    # ── New: body composition ─────────────────────────────────────────────────
    {
        "id": "body_composition_viz",
        "q":  "Show me my weight and body composition over the last 3 months",
        "expect_tools": ["get_garmin_body_composition"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Weight line chart with optional body fat line",
    },
    # ── New: gear mileage ────────────────────────────────────────────────────
    {
        "id": "gear_mileage_viz",
        "q":  "How many km are on my running shoes and bikes? Show the mileage.",
        "expect_tools": ["get_gear_info"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Horizontal bar chart: gear name vs. km",
    },
    # ── New: activity stats ───────────────────────────────────────────────────
    {
        "id": "activity_stats_viz",
        "q":  "Give me an overview of all my training: total distance, time per sport",
        "expect_tools": ["get_activity_stats"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Sport breakdown bar chart + metric cards",
    },
    # ── New: training load chart ─────────────────────────────────────────────
    {
        "id": "training_load_viz",
        "q":  "Show me my training load, ATL/CTL, and current form score",
        "expect_tools": ["get_training_load"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "ATL/CTL line chart + TSB bar chart",
    },
    # ── New: activity streams GPS with HR ────────────────────────────────────
    {
        "id": "gps_run_hr_map",
        "q":  "Show me the GPS map of my last run coloured by heart rate",
        "expect_tools": ["get_activity_streams"],
        "expect_chart": True,
        "expect_map":   True,
        "intent":       "GPS map with HR color gradient + HR profile chart",
    },
    # ── New: performance trend ───────────────────────────────────────────────
    {
        "id": "perf_trend_viz",
        "q":  "Is my running pace getting faster? Show me the trend over last 20 runs",
        "expect_tools": ["analyze_performance_trends"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Pace trend + HR trend chart with regression line",
    },
    # ── New: yearly breakdown ────────────────────────────────────────────────
    {
        "id": "yearly_breakdown_viz",
        "q":  "Compare my training volume year over year since I started running",
        "expect_tools": ["get_yearly_breakdown"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Year-over-year bar chart",
    },
    # ── New: personal bests ──────────────────────────────────────────────────
    {
        "id": "personal_bests_viz",
        "q":  "What are my top 5 longest and fastest runs ever?",
        "expect_tools": ["get_personal_bests"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Top 5 tables: distance, pace, elevation",
    },
    # ── New: wellness trends ─────────────────────────────────────────────────
    {
        "id": "wellness_trends_viz",
        "q":  "Show me my sleep, stress, body battery and steps over the last 2 weeks",
        "expect_tools": ["get_garmin_wellness_trends"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Multi-panel wellness chart (sleep/BB/RHR/steps/stress)",
    },
    # ── New: HRV status ──────────────────────────────────────────────────────
    {
        "id": "hrv_viz",
        "q":  "What is my HRV status today and how does it compare to my baseline?",
        "expect_tools": ["get_garmin_hrv_status"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "HRV gauge with baseline range",
    },
    # ── New: intraday steps ──────────────────────────────────────────────────
    {
        "id": "steps_timeline_viz",
        "q":  "Show me my step count hour by hour today",
        "expect_tools": ["get_garmin_steps_timeline"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Stacked bar chart: steps per 15min bucket, color by activity level",
    },
    # ── New: stress timeline ─────────────────────────────────────────────────
    {
        "id": "stress_timeline_viz",
        "q":  "Show me my stress levels throughout the day with the peaks highlighted",
        "expect_tools": ["get_garmin_stress_timeline"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Stress area chart with zone color coding",
    },
    # ── New: compare activity ────────────────────────────────────────────────
    {
        "id": "compare_activity_viz",
        "q":  "How difficult was my last bike ride compared to my typical rides? "
               "Show difficulty percentiles.",
        "expect_tools": ["compare_activity_to_baseline"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Difficulty percentile bar chart",
    },
    # ── New: trails near home ────────────────────────────────────────────────
    {
        "id": "explore_trails_viz",
        "q":  "Find me 3 hiking trails within 15 km of Karlsruhe",
        "expect_tools": ["explore_trails"],
        "expect_chart": False,
        "expect_map":   True,
        "intent":       "Folium map with multiple trail polylines",
    },
    # ── Elevation metric hint ────────────────────────────────────────────────
    {
        "id": "elevation_gain_hint",
        "q":  "Which of my recent hikes had the most elevation gain? Rank them.",
        "expect_tools": ["get_activities"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Bar chart sorted by elevation_gain_m (not distance)",
        "expect_viz_metric": "elevation_gain_m",
    },
    # ── Pace metric hint ─────────────────────────────────────────────────────
    {
        "id": "pace_metric_hint",
        "q":  "Show me my last 10 runs ranked by pace (fastest first)",
        "expect_tools": ["get_activities"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Bar chart sorted by pace_min_per_km",
        "expect_viz_metric": "pace_min_per_km",
    },
    # ── HR metric hint ───────────────────────────────────────────────────────
    {
        "id": "hr_metric_hint",
        "q":  "Which runs had my highest average heart rate?",
        "expect_tools": ["get_activities"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Bar chart sorted by avg_heart_rate",
        "expect_viz_metric": "avg_heart_rate",
    },
    # ── Suffer score hint (our new fix) ─────────────────────────────────────
    {
        "id": "suffer_score_hint",
        "q":  "Which activities had the highest suffer score this year?",
        "expect_tools": ["get_activities"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Bar chart sorted by suffer_score",
        "expect_viz_metric": "suffer_score",
    },
    # ── Recovery check: multi-tool ───────────────────────────────────────────
    {
        "id": "recovery_multi_chart",
        "q":  "Full recovery check: HRV, body battery, and last night's sleep — "
               "am I ready for a hard workout?",
        "expect_tools": ["get_garmin_hrv_status", "get_garmin_body_battery", "get_garmin_sleep"],
        "expect_chart": True,
        "expect_map":   False,
        "intent":       "Multiple charts: HRV gauge + body battery line + sleep stages",
    },
]


# ── Viz renderer test (calls actual matplotlib/PIL) ───────────────────────────

def _test_chart_render(tool_name: str, result_json: str, user_query: str = "") -> dict:
    """Try to render a chart and return quality info."""
    try:
        from core.viz_telegram import can_render, render_chart_png
    except ImportError:
        return {"can_render": False, "png_bytes": 0, "error": "viz_telegram import failed"}

    bare = tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name
    if not can_render(bare):
        return {"can_render": False, "png_bytes": 0, "error": "not in registry"}

    t0 = time.time()
    try:
        png = render_chart_png(tool_name, result_json, user_query)
    except Exception as exc:
        return {"can_render": True, "png_bytes": 0, "render_ms": int((time.time()-t0)*1000), "error": str(exc)}

    return {
        "can_render": True,
        "png_bytes": len(png) if png else 0,
        "render_ms": int((time.time()-t0)*1000),
        "error": None if png else "render returned None",
    }


def _check_route_data_quality(route_data: dict | None) -> dict:
    """Return quality metrics about route_data."""
    if not route_data:
        return {"has_route": False}
    data = route_data.get("data") or {}
    tool = route_data.get("tool", "")
    waypoints = data.get("waypoints") or data.get("points") or []
    n_points = len(waypoints)
    return {
        "has_route": True,
        "tool":      tool,
        "n_points":  n_points,
        "quality":   "good" if n_points >= 10 else "sparse" if n_points > 0 else "empty",
    }


def _check_data_quality(tool_name: str, result_json: str) -> dict:
    """Check key fields in tool result for common quality issues."""
    try:
        data = json.loads(result_json) if isinstance(result_json, str) else result_json
    except Exception:
        return {"parse_error": True}

    if not isinstance(data, dict):
        return {"type": type(data).__name__}
    if data.get("error"):
        return {"tool_error": data["error"]}

    bare = tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name
    out: dict = {"bare_tool": bare}

    if bare in ("get_activities", "get_garmin_activities"):
        acts = data.get("activities") or []
        out["n_acts"] = len(acts)
        out["pct_with_hr"]   = round(sum(1 for a in acts if a.get("avg_heart_rate") or a.get("avg_hr")) / max(len(acts), 1) * 100)
        out["pct_with_pace"] = round(sum(1 for a in acts if a.get("pace_min_per_km")) / max(len(acts), 1) * 100)
        out["pct_with_elev"] = round(sum(1 for a in acts if a.get("elevation_gain_m")) / max(len(acts), 1) * 100)

    elif bare == "get_activity_streams":
        pts = data.get("points") or []
        out["n_points"] = len(pts)
        out["has_hr"]   = any(p.get("hr") for p in pts[:50])
        out["has_ele"]  = any(p.get("ele") is not None for p in pts[:50])
        out["has_gps"]  = any(p.get("lat") for p in pts[:10])

    elif bare == "get_activity_gps_track":
        pts = data.get("points") or []
        out["n_points"] = len(pts)
        out["has_ele"]  = any(p.get("ele") is not None for p in pts[:50])
        out["has_gps"]  = any(p.get("lat") for p in pts[:10])

    elif bare == "get_garmin_wellness_trends":
        trend = data.get("trend") or []
        out["n_days"] = len(trend)
        out["pct_with_bb"]    = round(sum(1 for t in trend if t.get("body_battery_high")) / max(len(trend), 1) * 100)
        out["pct_with_rhr"]   = round(sum(1 for t in trend if t.get("resting_hr")) / max(len(trend), 1) * 100)
        out["pct_with_sleep"] = round(sum(1 for t in trend if t.get("total_sleep_h")) / max(len(trend), 1) * 100)
        out["pct_with_steps"] = round(sum(1 for t in trend if t.get("steps")) / max(len(trend), 1) * 100)

    elif bare == "get_garmin_sleep":
        out["has_stages"] = bool(data.get("deep_h") or data.get("rem_h"))
        out["total_h"]    = data.get("total_sleep_h")
        out["score"]      = data.get("sleep_score")

    elif bare == "get_garmin_hrv_status":
        out["hrv_ms"]   = data.get("last_night_hrv")
        out["status"]   = data.get("status")
        out["has_data"] = data.get("last_night_hrv") is not None

    elif bare == "get_garmin_body_battery":
        days = [d for d in (data.get("days") or []) if d.get("highest") is not None]
        out["n_days"] = len(days)
        out["quality"] = "good" if len(days) >= 5 else "sparse" if days else "empty"

    elif bare == "get_garmin_activity_detail":
        laps = data.get("laps") or []
        zones = data.get("hr_zones") or []
        out["n_laps"]    = len(laps)
        out["n_zones"]   = len(zones)
        out["has_pace"]  = any(l.get("pace_min_per_km") for l in laps)
        out["has_hr_z"]  = bool(zones)

    elif bare == "get_garmin_training_metrics":
        out["vo2max"]   = data.get("vo2max_running")
        out["status"]   = data.get("training_status")
        out["has_preds"] = bool(data.get("race_predictions"))

    elif bare == "get_weather_forecast":
        f = data.get("forecast") or []
        out["n_days"] = len(f)
        out["has_temp"] = any(d.get("temp_max_c") for d in f)
        out["has_rain"] = any(d.get("precip_probability_pct") is not None for d in f)

    elif bare in ("plan_route", "plan_circular_route"):
        pts = data.get("waypoints") or []
        out["n_waypoints"] = len(pts)
        out["distance_km"] = data.get("total_distance_km") or data.get("distance_km")

    elif bare == "explore_trails":
        trails = data.get("trails") or []
        out["n_trails"] = len(trails)
        out["has_segments"] = any(t.get("segments") for t in trails)

    elif bare == "get_garmin_body_composition":
        measurements = data.get("measurements") or []
        out["n_measurements"] = len(measurements)
        out["has_weight"] = any(m.get("weight_kg") for m in measurements)
        out["has_fat"]    = any(m.get("body_fat_pct") for m in measurements)

    elif bare == "get_gear_info":
        shoes = data.get("shoes") or []
        bikes = data.get("bikes") or []
        out["n_shoes"] = len(shoes)
        out["n_bikes"] = len(bikes)
        out["total_km"] = sum(s.get("distance_km", 0) for s in shoes + bikes)

    elif bare == "compare_activity_to_baseline":
        comps = data.get("comparisons") or {}
        out["n_metrics"] = len(comps)
        out["has_percentiles"] = any(v.get("difficulty_percentile") is not None for v in comps.values())

    elif bare == "get_activity_stats":
        breakdown = data.get("sport_breakdown") or {}
        out["n_sports"]   = len(breakdown)
        out["total_km"]   = data.get("total_distance_km")
        out["total_acts"] = data.get("total_activities")

    return out


# ── Grade a single result ─────────────────────────────────────────────────────

def grade(q: dict, result: dict) -> list:
    """Return list of (issue_code, description) for this result."""
    issues = []
    answer = result.get("answer", "").lower()
    tools  = result.get("tools_called", [])
    trace  = result.get("trace") or {}
    bare_tools = [t.split("__", 1)[-1] if "__" in t else t for t in tools]

    # Tool call check
    expect = q.get("expect_tools", [])
    if expect and not any(e in bare_tools for e in expect):
        issues.append(("WRONG_TOOL",
                       f"Expected one of {expect}, got {bare_tools}"))

    # Map check
    if q.get("expect_map") and not result.get("route_data"):
        issues.append(("NO_MAP_DATA", "Expected GPS/route map but route_data is None"))

    # VIZ metric check
    if "expect_viz_metric" in q:
        hints = trace.get("viz_hints") or {}
        actual_metric = hints.get("metric")
        if actual_metric != q["expect_viz_metric"]:
            issues.append(("WRONG_VIZ_METRIC",
                           f"Expected metric hint '{q['expect_viz_metric']}', got '{actual_metric}'"))

    # Chart render check
    charts_rendered = result.get("charts_rendered", [])
    if q.get("expect_chart") and not any(c.get("png_bytes", 0) > 1000 for c in charts_rendered):
        issues.append(("EMPTY_CHART",
                       f"Expected a non-empty chart but all renders produced <1KB "
                       f"({[(c.get('tool','?'), c.get('png_bytes',0)) for c in charts_rendered]})"))

    # Tool errors
    for tc in (trace.get("tool_calls") or []):
        if tc.get("error"):
            issues.append(("TOOL_ERROR",
                           f"{tc['tool']} returned error — {tc.get('error','')}"))

    # Hallucinated map claim
    if (q.get("expect_map") and "map is shown" in answer and not result.get("route_data")):
        issues.append(("HALLUCINATED_MAP",
                       "Model claims map is shown but no GPS data was fetched"))

    # Empty answer
    if len(answer.strip()) < 30:
        issues.append(("EMPTY_ANSWER", f"Answer too short: {repr(answer[:80])}"))

    return issues


# ── Main runner ───────────────────────────────────────────────────────────────

def run_tests(queries=None, out_dir=LOG_DIR):
    from core.orchestrator import FitDashOrchestrator

    queries = queries or QUERIES
    orch = FitDashOrchestrator()

    results = []
    total_issues = 0

    print("=" * 72)
    print(f"FitDash Visualization Quality Test — {TS}")
    print("=" * 72)

    for i, q in enumerate(queries, 1):
        print(f"\n[{i:02d}/{len(queries)}] {q['id']}: {q['q'][:65]}…")
        t0 = time.time()

        try:
            answer, trace = orch.run(q["q"], [], None)
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"  CRASH ({elapsed:.0f}s): {exc}")
            results.append({
                "id": q["id"], "q": q["q"], "elapsed_s": round(elapsed, 1),
                "crashed": True, "error": str(exc), "issues": [("CRASH", str(exc))],
            })
            total_issues += 1
            continue

        elapsed = time.time() - t0
        tools_called = [tc["tool"] for tc in (trace.get("tool_calls") or [])]
        route_data   = trace.get("route_data")

        # ── Run chart renderers on each tool result ──────────────────────────
        charts_rendered = []
        data_quality    = []
        for tc in (trace.get("tool_calls") or []):
            if tc.get("error"):
                continue
            result_json = tc.get("result", "")
            render_info = _test_chart_render(tc["tool"], result_json, q["q"])
            render_info["tool"] = tc["tool"]
            charts_rendered.append(render_info)

            dq = _check_data_quality(tc["tool"], result_json)
            dq["tool"] = tc["tool"]
            data_quality.append(dq)

        # ── Grade ────────────────────────────────────────────────────────────
        row = {
            "id":             q["id"],
            "q":              q["q"],
            "intent":         q.get("intent", ""),
            "elapsed_s":      round(elapsed, 1),
            "answer_len":     len(answer or ""),
            "tools_called":   tools_called,
            "route_data":     _check_route_data_quality(route_data),
            "charts_rendered":charts_rendered,
            "data_quality":   data_quality,
            "viz_hints":      trace.get("viz_hints") or {},
            "issues":         [],
        }
        row["trace"] = {"tool_calls": trace.get("tool_calls") or [], "viz_hints": trace.get("viz_hints") or {}}

        row["issues"] = grade(q, {
            "answer": answer, "tools_called": tools_called,
            "route_data": route_data, "trace": trace,
            "charts_rendered": charts_rendered,
        })
        total_issues += len(row["issues"])

        # ── Print summary ─────────────────────────────────────────────────────
        chart_ok  = [c for c in charts_rendered if (c.get("png_bytes") or 0) > 1000]
        chart_bad = [c for c in charts_rendered if (c.get("png_bytes") or 0) <= 1000 and c.get("can_render")]
        status = "✓" if not row["issues"] else "✗"
        print(f"  {status}  ({elapsed:.0f}s)  tools: {', '.join(tools_called) or 'none'}")
        print(f"       charts OK: {len(chart_ok)}  |  empty/bad: {len(chart_bad)}")
        if route_data:
            rq = _check_route_data_quality(route_data)
            print(f"       route_data: {rq.get('tool','')} — {rq.get('n_points',0)} pts ({rq.get('quality','')})")
        if row["issues"]:
            for code, msg in row["issues"]:
                print(f"    ► {code}: {msg}")
        if chart_bad:
            for c in chart_bad:
                err_or_bytes = c.get("error", "") or f"{c.get('png_bytes', 0)} bytes"
                print(f"    ★ EMPTY_CHART: {c['tool']} — {err_or_bytes}")
        if data_quality:
            for dq in data_quality:
                interesting = {k: v for k, v in dq.items() if k not in ("bare_tool", "tool")}
                if interesting:
                    print(f"    data [{dq['tool'].split('__')[-1]}]: {interesting}")

        results.append(row)

    # ── Write JSON ────────────────────────────────────────────────────────────
    json_path = out_dir / f"viz_quality_{TS}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        # Don't serialise full trace (too large)
        for r in results:
            r.pop("trace", None)
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    # ── Write Markdown report ─────────────────────────────────────────────────
    md_path = out_dir / f"viz_quality_{TS}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# FitDash Visualization Quality Test — {TS}\n\n")
        f.write(f"**Queries:** {len(queries)}  |  **Total issues:** {total_issues}\n\n")

        f.write("## Chart Render Summary\n\n")
        f.write("| Query | Tools | Chart PNGs | Route pts | Issues |\n")
        f.write("|---|---|---|---|---|\n")
        for r in results:
            tools_short = ", ".join(t.split("__")[-1] for t in r.get("tools_called",[]))[:50]
            chart_ok_n  = sum(1 for c in r.get("charts_rendered",[]) if (c.get("png_bytes") or 0) > 1000)
            chart_reg_n = sum(1 for c in r.get("charts_rendered",[]) if c.get("can_render"))
            rq  = r.get("route_data") or {}
            rpt = f"{rq.get('n_points','—')} ({rq.get('quality','no map')})" if rq.get("has_route") else "—"
            iss = "; ".join(f"**{c}**" for c, _ in (r.get("issues") or []))
            f.write(f"| {r['id']} | {tools_short} | {chart_ok_n}/{chart_reg_n} | {rpt} | {iss or '✓'} |\n")

        f.write("\n## Issues Detail\n\n")
        for r in results:
            if not r.get("issues"):
                continue
            f.write(f"### {r['id']}\n")
            f.write(f"*Query:* {r['q']}\n\n")
            f.write(f"*Intent:* {r.get('intent','')}\n\n")
            for code, msg in r["issues"]:
                f.write(f"- **{code}**: {msg}\n")
            f.write("\n")

        f.write("\n## Data Quality Observations\n\n")
        for r in results:
            dq_list = r.get("data_quality") or []
            if not dq_list:
                continue
            f.write(f"### {r['id']}\n")
            for dq in dq_list:
                bare = dq.get("tool","").split("__")[-1]
                info = {k: v for k, v in dq.items() if k not in ("bare_tool","tool")}
                f.write(f"- `{bare}`: {json.dumps(info)}\n")
            f.write("\n")

    # ── Print summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"SUMMARY — {len(queries)} queries, {total_issues} total issues")
    print("=" * 72)
    chart_ok_total   = sum(sum(1 for c in r.get("charts_rendered",[]) if (c.get("png_bytes") or 0) > 1000) for r in results)
    chart_reg_total  = sum(sum(1 for c in r.get("charts_rendered",[]) if c.get("can_render")) for r in results)
    chart_empty_total= chart_reg_total - chart_ok_total
    print(f"  Charts rendered OK:    {chart_ok_total}/{chart_reg_total}")
    print(f"  Charts empty/failed:   {chart_empty_total}")
    n_with_map = sum(1 for r in results if r.get("route_data",{}).get("has_route"))
    print(f"  Queries with GPS map:  {n_with_map}")
    issue_counts: dict = {}
    for r in results:
        for code, _ in (r.get("issues") or []):
            issue_counts[code] = issue_counts.get(code, 0) + 1
    for code, n in sorted(issue_counts.items(), key=lambda x: -x[1]):
        print(f"  {n:3d}x  {code}")
    print(f"\n  JSON  → {json_path}")
    print(f"  MD    → {md_path}")
    return results


if __name__ == "__main__":
    run_tests()
