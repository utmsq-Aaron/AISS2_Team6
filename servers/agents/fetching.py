"""FetchingAgent — plans and executes all MCP data fetches (Strava, Garmin, Weather, Routes).

Multi-agent role: data retrieval specialist.
  - Receives the user query and today's date
  - Uses an LLM planner to decide which MCP tools to call
  - Validates the plan against the actual tool list before executing
  - Executes valid steps in parallel (ThreadPoolExecutor)
  - Returns a structured JSON result set for downstream agents

Exposed as an MCP server tool:  fetch_data(query, today, history?)
Callable in-process via:        call_sync(query, today, history?)

Standalone usage:
    python servers/agents/fetching.py
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from mcp.server.fastmcp import FastMCP

from servers.agents._base import llm_call, truncate, extract_json, FLYTHROUGH_KEYWORDS

mcp = FastMCP(
    "FetchingAgent",
    instructions="Plans and executes all fitness and environment data retrievals via MCP tools.",
)

# ── Planner prompt ────────────────────────────────────────────────────────────
#
# Design principle: teach the LLM to reason (GOAL→DATA→TOOLS→PLAN), not to
# pattern-match a list of rules. The tool descriptions carry the per-tool
# constraints; the planner's job is to think about the question, not memorise paths.

_PLANNER_SYSTEM = """\
ROLE: You are FetchingAgent — Phase 1 of 4 in the FitDash analytics pipeline.
Your sole responsibility: decide what data to retrieve and output a validated \
call plan. Downstream agents depend on your structured results.
Today is {today}.

PIPELINE POSITION:
  You → [VisualizationAgent + FlyoverAgent in parallel] → ChatAgent
  • VisualizationAgent will render charts from your results automatically.
  • FlyoverAgent will trigger a 3D flythrough if launch_flythrough succeeded.
  • ChatAgent writes the final answer using everything you retrieved.

REASONING METHOD — use this exact structure inside the "reasoning" field:
  GOAL:    One sentence — what is the user actually trying to learn or do?
  DATA:    What specific data fully answers it?
  TOOLS:   Which tools return that data and why each one?
  HISTORY: What context from prior conversation turns is relevant here?
           (e.g. an activity already identified, params already confirmed)
  PLAN:    Final call list with key params — note any trade-offs or gaps.

RULES:
  1. TOOL NAMES — only use names that appear VERBATIM in Available Tools below.
     Never abbreviate, invent, or guess. An unknown name wastes a turn.
  2. DATES — always compute explicit YYYY-MM-DD dates. Today = {today}.
     Never pass "last week", "yesterday", or any relative string.
  3. BATCH STRATEGY:
     - Intraday metrics (sleep, hr_timeline, steps_timeline, hrv, daily_health):
       one call per day.
     - Range tools (wellness_trends, body_battery, training_trends, activities,
       yearly_breakdown): one call for the full range.
  4. ALL-TIME queries ("fastest ever", "best ever", "all-time", "highest ever"):
     use start_date="2010-01-01" so all history is included.
  5. STREAMS / ROUTE — get_activity_streams(activity_name=keyword) is self-contained.
     No prior get_activities call is needed for name-based lookups.
  6. FLYTHROUGH — launch_flythrough requires ALL THREE user-confirmed params:
       orientation ("landscape" or "portrait"),
       mode ("satellite_3d" or "dark"),
       duration_sec (integer 30–120).
     If ANY is missing: fetch activities or detail to identify the right activity,
     but do NOT call launch_flythrough yet. ChatAgent will ask for the missing params.
     If ALL THREE are confirmed: call launch_flythrough directly (only one call).
  7. FOLLOW-UP TURNS — if the user's message is a short reply (e.g. "landscape,
     satellite 3D, 60 seconds") that provides parameters for a prior request,
     check history to understand what was being set up. If a flythrough was started
     in a previous turn and the activity was already identified, use that context:
     call launch_flythrough with the identified activity and the new params.
     Resolve the activity name from history via get_activity_detail if needed.
  8. CORRELATIONS — fetch both data sources when the query spans two metrics.
  9. NO DATA NEEDED — greetings, math, direct replies: return steps=[].
  10. CLARIFICATION — only ask when the query is genuinely ambiguous AND history
      does not resolve it AND no clarification on this topic was already asked.
      Default to fetching. Write exactly ONE specific question.
  11. MAX STEPS — maximum {max_steps} steps.
  12. WEATHER TOOLS — use get_current_weather, get_pollen_levels, or get_uv_index when the
      user asks about weather, temperature, wind, pollen/allergy, or UV index.
      These tools require no arguments. Combine with fitness data if the question
      links both (e.g. "is today good for a run?"). Never fabricate forecasts —
      only current conditions are available from these tools.

