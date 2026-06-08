"""VisualizationAgent — selects which charts to render from fetched data.

Multi-agent role: visualization curator.
  - Receives the user query and the FetchingAgent's structured results
  - Uses an LLM to decide which tool results deserve a chart and in what priority
  - Cross-references against the viz registry (ui.viz) so only renderable tools
    are proposed
  - Returns a ranked list of viz actions that the UI will render inline

Exposed as MCP tool:       plan_visualizations(query, data_results)
Callable in-process via:   call_sync(query, data_results)

Standalone usage:
    python servers/agents/visualization.py
"""

import json

from mcp.server.fastmcp import FastMCP

from servers.agents._base import llm_call, truncate, extract_json

mcp = FastMCP(
    "VisualizationAgent",
    instructions="Selects the most relevant charts to render from fetched fitness data.",
)

_SYSTEM = """\
ROLE: You are VisualizationAgent — Phase 2a of 4, running in parallel with FlyoverAgent.
Charts you select render automatically below the ChatAgent's final answer.
Your only job is curation: decide which fetched results deserve a visual.

CORE PRINCIPLE — less is more:
  A single focused chart is better than several marginal ones.
  When in doubt, return fewer charts or none at all.
  The ChatAgent's text answer already contains the numbers — a chart must add
  genuine visual insight (trend shape, pattern, distribution) to be worth including.

WHEN TO RETURN ZERO CHARTS:
  • The query is a calculation, average, or two-value comparison fully answered
    by the text (e.g. "compare average deep sleep last week vs the week before" —
    the comparison IS the answer; individual night breakdowns add nothing).
  • The query asks for a single scalar ("what is my VO₂max today?").
  • All fetched results have errors or no data.

HOW MANY CHARTS TO SELECT:
  • Comparison / average / trend query: 0–1 charts (one aggregate view max).
  • Single-day or single-activity query: 1 chart.
  • Explicit multi-metric exploration ("show everything about my run"): up to 2.
  • Hard cap: 4 — only for genuinely multi-dimensional, exploratory queries.

AGGREGATION RULE — always prefer the aggregate:
  When the fetched results include both a get_garmin_wellness_trends result AND
  multiple per-day results from the same metric (e.g. 7× get_garmin_sleep,
  14× get_garmin_daily_health), ALWAYS select the wellness_trends aggregate and
  ignore the individual per-day results. The aggregate communicates the trend;
  per-day breakdowns just repeat the same chart N times.
  Only select individual per-day results when no aggregate is available AND the
  query targets a specific named day or specific single event.

METRIC FOCUS — required for get_garmin_wellness_trends:
  The wellness_trends renderer shows five sub-charts by default (sleep stages,
  body battery, heart rate, steps, stress). You MUST set "metric_focus" to show
  only what the query is about. Use "" only for broad health-overview queries.
    "sleep"        → only sleep stages
    "stress"       → only stress bars
    "body_battery" → only body battery
    "heart_rate"   → only resting/max HR trend
    "steps"        → only daily steps
    ""             → all sub-charts (broad overview only)

MUST DO:
  • Only select tool names from the Renderable Tools list below — no exceptions.
  • Set metric_focus on every get_garmin_wellness_trends action.
  • Use the [index] to identify the correct result when multiple exist.
  • Rank by relevance: the chart most directly answering the query gets priority 1.

MUST NOT DO:
  • Never propose a tool not in the Renderable Tools list.
  • Never propose a chart for a result that has an error or empty data.
  • Never select multiple per-day results (e.g. 4× get_garmin_sleep) for a
    trend, average, or comparison query — select 0 or use wellness_trends instead.
  • Never exceed 4 charts.

Renderable tools (select ONLY from this list):
  get_garmin_wellness_trends    — multi-day aggregate (PREFERRED for trend/comparison queries)
  get_garmin_sleep              — sleep stages for ONE specific night (single-day only)
  get_garmin_body_battery       — Body Battery charge curve for one day
  get_garmin_hrv_status         — HRV readiness gauge
  get_garmin_daily_health       — steps, stress, and intensity for one day
  get_garmin_heart_rate_timeline — full-day HR timeline
  get_garmin_steps_timeline     — intraday step counts
  get_garmin_stress_timeline    — intraday stress levels
  get_garmin_training_metrics   — VO₂max, training load, training status
  get_garmin_body_composition   — weight and body composition trend
  get_activities                — Strava activity list chart
  get_garmin_activities         — Garmin activity list chart
  get_activity_streams          — GPS route map + elevation/HR profile
  get_activity_stats            — overall stats breakdown
  get_training_trends           — weekly training volume over time
  get_yearly_breakdown          — year-over-year comparison chart
  get_personal_bests            — personal best comparison

OUTPUT — reply ONLY with valid JSON:
{
  "viz_actions": [
    {"index": <int>, "tool": "<tool_name>", "label": "<short label>", "priority": 1, "metric_focus": ""},
    ...
  ]
}
Return {"viz_actions": []} when charts would not add insight beyond what the text already provides.
"""

MAX_CHARTS = 4

