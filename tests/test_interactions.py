"""
Test harness: fire many queries at the orchestrator, capture traces, log issues.
Run with: conda run -n aiss2026 python test_interactions.py
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.orchestrator import FitDashOrchestrator
from core.host import ToolHost

QUERIES = [
    # ── Strava: basic activity listing ──────────────────────────────────────
    ("strava_list_recent",       "What were my last 10 activities?"),
    ("strava_list_runs",         "Show me my recent runs"),
    ("strava_list_hikes",        "Show me my recent hikes and their elevation gain"),
    ("strava_list_rides",        "Show me my recent bike rides"),

    # ── Strava: performance / trends ────────────────────────────────────────
    ("strava_pace_trend",        "Is my running pace improving over the last 30 runs?"),
    ("strava_training_load",     "What is my current training load? Am I overtraining?"),
    ("strava_training_trends",   "Show me my weekly training volume over the last 12 weeks"),
    ("strava_yearly_breakdown",  "Year-over-year comparison of my training"),
    ("strava_activity_stats",    "What are my all-time training statistics?"),
    ("strava_personal_bests",    "What are my personal bests in running?"),
    ("strava_fastest_ever",      "What's my fastest ever run by pace?"),
    ("strava_longest_run",       "What was my longest run ever?"),
    ("strava_highest_altitude",  "Which hike brought me to the highest altitude?"),
    ("strava_most_elevation",    "Which activity had the most total elevation gain?"),
    ("strava_longest_streak",    "What was my longest training streak?"),

    # ── Strava: specific activity detail ────────────────────────────────────
    ("strava_last_run_detail",   "Give me detailed stats of my last run — laps, splits, heart rate"),
    ("strava_compare_last",      "How does my last run compare to my typical runs? Was it harder or easier?"),
    ("strava_compare_last_hike", "Was my most recent hike harder or easier than my typical hike?"),
    ("strava_gear",              "What shoes and bikes do I use, and how worn out are they?"),

    # ── Strava: GPS / map queries ────────────────────────────────────────────
    ("strava_gps_last_run",      "Show me the GPS map of my last run with heart rate"),
    ("strava_gps_last_hike",     "Show me the route of my last hiking trip"),
    ("strava_elevation_profile", "Show me the elevation profile of my last hike"),

    # ── Garmin: health / wellness ────────────────────────────────────────────
    ("garmin_sleep_last",        "How did I sleep last night?"),
    ("garmin_sleep_score",       "What was my sleep score last night?"),
    ("garmin_body_battery",      "What is my current body battery level?"),
    ("garmin_body_battery_week", "Show me my body battery levels over the last week"),
    ("garmin_hrv",               "What is my HRV status today? Am I recovered?"),
    ("garmin_stress_today",      "How stressed was I today? When was my peak stress?"),
    ("garmin_steps_today",       "How many steps did I take today? Show me the timeline"),
    ("garmin_daily_health",      "Give me a health summary for today"),
    ("garmin_vo2max",            "What is my VO2max and current training status?"),
    ("garmin_race_preds",        "What are my race time predictions for a 10k and half marathon?"),
    ("garmin_wellness_2weeks",   "Show me my wellness trends over the past 2 weeks"),
    ("garmin_body_comp",         "Do I have any body weight or composition measurements?"),

    # ── Garmin + Strava combined / recovery ─────────────────────────────────
    ("recovery_readiness",       "Am I ready to train hard today? Check my HRV, body battery and sleep"),
    ("full_week_overview",       "Give me a full overview of my health and training this past week"),
    ("train_or_rest",            "Should I go for a hard run today or take a rest day?"),
    ("marathon_readiness",       "Am I in shape to run a marathon? Check my VO2max, training load and personal bests"),

    # ── Edge cases / ambiguity ───────────────────────────────────────────────
    ("ambiguous_activities",     "How many activities did I do last month?"),
    ("garmin_vs_strava",         "Compare my Garmin and Strava stats"),  # should NOT call both for activities
    ("last_activity",            "Tell me about my last activity"),
    ("hr_zones_last_run",        "Show me the heart rate zone distribution of my last run"),
    ("weekly_check_in",          "Weekly check-in: how was my training and recovery this week?"),

    # ── Potential missing tools / edge data ──────────────────────────────────
    ("intraday_hr",              "Show me my heart rate throughout the day today"),
    ("cadence_last_run",         "What was my cadence during my last run?"),
    ("power_last_ride",          "Did I have power meter data on my last bike ride?"),
    ("suffer_score",             "Which was my hardest workout this month by suffer score?"),
    ("activity_calories",        "How many calories did I burn across all my runs this month?"),
]


def grade_answer(query_id: str, query: str, answer: str, trace: dict) -> list:
    """Return a list of issue strings for this interaction, empty if ok."""
    issues = []
    tool_calls = trace.get("tool_calls") or []
    tool_names = [tc.get("tool", "") for tc in tool_calls]
    errors = [tc for tc in tool_calls if tc.get("error")]
    route_data = trace.get("route_data")
    viz_hints  = trace.get("viz_hints") or {}
    answer_lower = answer.lower()

    # ── Basic answer quality ─────────────────────────────────────────────────
    if not answer or len(answer) < 20:
        issues.append("EMPTY_OR_TOO_SHORT: answer is too short / blank")

    if "i don't have access" in answer_lower or "i cannot access" in answer_lower:
        issues.append("REFUSES_DATA: model claims it can't access data despite tool availability")

    if "i'm sorry" in answer_lower and len(tool_calls) == 0:
        issues.append("NO_TOOLS_CALLED: model answered with apology and called no tools")

    # ── Tool call quality ────────────────────────────────────────────────────
    if len(tool_calls) == 0 and any(kw in query.lower() for kw in [
        "activities", "run", "hike", "ride", "sleep", "hrv", "battery",
        "stress", "steps", "pace", "distance", "training", "calories",
        "gear", "vo2", "race", "recovery", "week", "month", "year"
    ]):
        issues.append("NO_TOOLS_CALLED: data-dependent query called no tools")

    # ── Double-fetching Strava + Garmin activities ───────────────────────────
    called_strava_acts  = any("get_activities" in t and "garmin" not in t for t in tool_names)
    called_garmin_acts  = any("get_garmin_activities" in t for t in tool_names)
    if called_strava_acts and called_garmin_acts:
        issues.append("DUAL_FETCH: called BOTH strava__get_activities AND garmin__get_garmin_activities — duplicates data, wastes budget")

    # ── Error handling ───────────────────────────────────────────────────────
    for tc in errors:
        err = tc.get("error", "")
        if "rate" in str(err).lower():
            issues.append(f"RATE_LIMIT: {tc['tool']} hit rate limit — {err[:80]}")
        elif err:
            issues.append(f"TOOL_ERROR: {tc['tool']} returned error — {err[:80]}")

    # ── GPS / map queries ────────────────────────────────────────────────────
    if any(kw in query.lower() for kw in ["gps", "map", "route", "trail", "elevation profile"]):
        if not route_data:
            # Check if streams / gps_track was at least called
            if not any("streams" in t or "gps_track" in t for t in tool_names):
                issues.append("NO_MAP_DATA: GPS/map query but no streams/gps_track tool was called and no route_data generated")
            else:
                issues.append("MAP_NOT_RENDERED: GPS/map tool called but route_data not populated — check _route_data() helper")

    # ── Sleep / health queries should use correct Garmin tools ───────────────
    if any(kw in query.lower() for kw in ["sleep", "hrv", "body battery", "stress", "steps"]):
        garmin_health_tools = [t for t in tool_names if "garmin" in t and "activities" not in t]
        if not garmin_health_tools:
            issues.append("WRONG_TOOL: health query but no Garmin health tools called")

    # ── Viz hints present for activity-list queries ──────────────────────────
    if any(kw in query.lower() for kw in ["highest altitude", "elevation gain", "fastest", "hardest", "pace"]):
        if not viz_hints:
            issues.append("NO_VIZ_HINT: metric-specific query but no VIZ tag emitted — chart will default to distance_km")

    # ── Answer mentions raw numbers that don't match tool results ────────────
    # Check for "I don't have" or "no data" when tools actually returned data
    has_real_data = any(
        not tc.get("error") for tc in tool_calls
    )
    if has_real_data and ("no data" in answer_lower or "no information" in answer_lower or "not available" in answer_lower):
        issues.append("INCORRECT_NO_DATA: model says no data but tools returned results")

    # ── Too many rounds ──────────────────────────────────────────────────────
    rounds = len({tc.get("tool") for tc in tool_calls})
    if rounds > 8:
        issues.append(f"EXCESSIVE_TOOL_CALLS: {rounds} distinct tools called — may be over-fetching")

    return issues


def run_tests():
    print(f"\n{'='*70}")
    print(f"FitDash Integration Test — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}\n")

    host = ToolHost()
    orch = FitDashOrchestrator(host=host)

    results = []
    all_issues = []

    for i, (qid, query) in enumerate(QUERIES):
        print(f"[{i+1:02d}/{len(QUERIES)}] {qid}: {query[:60]}...")
        t0 = time.perf_counter()
        try:
            answer, trace = orch.run(query, history=[], progress_cb=None)
        except Exception as exc:
            answer = ""
            trace = {"tool_calls": [], "route_data": None, "viz_hints": {}, "error": str(exc)}
            print(f"  !! EXCEPTION: {exc}")

        elapsed = time.perf_counter() - t0
        tool_names = [tc.get("tool","") for tc in (trace.get("tool_calls") or [])]
        issues = grade_answer(qid, query, answer, trace)

        result = {
            "id":       qid,
            "query":    query,
            "answer":   answer[:400],
            "tools":    tool_names,
            "elapsed_s": round(elapsed, 1),
            "issues":   issues,
            "has_route_data": bool(trace.get("route_data")),
            "viz_hints": trace.get("viz_hints") or {},
            "error":    trace.get("error"),
        }
        results.append(result)

        status = "✓" if not issues else f"✗ {len(issues)} issue(s)"
        print(f"  {status}  ({elapsed:.0f}s)  tools: {', '.join(tool_names) or 'none'}")
        for issue in issues:
            print(f"    ► {issue}")
            all_issues.append({"query_id": qid, "query": query, "issue": issue})

    # ── Write detailed report ────────────────────────────────────────────────
    logs_dir = Path("tests/logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    report_path = logs_dir / f"run_{ts}_report.json"
    report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    issues_path = logs_dir / f"run_{ts}_issues.json"
    issues_path.write_text(json.dumps(all_issues, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also write a human-readable markdown summary
    md_path = logs_dir / f"run_{ts}_summary.md"
    _write_markdown_summary(md_path, results, all_issues, ts)

    # ── Print summary ────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"SUMMARY — {len(results)} queries, {len(all_issues)} issues found")
    print(f"{'='*70}")
    from collections import Counter
    issue_types = Counter(iss["issue"].split(":")[0] for iss in all_issues)
    for itype, count in issue_types.most_common():
        print(f"  {count:3d}x  {itype}")

    failing = [r for r in results if r["issues"]]
    print(f"\n  {len(failing)} / {len(results)} queries had issues")
    print(f"  Detailed results → {report_path}")
    print(f"  Issue list       → {issues_path}")
    print(f"  Markdown summary → {md_path}")
    return results, all_issues


def _write_markdown_summary(path: Path, results: list, all_issues: list, ts: str) -> None:
    from collections import Counter
    lines = [
        f"# FitDash Integration Test — {ts}",
        f"",
        f"**{len(results)} queries tested · {len(all_issues)} issues found · "
        f"{sum(1 for r in results if r['issues'])} queries with problems**",
        f"",
        f"## Issue Type Frequency",
        f"",
    ]
    issue_types = Counter(iss["issue"].split(":")[0] for iss in all_issues)
    for itype, count in issue_types.most_common():
        lines.append(f"- `{itype}` × {count}")
    lines += ["", "## Per-Query Results", ""]
    for r in results:
        status = "✓" if not r["issues"] else f"✗ {len(r['issues'])} issue(s)"
        tools_str = ", ".join(f"`{t}`" for t in r["tools"]) or "_(none)_"
        lines.append(f"### [{status}] `{r['id']}` ({r['elapsed_s']}s)")
        lines.append(f"> {r['query']}")
        lines.append(f"")
        lines.append(f"**Tools called:** {tools_str}")
        if r.get("viz_hints"):
            lines.append(f"**Viz hints:** `{r['viz_hints']}`")
        if r.get("has_route_data"):
            lines.append(f"**Route map:** ✓ will be rendered")
        if r["issues"]:
            lines.append(f"")
            lines.append(f"**Issues:**")
            for iss in r["issues"]:
                lines.append(f"- ⚠ {iss}")
        lines.append(f"")
        lines.append(f"**Answer preview:** {r['answer'][:300]}")
        lines += ["", "---", ""]

    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    run_tests()