Available tools:
{tool_descriptions}

OUTPUT — reply ONLY with valid JSON (no markdown fences):
{{
  "reasoning": "GOAL: ... DATA: ... TOOLS: ... HISTORY: ... PLAN: ...",
  "clarification_needed": false,
  "clarification_question": "",
  "steps": [
    {{"tool": "<exact tool name>", "args": {{}}, "label": "<short human label>"}},
    ...
  ]
}}
"""

MAX_STEPS        = 60
MAX_REFINE_STEPS = 5   # max follow-up steps in the refinement pass
MAX_WORKERS      = 5
TIMEOUT_S        = 120

# Tools that return lists with activity IDs — only these warrant a refinement pass.
# Point-in-time or aggregate tools are self-contained and never need follow-up fetches.
_REFINABLE_TOOLS = {
    "get_activities", "get_garmin_activities", "get_personal_bests",
    "get_yearly_breakdown", "get_training_trends",
}

# Tools that return many rows — give their result_summary more room so the
# refinement LLM sees enough IDs/names to pick the right follow-up call.
_LIST_TOOLS = {
    "get_activities", "get_garmin_activities", "get_personal_bests",
    "get_yearly_breakdown", "get_training_trends", "get_garmin_wellness_trends",
}

# ── Refinement prompt ─────────────────────────────────────────────────────────
#
# After executing the initial plan the LLM may realize it has intermediate data
# (e.g. an activity_id from a list) needed to make a more specific follow-up call
# (e.g. streams for that exact activity). This second pass handles that.

_REFINE_SYSTEM = """\
ROLE: You are the refinement pass of FetchingAgent.
Today is {today}. Original query: "{query}"
Initial reasoning: {reasoning}

Results retrieved so far:
{results_block}

Tools already called this turn: {already_called}

TASK: Decide if any FOLLOW-UP tool calls are needed to more precisely answer the
query, given IDs or data that are now available from the initial results.

FOLLOW-UP IS NEEDED when:
  • An activity list was fetched and the query asks for a SPECIFIC one (fastest,
    longest, named) — you can now call with the exact activity_id from the results.
  • The query requests a FLYTHROUGH and all three required params
    (orientation, mode, duration_sec) are present in the original query or
    history — call launch_flythrough with the activity_id from the results.
  • You have an ID that wasn't available before and need detail for it.

FOLLOW-UP IS NOT NEEDED when:
  • The data already retrieved is sufficient to answer the query.
  • You would just repeat a call already made (check {already_called}).
  • The query is about aggregates or trends already covered by initial results.
  • A required param for launch_flythrough is missing — let ChatAgent ask instead.

Maximum {max_steps} follow-up steps. Only add steps you are CERTAIN are necessary.

Available tools:
{tool_descriptions}