_METRIC_FOCUS_KEYWORDS: dict = {
    "sleep":        ["sleep", "deep sleep", "rem", "light sleep", "awake", "bedtime", "nap", "wake"],
    "stress":       ["stress", "tense", "anxiety", "anxious", "relax"],
    "body_battery": ["body battery", "bodybattery", "energy level", "battery charge"],
    "heart_rate":   ["heart rate", "resting hr", " rhr ", "bpm", "cardiac", "pulse"],
    "steps":        ["steps", "step count", "walked", "walking", "daily steps"],
}


def _infer_metric_focus(query: str) -> str:
    """Keyword-based fallback: infer the metric_focus from the user query."""
    q = query.lower()
    for focus, keywords in _METRIC_FOCUS_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return focus
    return ""


# ── MCP tool ──────────────────────────────────────────────────────────────────

@mcp.tool()
def plan_visualizations(query: str, data_results: str) -> str:
    """Select which charts to render from fetched fitness data.

    Args:
        query:        The user's original question.
        data_results: JSON from FetchingAgent.call_sync() containing results list.

    Returns:
        JSON string: {viz_actions: [{type, tool, label, result}]}
    """
    return call_sync(query, data_results)


# ── In-process entry point ────────────────────────────────────────────────────

def call_sync(query: str, data_results: str) -> str:
    """Callable directly in-process by the orchestrator."""
    try:
        fetch = json.loads(data_results)
        results = fetch.get("results") or []
        reasoning = fetch.get("reasoning", "")
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"viz_actions": []})

    if not results:
        return json.dumps({"viz_actions": []})

    # Build renderable list — unique by (tool, label) so multi-day queries
    # (e.g. sleep Monday + sleep Tuesday) retain all distinct results.
    seen_keys: set = set()
    renderable_list = []
    for r in results:
        t     = r.get("tool", "")
        label = r.get("label", "")
        key   = (t, label)
        if not r.get("error") and _is_renderable(t) and key not in seen_keys:
            seen_keys.add(key)
            renderable_list.append(r)

    if not renderable_list:
        return json.dumps({"viz_actions": []})

    # Fast path: 1–2 renderable results — skip the LLM, render all of them.
    if len(renderable_list) <= 2:
        actions = []
        for r in renderable_list:
            a: dict = {"type": "viz", "tool": r["tool"], "label": r["label"], "result": r["result"]}
            if r["tool"] == "get_garmin_wellness_trends":
                mf = _infer_metric_focus(query)
                if mf:
                    a["metric_focus"] = mf
            actions.append(a)
        return json.dumps({"viz_actions": actions})

    # LLM path: 3+ charts — ask the LLM to rank by relevance.
    # Include index numbers so the LLM can reference the right result when
    # multiple results from the same tool exist.
    available_summary = "\n".join(
        f"[{i}] {r['tool']} ({r['label']}): {truncate(r.get('result_summary',''), 300)}"
        for i, r in enumerate(renderable_list)
    )
    reasoning_line = f"\nFetching reasoning: {reasoning}\n" if reasoning else ""
    user_msg = (
        f"User query: {query}"
        f"{reasoning_line}"
        f"\nFetched renderable data:\n{available_summary}"
    )

    raw  = llm_call(_SYSTEM, user_msg, json_mode=True)
    plan = extract_json(raw)

    viz_actions = []
    seen_indices: set = set()
    for va in sorted(
        plan.get("viz_actions") or [],
        key=lambda x: x.get("priority", 99),
    )[:MAX_CHARTS]:
        # Prefer index-based matching; fall back to tool+label search
        idx = va.get("index")
        if isinstance(idx, int) and 0 <= idx < len(renderable_list) and idx not in seen_indices:
            r = renderable_list[idx]
            seen_indices.add(idx)
        else:
            tool  = va.get("tool", "")
            label = va.get("label", "")
            r = next(
                (x for x in renderable_list
                 if x["tool"] == tool and (not label or x["label"] == label)
                 and renderable_list.index(x) not in seen_indices),
                None,
            )
            if r is None:
                r = next(
                    (x for x in renderable_list
                     if x["tool"] == tool and renderable_list.index(x) not in seen_indices),
                    None,
                )
            if r:
                seen_indices.add(renderable_list.index(r))

        if r:
            action: dict = {
                "type":   "viz",
                "tool":   r["tool"],
                "label":  va.get("label", r["label"]),
                "result": r["result"],
            }
            if r["tool"] == "get_garmin_wellness_trends":
                mf = va.get("metric_focus") or _infer_metric_focus(query)
                if mf:
                    action["metric_focus"] = mf
            viz_actions.append(action)

    return json.dumps({"viz_actions": viz_actions})


def _is_renderable(tool_name: str) -> bool:
    """Check against the viz registry without importing Streamlit."""
    _RENDERABLE = {
        "get_garmin_wellness_trends", "get_garmin_sleep", "get_garmin_body_battery",
        "get_garmin_hrv_status", "get_garmin_daily_health", "get_garmin_heart_rate_timeline",
        "get_garmin_steps_timeline", "get_garmin_stress_timeline", "get_garmin_training_metrics",
        "get_garmin_body_composition",
        "get_activities", "get_garmin_activities", "get_activity_streams",
        "get_activity_stats", "get_training_trends", "get_yearly_breakdown", "get_personal_bests",
    }
    return tool_name in _RENDERABLE


# ── Standalone MCP server entry point ────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