OUTPUT — reply ONLY with valid JSON (no markdown fences):
{{"reasoning": "brief explanation", "steps": [{{"tool": "<name>", "args": {{}}, "label": "<label>"}}]}}
Return {{"reasoning": "sufficient", "steps": []}} if no follow-up is needed.
"""


# ── MCP tool (exposed to external MCP clients) ────────────────────────────────

@mcp.tool()
def fetch_data(query: str, today: str, history: str = "[]") -> str:
    """Plan and execute all MCP data fetches needed to answer a fitness analytics query.

    Args:
        query:   The user's natural-language question.
        today:   Today's date as YYYY-MM-DD.
        history: JSON-encoded list of {role, content} conversation turns (optional).

    Returns:
        JSON string: {reasoning, results: [{tool, label, args, result,
                      result_summary, duration_ms, error}], data_summary, total_ms}
    """
    try:
        hist = json.loads(history) if history else []
    except (json.JSONDecodeError, TypeError):
        hist = []
    return call_sync(query, today, hist)


# ── In-process entry point (called by orchestrator, no transport overhead) ────

_WEATHER_KEYWORDS = frozenset({
    "wetter", "weather", "temperatur", "temperature", "regen", "rain", "sonne", "sun",
    "wolken", "cloud", "wind", "schnee", "snow", "pollen", "allergie", "allergy",
    "uv", "uv-index", "uvindex", "luftqualität", "air quality",
})

_WEATHER_FAST_PATH = {
    # keyword → (tool_name, label)
    "pollen":    ("get_pollen_levels",   "Pollenwerte Karlsruhe"),
    "allergie":  ("get_pollen_levels",   "Pollenwerte Karlsruhe"),
    "allergy":   ("get_pollen_levels",   "Pollenwerte Karlsruhe"),
    "uv":        ("get_uv_index",        "UV-Index Karlsruhe"),
}


def _weather_fast_path(query: str, progress_cb=None) -> str | None:
    """Bypass LLM planning for pure weather/pollen/UV questions.

    Returns a FetchingAgent-compatible JSON string if the query is a simple
    weather question, or None if it needs full LLM planning.
    """
    import json as _json
    from ui.shared import call_tool

    q = query.lower()
    if not any(kw in q for kw in _WEATHER_KEYWORDS):
        return None

    # Determine which tool(s) to call
    steps = []
    if any(kw in q for kw in ("pollen", "allergie", "allergy")):
        steps.append(("get_pollen_levels", "Pollenwerte Karlsruhe"))
    if any(kw in q for kw in ("uv", "uv-index", "uvindex")):
        steps.append(("get_uv_index", "UV-Index Karlsruhe"))
    if not steps:
        steps.append(("get_current_weather", "Aktuelles Wetter Karlsruhe"))

    if progress_cb:
        try:
            tool_names = ", ".join(s[0] for s in steps)
            progress_cb(f"Fast-Path: {tool_names}")
        except Exception:
            pass

    results = []
    for tool_name, label in steps:
        try:
            raw = call_tool(tool_name, {})
            results.append({"tool": tool_name, "label": label, "result": raw})
        except Exception as e:
            results.append({"tool": tool_name, "label": label, "error": str(e)})

    return _json.dumps({
        "reasoning":            f"Fast-path: weather query detected, skipped LLM planner.",
        "clarification_needed": False,
        "clarification_question": "",
        "steps":                [{"tool": t, "args": {}, "label": l} for t, l in steps],
        "results":              results,
        "data_summary":         f"{len(results)} weather tool(s) called directly",
        "key_findings":         [],
        "total_ms":             0,
    })


def _get_cached_tools():
    """Return (tools, tool_desc, valid_names) — built once per process, never rebuilt."""
    from ui.shared import get_all_openai_tools
    if not hasattr(_get_cached_tools, "_cache"):
        tools = get_all_openai_tools()
        _get_cached_tools._cache = (
            tools,
            _describe_tools(tools),
            {t["function"]["name"] for t in tools},
        )
    return _get_cached_tools._cache


def call_sync(query: str, today: str, history: list = None, progress_cb=None) -> str:
    """Callable directly in-process by the orchestrator (bypasses MCP transport)."""
    from ui.shared import call_tool

    # ── Fast-path: weather/pollen/UV queries skip LLM planning entirely ────────
    fast = _weather_fast_path(query, progress_cb)
    if fast is not None:
        return fast

    tools, tool_desc, valid_names = _get_cached_tools()

    # ── Plan ──────────────────────────────────────────────────────────────────
    system = _PLANNER_SYSTEM.format(
        today=today, max_steps=MAX_STEPS, tool_descriptions=tool_desc
    )
    raw  = llm_call(system, query, json_mode=True, history=history)
    plan = extract_json(raw)

    # Unparseable planner output → ask the user to rephrase
    if not plan:
        return json.dumps({
            "reasoning":              "Could not parse the planning response.",
            "clarification_needed":   True,
            "clarification_question": "I had trouble understanding that query — could you rephrase it?",
            "results":                [],
            "data_summary":           "no data fetched",
            "total_ms":               0,
        })

    reasoning              = plan.get("reasoning", "")
    clarification_needed   = bool(plan.get("clarification_needed", False))
    clarification_question = plan.get("clarification_question", "").strip()
    raw_steps: List[Dict]  = (plan.get("steps") or [])[:MAX_STEPS]

    # Guard: clarification requested but question missing
    if clarification_needed and not clarification_question:
        clarification_question = "Could you be more specific about what you'd like to see?"

    # Surface plan as a short readable status (not the full GOAL/DATA/TOOLS block)
    if progress_cb and raw_steps:
        try:
            tool_names = ", ".join(s.get("tool", "?") for s in raw_steps[:4])
            suffix = f" +{len(raw_steps)-4} mehr" if len(raw_steps) > 4 else ""
            progress_cb(f"Plan: {len(raw_steps)} Tool-Call(s) — {tool_names}{suffix}")
        except Exception:
            pass
    elif progress_cb and clarification_needed:
        try:
            progress_cb("Brauche mehr Infos…")
        except Exception:
            pass

    # ── Validate planned steps against real tool names ─────────────────────────
    # This catches invented or misspelled tool names before execution, rather than
    # letting them fail silently and leaving downstream agents with no data.
    steps: List[Dict]   = []
    phantom_results: List[Dict] = []
    for s in raw_steps:
        name = s.get("tool", "")
        if name in valid_names:
            steps.append(s)
        else:
            phantom_results.append({
                "label":          s.get("label", name),
                "tool":           name,
                "args":           s.get("args") or {},
                "result":         f"Tool '{name}' does not exist. Check available tools.",
                "result_summary": f"Tool '{name}' does not exist.",
                "duration_ms":    0,
                "error":          f"Unknown tool: '{name}'",
            })

    if not steps:
        data_summary = (
            "no data fetched"
            if not phantom_results
            else "plan contained only invalid tool names — " +
                 ", ".join(p["tool"] for p in phantom_results)
        )
        return json.dumps({
            "reasoning":              reasoning,
            "clarification_needed":   clarification_needed,
            "clarification_question": clarification_question,
            "results":                phantom_results,
            "data_summary":           data_summary,
            "total_ms":               0,
        })

    # ── Execute in parallel ───────────────────────────────────────────────────
    t0        = time.perf_counter()
    results: List[Dict] = list(phantom_results)
    total     = len(steps)
    completed = 0

    def _run_one(step: Dict) -> Dict:
        ts    = time.perf_counter()
        tool  = step.get("tool", "")
        args  = step.get("args") or {}
        label = step.get("label", tool)
        try:
            result_text = call_tool(tool, args)
            error = None
        except Exception as exc:
            from servers.agents._base import sanitize_error
            safe_msg = sanitize_error(str(exc))
            result_text = f"Error: {safe_msg}"
            error = safe_msg
        trunc_size = 5000 if tool in _LIST_TOOLS else 2000
        return {
            "label":          label,
            "tool":           tool,
            "args":           args,
            "result":         result_text,
            "result_summary": truncate(result_text, trunc_size),
            "duration_ms":    int((time.perf_counter() - ts) * 1000),
            "error":          error,
        }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_run_one, s): s for s in steps}
        for future in as_completed(futures):
            try:
                results.append(future.result(timeout=TIMEOUT_S))
            except Exception as exc:
                s = futures[future]
                results.append({
                    "label": s.get("label", "?"), "tool": s.get("tool", ""),
                    "args": s.get("args") or {}, "result": f"Timeout: {exc}",
                    "result_summary": f"Timeout: {exc}",
                    "duration_ms": TIMEOUT_S * 1000, "error": str(exc),
                })
            completed += 1
            if progress_cb:
                try:
                    done_label = futures[future].get("label", "source")
                    progress_cb(f"Retrieved: {done_label} ({completed}/{total})")
                except Exception:
                    pass

    # ── Optional refinement pass ──────────────────────────────────────────────
    # Only refine when an initial result comes from a list tool (one that returns
    # activity IDs that can unlock a more specific follow-up call). Point-in-time
    # or aggregate tools are self-contained and never need a second pass.
    _should_refine = any(
        r["tool"] in _REFINABLE_TOOLS and not r.get("error")
        for r in results
    )
    if not clarification_needed and _should_refine:
        refine_steps = _plan_refinement(
            query, today, reasoning, results, valid_names, tools
        )
        if refine_steps:
            if progress_cb:
                try:
                    progress_cb(f"Fetching details ({len(refine_steps)} follow-up call(s))…")
                except Exception:
                    pass
            total += len(refine_steps)
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures2 = {pool.submit(_run_one, s): s for s in refine_steps}
                for future in as_completed(futures2):
                    try:
                        results.append(future.result(timeout=TIMEOUT_S))
                    except Exception as exc:
                        s = futures2[future]
                        results.append({
                            "label": s.get("label", "?"), "tool": s.get("tool", ""),
                            "args": s.get("args") or {}, "result": f"Timeout: {exc}",
                            "result_summary": f"Timeout: {exc}",
                            "duration_ms": TIMEOUT_S * 1000, "error": str(exc),
                        })
                    completed += 1
                    if progress_cb:
                        try:
                            done_label = futures2[future].get("label", "detail")
                            progress_cb(f"Retrieved: {done_label} ({completed}/{total})")
                        except Exception:
                            pass

    # ── Dedup: drop by-name results superseded by more specific by-id calls ─────
    # When the initial plan called get_activity_streams(activity_name=...) and the
    # refinement pass added get_activity_streams(activity_id=X), discard the by-name
    # result so downstream agents only see the correct, specific data.
    results = _dedup_superseded(results)

    # ── Build concise data_summary for downstream agents ─────────────────────
    # Gives ChatAgent, VisualizationAgent, and FlyoverAgent a clean one-liner
    # of what was retrieved without having to parse raw JSON walls.
    ok_parts  = [r["label"] for r in results if not r.get("error")]
    err_parts = [
        f"{r['label']} ({_classify_error(r.get('error',''))})"
        for r in results if r.get("error")
    ]
    if ok_parts and err_parts:
        data_summary = f"retrieved: {', '.join(ok_parts)}; failed: {', '.join(err_parts)}"
    elif ok_parts:
        data_summary = f"retrieved: {', '.join(ok_parts)}"
    elif err_parts:
        data_summary = f"all sources failed: {', '.join(err_parts)}"
    else:
        data_summary = "no data fetched"

    key_findings = _extract_key_findings(results)

    return json.dumps({
        "reasoning":              reasoning,
        "clarification_needed":   clarification_needed,
        "clarification_question": clarification_question,
        "results":                results,
        "data_summary":           data_summary,
        "key_findings":           key_findings,
        "total_ms":               int((time.perf_counter() - t0) * 1000),
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify_error(error: str) -> str:
    """Bucket a raw error string into a short human-readable category."""
    e = error.lower()
    if "401" in e or "unauthorized" in e or "forbidden" in e:
        return "auth error"
    if "429" in e or "rate limit" in e or "too many" in e:
        return "rate limited"
    if "timeout" in e:
        return "timeout"
    if "not found" in e or "no activity" in e or "404" in e:
        return "not found"
    if "no data" in e or "empty" in e:
        return "no data"
    return "error"


def _dedup_superseded(results: List[Dict]) -> List[Dict]:
    """Remove by-name stream results when a by-id result for the same tool exists.

    If both get_activity_streams(activity_name=X) and get_activity_streams(activity_id=Y)
    appear in the results, the by-name result is likely wrong (most-recent, not the
    specific one the user asked for). Drop it so downstream agents see only the correct one.
    """
    # Collect tools that have at least one result with a specific activity_id
    id_tools: set = set()
    for r in results:
        if not r.get("error") and r.get("args", {}).get("activity_id"):
            id_tools.add(r["tool"])

    if not id_tools:
        return results

    filtered = []
    for r in results:
        tool = r.get("tool", "")
        if (tool in id_tools
                and not r.get("error")
                and r.get("args", {}).get("activity_name")
                and not r.get("args", {}).get("activity_id")):
            # This is a by-name result for a tool that also has a by-id result — drop it
            continue
        filtered.append(r)
    return filtered


def _plan_refinement(
    query: str,
    today: str,
    reasoning: str,
    results: List[Dict],
    valid_names: set,
    tools: list,
) -> List[Dict]:
    """Ask the LLM if any follow-up tool calls are needed given the initial results.

    Returns a validated list of additional steps (may be empty).
    """
    ok_results = [r for r in results if not r.get("error")]
    if not ok_results:
        return []

    results_block = "\n\n".join(
        f"[{r['label']}] via {r['tool']}:\n{r['result_summary']}"
        for r in ok_results
    )
    already_called = ", ".join(sorted({r["tool"] for r in results}))

    system = _REFINE_SYSTEM.format(
        today=today,
        query=query,
        reasoning=reasoning,
        results_block=results_block,
        already_called=already_called,
        tool_descriptions=_describe_tools(tools),
        max_steps=MAX_REFINE_STEPS,
    )

    try:
        raw  = llm_call(system, "Are follow-up calls needed?", json_mode=True)
        plan = extract_json(raw)
    except Exception:
        return []

    steps: List[Dict] = []
    for s in (plan.get("steps") or [])[:MAX_REFINE_STEPS]:
        if s.get("tool") in valid_names:
            steps.append(s)
    return steps


def _extract_key_findings(results: List[Dict]) -> List[str]:
    """Rule-based extraction of key facts from tool results for downstream agents."""
    findings = []
    for r in results:
        if r.get("error"):
            continue
        tool = r.get("tool", "")
        try:
            data = json.loads(r.get("result", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue

        if tool == "get_activities":
            acts = data.get("activities", [])
            n = data.get("total_count", len(acts))
            if acts:
                recent = acts[0]
                pace = recent.get("pace_display") or str(recent.get("pace_min_per_km", "?"))
                findings.append(
                    f"Strava activities: {n} fetched. Most recent: {recent.get('name','?')} "
                    f"({recent.get('date','?')}, {recent.get('distance_km','?')} km, pace {pace} /km). "
                    f"IDs available for follow-up calls."
                )

        elif tool == "get_garmin_activities":
            acts = data.get("activities", [])
            n = data.get("total", len(acts))
            if acts:
                recent = acts[0]
                pace = recent.get("pace_display") or str(recent.get("pace_min_per_km", "?"))
                findings.append(
                    f"Garmin activities: {n} fetched. Most recent: {recent.get('name','?')} "
                    f"({recent.get('date','?')}, {recent.get('distance_km','?')} km, pace {pace} /km)."
                )

        elif tool == "get_personal_bests":
            fastest = (data.get("top_5_fastest") or [{}])[0]
            longest = (data.get("top_5_by_distance") or [{}])[0]
            parts = []
            if fastest:
                pace = fastest.get("pace_display") or str(fastest.get("pace_min_per_km", "?"))
                parts.append(
                    f"Fastest: {fastest.get('name','?')} ({fastest.get('date','?')}, "
                    f"id={fastest.get('id','?')}, {pace} /km)"
                )
            if longest:
                parts.append(
                    f"Longest: {longest.get('name','?')} ({longest.get('date','?')}, "
                    f"id={longest.get('id','?')}, {longest.get('distance_km','?')} km)"
                )
            if parts:
                findings.append("Personal bests: " + "; ".join(parts))

        elif tool in ("get_activity_detail", "get_garmin_activity_detail"):
            name = data.get("name", "?")
            date = data.get("date", "?")
            dist = data.get("distance_km", "?")
            pace = data.get("pace_display") or str(data.get("pace_min_per_km", "?"))
            avg_hr = data.get("avg_hr") or data.get("avg_heart_rate_bpm")
            detail = f"{name} ({date}, {dist} km"
            if pace and pace not in ("None", "?"):
                detail += f", {pace} /km"
            if avg_hr:
                detail += f", avg HR {avg_hr} bpm"
            detail += ")"
            findings.append(f"Activity detail: {detail}")

        elif tool == "get_activity_streams":
            act = data.get("activity", {})
            if act.get("name"):
                pace = act.get("pace_display") or "?"
                findings.append(
                    f"Streams: {act.get('name','?')} ({act.get('date','?')}, "
                    f"{act.get('distance_km','?')} km, {pace} /km). "
                    f"{data.get('total', 0)} GPS points."
                )

        elif tool == "get_garmin_sleep":
            score = data.get("sleep_score")
            total = data.get("total_sleep_h")
            if total:
                findings.append(
                    f"Sleep ({data.get('date','?')}): {total}h total"
                    + (f", score {score}" if score else "")
                )

        elif tool == "get_garmin_daily_health":
            parts = []
            if data.get("steps"):
                parts.append(f"steps {data['steps']}")
            if data.get("resting_hr"):
                parts.append(f"resting HR {data['resting_hr']}")
            if data.get("body_battery_max"):
                parts.append(f"body battery max {data['body_battery_max']}")
            if parts:
                findings.append(f"Daily health ({data.get('date','?')}): " + ", ".join(parts))

        elif tool == "get_garmin_hrv_status":
            hrv = data.get("last_night_hrv")
            status = data.get("status")
            if hrv or status:
                findings.append(
                    f"HRV ({data.get('date','?')}): "
                    + (f"{hrv} ms" if hrv else "")
                    + (f", status: {status}" if status else "")
                )

        elif tool == "get_garmin_training_metrics":
            vo2 = data.get("vo2max_running")
            load = data.get("training_load_7d")
            status = data.get("training_status")
            parts = []
            if vo2:
                parts.append(f"VO2max {vo2}")
            if load:
                parts.append(f"7d load {load}")
            if status:
                parts.append(f"status: {status}")
            if parts:
                findings.append(f"Training metrics: " + ", ".join(parts))

        elif tool == "get_garmin_wellness_trends":
            s      = data.get("summary") or {}
            days_n = data.get("days", 0)
            parts  = []
            if s.get("avg_sleep_score"):
                parts.append(f"avg sleep score {s['avg_sleep_score']:.0f}")
            if s.get("avg_total_sleep_h"):
                parts.append(f"avg sleep {s['avg_total_sleep_h']:.1f}h")
            if s.get("avg_deep_h"):
                parts.append(f"avg deep {s['avg_deep_h']:.1f}h")
            if s.get("avg_rem_h"):
                parts.append(f"avg REM {s['avg_rem_h']:.1f}h")
            if s.get("avg_steps"):
                parts.append(f"avg steps {int(s['avg_steps']):,}")
            if s.get("avg_stress"):
                parts.append(f"avg stress {s['avg_stress']:.0f}")
            if s.get("avg_resting_hr"):
                parts.append(f"avg resting HR {s['avg_resting_hr']:.0f}")
            if parts:
                findings.append(f"Wellness trends ({days_n}d): " + ", ".join(parts))

        elif tool == "get_garmin_stress_timeline":
            avg = data.get("avg_stress")
            mx  = data.get("max_stress")
            mxt = data.get("max_stress_time")
            if avg or mx:
                parts = []
                if avg:
                    parts.append(f"avg {avg}")
                if mx and mxt:
                    parts.append(f"peak {mx} at {mxt}")
                elif mx:
                    parts.append(f"peak {mx}")
                findings.append(f"Stress ({data.get('date','?')}): " + ", ".join(parts))

        elif tool == "get_garmin_body_composition":
            latest = data.get("latest") or {}
            if latest.get("weight_kg"):
                parts = [f"{latest['weight_kg']:.1f} kg ({latest.get('date','?')})"]
                if latest.get("body_fat_pct"):
                    parts.append(f"body fat {latest['body_fat_pct']:.1f}%")
                if data.get("trend_kg") is not None:
                    trend = data["trend_kg"]
                    parts.append(f"trend {trend:+.1f} kg over period")
                findings.append("Body composition: " + ", ".join(parts))

    return findings


def _describe_tools(tools: list) -> str:
    lines = []
    for t in tools:
        fn    = t["function"]
        props = fn.get("parameters", {}).get("properties", {})
        param_str = ", ".join(
            f'{k} ({v.get("type","any")}): {v.get("description","")}'
            for k, v in props.items()
        ) or "none"
        lines.append(f'- {fn["name"]}: {fn["description"]}\n  params: {param_str}')
    return "\n".join(lines)


# ── Standalone MCP server entry point ────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
