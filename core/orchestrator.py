"""Tool-agnostic orchestration — the runtime engine the app talks to.

ONE native tool-use loop: the model (via core.llm) gets the tools discovered from
the MCP servers (via core.host.ToolHost) and decides which to call. No code names a
tool. This replaces the old planner + 4-agent pipeline.

Drop-in for the UI: exposes ``FitDashOrchestrator.run(user_input, history, cb)``
returning ``(answer, trace)`` — the trace is shaped for the existing Streamlit debug
panel and route-map renderer, so the app keeps working on the new engine.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from types import SimpleNamespace

from core.config import SEP
from core.host import ToolHost
from core.llm import get_llm_client

LOG_DIR = Path(".logs")
LOG_FILE = LOG_DIR / "agent_interactions.jsonl"
LLM_LOG_FILE = LOG_DIR / "llm_calls.jsonl"

MAX_ROUNDS = 12
HISTORY_WINDOW = 16          # message pairs kept in context
HISTORY_CHAR_LIMIT = 2500    # chars per history message before truncation
LARGE_ARRAY_KEYS = {"points", "waypoints", "segments", "timeline", "buckets_15min", "trails", "instructions"}
ROUTE_TOOLS = {"plan_route", "plan_circular_route", "explore_trails", "get_isochrone",
               "get_activity_streams", "get_activity_gps_track"}
# Keys always preserved verbatim in compact_list_item regardless of string length
_ALWAYS_KEEP_KEYS = {"id", "name", "date", "type", "sport_type", "start_date"}

# Tool results are cached in the orchestrator instance for this many seconds.
# Covers multi-turn conversations without re-fetching stable data (activities, PBs…).
_TOOL_CACHE_TTL = 300.0
# These tools must always hit the network — time-sensitive or have side effects.
_NO_CACHE_TOOLS = frozenset({
    "delete_activity", "import_activity",
    "get_current_weather", "get_weather_forecast",
    "get_pollen_levels", "get_uv_index",
})
# Pattern the model uses to embed chart suggestions at the end of its answer.
_CHART_TAG_RE = re.compile(r'<!--charts:\s*(.+?)-->', re.IGNORECASE | re.DOTALL)


_SYSTEM = """\
You are Training Copilot, an AI sports analyst. Today is {today}.
Home location: Karlsruhe, Germany (49.0069°N, 8.4037°E).

CORE RULE: You have tools that fetch REAL data. Always use them for any question about
fitness, health, weather, routes, or calendar. Never guess, estimate, or invent numbers.
Only skip tools for pure small-talk with no fitness angle ("hello", "what are you?").

ANY question that could be answered better with real data MUST use tools first — including
open-ended, subjective, or identity questions like:
  "Am I a good runner?" → fetch activities + personal bests + training trends, then evaluate
  "What's interesting about me?" → fetch activities + wellness + personal bests, then synthesise
  "How am I doing lately?" → fetch recent runs + health data, then answer with real numbers
  "Should I run today?" → fetch HRV + body battery + sleep + weather, then advise
Never give a generic opinion-based answer when real data is available and relevant.

══════════════════════════════════════════════════════════════
EXECUTE IMMEDIATELY — NEVER ASK PERMISSION
══════════════════════════════════════════════════════════════
Call tools NOW. Never say:
• "Shall I go ahead and fetch it?"
• "Would you like me to proceed?"
• "Should I pull that data for you?"
• "I'll fetch that now — shall I continue?"
The user already asked the question. That IS the permission.
Multi-step queries: chain tool calls across rounds automatically.
  Round 1: find the activity (get_activities / get_garmin_activities)
  Round 2: fetch the detail (get_activity_detail / get_garmin_activity_detail / get_activity_streams)
Never stop between rounds to ask.

══════════════════════════════════════════════════════════════
PARALLEL TOOL CALLS — THE MOST IMPORTANT RULE
══════════════════════════════════════════════════════════════
When a question needs multiple independent data sources, call ALL required tools in
ONE round — they execute in parallel (no extra time cost).
Correct: recovery query → round 1: [HRV, body_battery, sleep] → round 2: synthesise
Wrong:   round 1: HRV → round 2: body_battery → round 3: sleep (wastes 2 rounds)

Other parallel examples:
• Weekly check-in → wellness_trends + training_trends simultaneously
• Marathon readiness → vo2max + training_load + personal_bests simultaneously
• Good day to train? → HRV + body_battery + sleep + weather simultaneously

══════════════════════════════════════════════════════════════
ACTIVITY DATA — SOURCE PRIORITY
══════════════════════════════════════════════════════════════
PRIMARY: strava__get_activities — use for ALL activity questions (runs, rides, hikes,
  elevation, pace, distance, training history). Includes Garmin-recorded activities
  that auto-synced to Strava.
FALLBACK: garmin__get_garmin_activities — ONLY when strava__get_activities returns a
  completely EMPTY list (zero activities returned, not filtered). If Strava returns
  activities of a different sport type than requested, report that fact — do NOT also
  call Garmin activities as a "double-check". NEVER call both. They contain identical
  workouts; calling both gives contradictory results and wastes tool budget.
  Exception: garmin__get_garmin_activities is used as part of the Garmin detail lookup
  chain (find Garmin ID by date → then get_garmin_activity_detail or get_activity_gps_track).
GARMIN HEALTH (always): get_sleep, get_body_battery, get_hrv_status, get_stress_timeline,
  get_steps_timeline, get_daily_health, get_wellness_trends — these are Garmin-exclusive,
  call them freely for all wellness/recovery/health questions.

══════════════════════════════════════════════════════════════
TOOL SELECTION — match the exact question type
══════════════════════════════════════════════════════════════

HEALTH / RECOVERY (Garmin only — never use Strava for these):
• "Sleep / how did I sleep?"        → garmin__get_garmin_sleep
• "Steps / active today?"           → garmin__get_garmin_steps_timeline
• "Stress / stressed?"              → garmin__get_garmin_stress_timeline
• "Body Battery / energy"           → garmin__get_garmin_body_battery
• "HRV / recovered?"                → garmin__get_garmin_hrv_status
• "VO2max / race predictions"       → garmin__get_garmin_training_metrics
• "Wellness / week overview"        → garmin__get_garmin_wellness_trends
• "Recovery / should I rest?"       → ONE round: [hrv_status + body_battery + sleep]
• "Daily health summary"            → garmin__get_garmin_daily_health

TRAINING LOAD & TRENDS (Strava):
• "Training load / overtraining / form / TSB" → strava__get_training_load
• "Weekly volume / consistency"     → strava__get_training_trends
• "Pace trend / progress"           → strava__analyze_performance_trends
• "All-time stats / totals"         → strava__get_activity_stats
• "Personal bests / records"        → strava__get_personal_bests
• "Year-over-year"                  → strava__get_yearly_breakdown
• "Gear / shoes / bike mileage"     → strava__get_gear_info

ACTIVITY DETAIL — choose by what the user asks:
• "Lap splits / splits per km"      → Round 1: strava__get_activities → Round 2: strava__get_activity_detail
• "HR zones of a run/ride"          → Round 1: garmin__get_garmin_activities → Round 2: garmin__get_garmin_activity_detail
  (Strava does NOT provide HR zone breakdowns — skip strava for zone queries entirely)
  DO NOT ask permission between rounds — execute Round 2 automatically after Round 1 returns.
• "Cadence / power per lap"         → Round 1: garmin__get_garmin_activities → Round 2: garmin__get_garmin_activity_detail
  (Garmin has per-lap cadence; Strava only has cadence in raw streams)
  DO NOT also call strava__get_activities — Garmin has the same activity, use it directly.
  DO NOT ask permission between rounds — execute Round 2 automatically after Round 1 returns.
• "GPS map / route / elevation profile / track" →
  Step 1: strava__get_activities or strava__get_activity_streams(activity_name=...) to find the activity
  Step 2: strava__get_activity_streams(activity_id=...) — preferred (gives HR-colored map)
  Step 3 fallback: if Strava returns 404/error → garmin__get_garmin_activities to find the Garmin ID
          → garmin__get_activity_gps_track(activity_id=...) for the GPS track
  NEVER skip steps 2/3 and claim a map is shown. Always actually fetch GPS data first.
• "How hard was this activity?"     → strava__compare_activity_to_baseline

GPS RULE — never claim a map is shown without fetching GPS:
  Finding an activity ID is NOT enough. You MUST call get_activity_streams or
  get_activity_gps_track BEFORE saying "see the map below".

WEATHER (never guess — always use weather tools):
• Forecast / will it rain?          → weather__get_weather_forecast
• Good to run?                      → weather forecast + advise: 5–20°C ideal, <30% rain ok
• UV / pollen                       → weather__get_uv_index or weather__get_pollen_levels
• If weather tools unavailable: tell the user, do NOT substitute Garmin/Strava data.

ROUTES:
• A→B route                         → routes__plan_route (lat/lon required; estimate from name)
• Circular loop / X km run          → routes__plan_circular_route (default home: 49.0069, 8.4037)
• Find trails nearby                → routes__explore_trails
• Reachable area in N min           → routes__get_isochrone
• If routes tools unavailable: tell the user, do NOT plan routes from memory.

══════════════════════════════════════════════════════════════
ERROR RECOVERY
══════════════════════════════════════════════════════════════
• 404 on streams/detail → evict and try the NEXT most-recent activity (never retry same ID)
• 429 Strava rate limit → stop all Strava calls; use Garmin fallback or inform user
• Tool error → try alternative tool/approach; never return a bare error as final answer
• HR zones / cadence on Strava detail with no data → fall back to Garmin immediately

══════════════════════════════════════════════════════════════
ACTIVITY IDs — never ask the user
══════════════════════════════════════════════════════════════
IDs are internal. When an action requires an activity_id you don't have from this
turn, call strava__get_activities silently to resolve it first. Never ask the user
to supply or confirm an ID.

══════════════════════════════════════════════════════════════
DESTRUCTIVE ACTIONS
══════════════════════════════════════════════════════════════
strava__delete_activity is permanent and irreversible.
1. First call get_activities(limit=5) to confirm the activity name + date.
2. Ask: "Permanently delete '[name]' ([date])? Reply 'yes' to confirm."
3. ONLY call delete after explicit user confirmation. Never in the same turn.

══════════════════════════════════════════════════════════════
ANSWER QUALITY
══════════════════════════════════════════════════════════════
• Compute absolute dates yourself — never pass "last Friday" to tools, use YYYY-MM-DD.
• Synthesise data into insights: don't dump raw lists. Lead with the key finding.
• Be precise with numbers: "7.2 h sleep, score 85" not "you had good sleep".
• A chart is generated automatically after your answer — say "see the chart below" when relevant.
• Route maps render automatically when you call GPS/route tools — say "see the map below" only
  after successfully calling get_activity_streams, get_activity_gps_track, or a route tool.
• Answer in the user's language.
• If data is missing / not found: say so clearly, don't fabricate.
• For "no matching activity" answers: give a clear negative ("I couldn't find a marathon
  in your Strava history for 2020") — don't show activity lists as consolation.

ELEVATION:
• elevation_gain_m = total vertical meters CLIMBED (e.g. 500 m of uphill)
• elevation_high_m = highest GPS altitude above sea level (e.g. 2300 m asl)
Highest altitude/summit → sort by elevation_high_m
Most climbing/steepest → sort by elevation_gain_m

══════════════════════════════════════════════════════════════
CHART HINTS — append to your final answer when useful
══════════════════════════════════════════════════════════════
If a chart would meaningfully illustrate your conclusion, add ONE invisible tag on
the very last line of your response (after all prose):
  <!--charts: description 1 | description 2-->
Examples:
  <!--charts: weekly running distance bar chart | cumulative km this year line-->
  <!--charts: pace per run scatter over last 3 months-->
  <!--charts: heart rate zone distribution pie chart-->
Rules: max 2 chart descriptions, each 3–8 words. Skip the tag entirely when no
chart adds value (conversational answers, route maps, pure health summaries)."""


class FitDashOrchestrator:
    """Stateless tool-use engine. Create once (st.cache_resource), call run() per turn."""

    def __init__(self, host: Optional[ToolHost] = None) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        self.host = host or ToolHost()
        self._tools: Optional[List[Dict]] = None
        # Cross-turn tool result cache keyed by "tool_name:arguments_json"
        self._tool_cache: Dict[str, Tuple[str, float]] = {}

    def _cache_get(self, tool_name: str, arguments: str) -> Optional[str]:
        bare = tool_name.split(SEP, 1)[-1]
        if bare in _NO_CACHE_TOOLS:
            return None
        entry = self._tool_cache.get(f"{tool_name}:{arguments}")
        if entry and time.time() - entry[1] < _TOOL_CACHE_TTL:
            return entry[0]
        return None

    def _cache_set(self, tool_name: str, arguments: str, result: str) -> None:
        bare = tool_name.split(SEP, 1)[-1]
        if bare not in _NO_CACHE_TOOLS:
            self._tool_cache[f"{tool_name}:{arguments}"] = (result, time.time())

    def _cache_evict_expired(self) -> None:
        now = time.time()
        self._tool_cache = {k: v for k, v in self._tool_cache.items()
                            if now - v[1] < _TOOL_CACHE_TTL}

    def _discover(self) -> List[Dict]:
        if not self._tools:
            # Discover tools on first call or if a previous discovery returned nothing
            # (can happen when servers restart mid-session).
            self._tools = self.host.list_tools()
        return self._tools

    def refresh_tools(self) -> int:
        """Force re-discovery of tools from all servers. Returns the new tool count."""
        self._tools = self.host.list_tools()
        return len(self._tools)

    def run(
        self,
        user_input: str,
        history: List[Dict],
        progress_cb: Optional[Callable[[str], None]] = None,
        text_cb: Optional[Callable[[Optional[str]], None]] = None,
    ) -> Tuple[str, Dict]:
        self._cache_evict_expired()
        client, model = get_llm_client()
        base_url = os.getenv("OPENAI_BASE_URL", "")
        today = datetime.now().strftime("%Y-%m-%d")

        trace: Dict[str, Any] = {
            "run_id": str(uuid.uuid4())[:8],
            "ts": datetime.utcnow().isoformat() + "Z",
            "user_input": user_input,
            "plan": None, "tool_calls": [], "answer": None,
            "timing": {}, "error": None, "actions": [], "agents": [],
        }

        tools = self._discover()
        messages: List[Dict[str, Any]] = [{"role": "system", "content": _SYSTEM.format(today=today)}]
        for m in (history or [])[-HISTORY_WINDOW:]:
            if m.get("role") in ("user", "assistant"):
                c = m.get("content") or ""
                messages.append({"role": m["role"], "content": c[:HISTORY_CHAR_LIMIT]})
        messages.append({"role": "user", "content": user_input})

        results: List[Dict[str, Any]] = []
        answer = ""
        t0 = time.perf_counter()

        def _call_one(tc):
            ts = time.perf_counter()
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            # ── Cross-turn result cache — avoids re-fetching stable data ──────
            cached = self._cache_get(tc.function.name, tc.function.arguments or "{}")
            if cached is not None:
                return tc, args, cached, 0  # 0 ms (served from cache)
            res = self.host.call_tool(tc.function.name, args)
            self._cache_set(tc.function.name, tc.function.arguments or "{}", res)
            return tc, args, res, int((time.perf_counter() - ts) * 1000)

        _forced_once = False      # prevent infinite retry loop
        _force_tools_next = False  # set True to use tool_choice="required" next round

        try:
            for rnd in range(MAX_ROUNDS):
                _cb(progress_cb, f"Phase {rnd + 1} — Model thinking…")
                current_tc = "required" if (_force_tools_next and tools) else ("auto" if tools else "none")
                _force_tools_next = False
                content, tcs = _llm_call(
                    client=client, model=model, messages=messages,
                    tools=tools or None, tool_choice=current_tc,
                    run_id=trace["run_id"], rnd=rnd, base_url=base_url,
                    timeout=90, text_cb=text_cb,
                )
                if not tcs:
                    answer = content
                    # ── Zero-tool enforcement ──────────────────────────────────
                    # If the model gave a generic answer without calling any tools
                    # for a personal data question, force one retry with real data.
                    if not _forced_once and len(results) == 0 and tools and _requires_data(user_input):
                        _forced_once = True
                        _force_tools_next = True
                        # Reset streamed text — the generic answer will be replaced
                        if text_cb:
                            try:
                                text_cb(None)
                            except Exception:
                                pass
                        messages.append({"role": "assistant", "content": answer})
                        messages.append({
                            "role": "user",
                            "content": (
                                "Your answer was generic — you didn't call any tools. "
                                "This question is about real personal fitness data. "
                                "Call the relevant tools now and give a specific, "
                                "data-driven answer with actual numbers."
                            ),
                        })
                        _cb(progress_cb, "⚠ No data fetched — retrying with tools…")
                        continue
                    break

                messages.append({
                    "role": "assistant", "content": content,
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in tcs
                    ],
                })
                cached_names = [tc.function.name for tc in tcs
                                if self._cache_get(tc.function.name, tc.function.arguments or "{}") is not None]
                fresh_names  = [tc.function.name for tc in tcs if tc.function.name not in cached_names]
                status_parts = []
                if fresh_names:  status_parts.append("Fetching: " + ", ".join(fresh_names))
                if cached_names: status_parts.append("💾 cached: " + ", ".join(cached_names))
                _cb(progress_cb, " · ".join(status_parts) or "Running tools…")

                # Execute all tool calls from this round in parallel — each ToolHost
                # call creates its own event loop so threads don't interfere.
                with ThreadPoolExecutor(max_workers=min(len(tcs), 8)) as _ex:
                    call_results = list(_ex.map(_call_one, tcs))

                for tc, args, res, dur in call_results:
                    results.append({
                        "tool": tc.function.name, "args": args, "label": tc.function.name,
                        "result": res, "duration_ms": dur,
                        "error": _error_of(res),
                    })
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": _clip(res)})
            else:
                # ran out of rounds — force a final answer without tools
                answer, _ = _llm_call(
                    client=client, model=model, messages=messages,
                    tools=None, tool_choice="none",
                    run_id=trace["run_id"], rnd=MAX_ROUNDS, base_url=base_url,
                    timeout=60, text_cb=text_cb,
                )
        except Exception as exc:
            trace["error"] = str(exc)
            answer = f"Orchestrator error: {exc}"

        # Extract chart hints the model embedded, then remove the tag from the answer.
        answer = answer.strip()
        chart_hints = _extract_chart_hints(answer)
        answer = _strip_chart_tag(answer)

        # ── Build trace for the existing UI (debug panel + route map) ──────────
        trace["timing"]["total_ms"] = int((time.perf_counter() - t0) * 1000)
        trace["tool_calls"] = results
        trace["plan"] = {
            "reasoning": f"native tool-use loop, {len(results)} call(s)",
            "steps": [{"tool": r["tool"], "args": r["args"], "label": r["label"]} for r in results],
        }
        trace["agents"] = [{
            "agent": "ToolUseLoop", "phase": 1,
            "duration_ms": trace["timing"]["total_ms"],
            "data_summary": _summary(results),
        }]
        trace["route_data"] = _route_data(results)
        trace["chart_hints"] = chart_hints
        ft = _flythrough_from_results(results)
        if ft:
            trace["actions"].append(ft)
        trace["answer"] = answer
        _write_log(trace)
        return answer, trace


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _consume_stream(stream, text_cb=None, _meta: Optional[Dict] = None):
    """Consume a streaming OpenAI completions response.

    Calls text_cb(delta_str) for each text chunk. Fills _meta in-place with
    timing and usage when provided:
      _meta["t_first_token_ms"], _meta["t_total_ms"],
      _meta["finish_reason"], _meta["usage"].

    Returns (content_str, tool_calls_list).
    """
    content_parts: List[str] = []
    tc_chunks: Dict[int, Dict[str, str]] = {}
    _t0 = time.perf_counter() if _meta is not None else None

    for chunk in stream:
        if not chunk.choices:
            # Usage-only chunk (last chunk when stream_options include_usage=True)
            if _meta is not None and hasattr(chunk, "usage") and chunk.usage:
                _meta["usage"] = {
                    "prompt_tokens": getattr(chunk.usage, "prompt_tokens", None),
                    "completion_tokens": getattr(chunk.usage, "completion_tokens", None),
                    "total_tokens": getattr(chunk.usage, "total_tokens", None),
                }
            continue
        delta = chunk.choices[0].delta
        if chunk.choices[0].finish_reason and _meta is not None:
            _meta["finish_reason"] = chunk.choices[0].finish_reason
        if delta.content:
            content_parts.append(delta.content)
            if _meta is not None and "t_first_token_ms" not in _meta and _t0 is not None:
                _meta["t_first_token_ms"] = int((time.perf_counter() - _t0) * 1000)
            if text_cb:
                try:
                    text_cb(delta.content)
                except Exception:
                    pass
        if delta.tool_calls:
            if _meta is not None and "t_first_token_ms" not in _meta and _t0 is not None:
                _meta["t_first_token_ms"] = int((time.perf_counter() - _t0) * 1000)
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tc_chunks:
                    tc_chunks[idx] = {"id": "", "name": "", "arguments": ""}
                if tc.id:
                    tc_chunks[idx]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tc_chunks[idx]["name"] += tc.function.name
                    if tc.function.arguments:
                        tc_chunks[idx]["arguments"] += tc.function.arguments

    if _meta is not None and _t0 is not None:
        _meta.setdefault("t_total_ms", int((time.perf_counter() - _t0) * 1000))

    content = "".join(content_parts)
    tool_calls = [
        SimpleNamespace(
            id=tc_chunks[i]["id"],
            type="function",
            function=SimpleNamespace(
                name=tc_chunks[i]["name"],
                arguments=tc_chunks[i]["arguments"],
            ),
        )
        for i in sorted(tc_chunks)
    ]
    return content, tool_calls


def _llm_call(
    client, model: str, messages: List[Dict], run_id: str, rnd: int, base_url: str,
    tools: Optional[List] = None, tool_choice: str = "none",
    timeout: int = 90, text_cb=None,
) -> Tuple[str, List]:
    """Single streaming LLM call with full timing/usage logging to llm_calls.jsonl."""
    ts = datetime.utcnow().isoformat() + "Z"
    _meta: Dict = {}
    try:
        stream = client.chat.completions.create(
            model=model, messages=messages,
            tools=tools or None, tool_choice=tool_choice,
            temperature=0, timeout=timeout,
            stream=True, stream_options={"include_usage": True},
        )
        content, tcs = _consume_stream(stream, text_cb, _meta=_meta)
    except Exception as exc:
        _meta["t_total_ms"] = _meta.get("t_total_ms")
        _write_llm_log(run_id, rnd, ts, model, base_url, tool_choice,
                       len(tools or []), messages, _meta, "", [], str(exc))
        raise
    _write_llm_log(run_id, rnd, ts, model, base_url, tool_choice,
                   len(tools or []), messages, _meta, content, tcs, None)
    return content, tcs


def _extract_chart_hints(answer: str) -> List[str]:
    """Pull chart description strings from a <!--charts: ... --> tag in the answer."""
    m = _CHART_TAG_RE.search(answer)
    if not m:
        return []
    return [h.strip() for h in m.group(1).split("|") if h.strip()]


def _strip_chart_tag(answer: str) -> str:
    """Remove the <!--charts: ...-->  tag and any trailing whitespace."""
    return _CHART_TAG_RE.sub("", answer).rstrip()


def _requires_data(question: str) -> bool:
    """True when the question is about personal fitness/health data that needs tool calls.

    Catches subjective/identity questions ("am I a good runner?") and explicit
    personal-data questions ("my pace this month") that the model might otherwise
    answer with generic text.  Intentionally narrow — conceptual questions
    ("what is VO2max?") and pure small-talk are not matched.  Bilingual: EN + DE.
    """
    q = question.lower()
    # EN: first-person possessive followed by a fitness concept
    if re.search(
        r'\bmy\s+(run|ride|trai|work|sleep|stress|heart|pace|perf|fit|health|'
        r'week|month|year|prog|stat|activ|body|recov|vo2|weight|bike|hike|zone)',
        q,
    ):
        return True
    # EN: self-evaluation / personal-profile patterns
    if re.search(r'\b(am i|how am i|have i|did i improve|am i getting)\b', q):
        return True
    if any(p in q for p in (
        "about me", "about myself", "interesting about",
        "analyze my", "analyse my", "tell me about my",
        "good runner", "good cyclist", "good athlete", "good at running",
    )):
        return True
    # DE: first-person possessive "mein/meine/meinen/meinem" followed by fitness concept
    if re.search(
        r'\bmein(e[mnrs]?)?\s+(lauf|run|rad|trai|sport|schlaf|stress|herz|tempo|pace|'
        r'leis|form|gesund|woche|monat|jahr|fort|stat|aktiv|körp|erhol|vo2|gewicht|'
        r'bike|wander|zone|puls)',
        q,
    ):
        return True
    # DE: self-evaluation questions
    if re.search(r'\b(bin ich|wie bin ich|wie lauf|wie trai|habe ich|hab ich|'
                 r'bin ich gut|entwickl|verbesser|fortschritt)\b', q):
        return True
    if any(p in q for p in (
        "über mich", "über mein", "von mir", "zeig mir mein",
        "analysier", "guter läufer", "guter radfahrer", "guter sportler",
        "wie stehe ich", "wie geht es mir", "wie ist mein",
    )):
        return True
    return False


def _error_of(result: str) -> Optional[str]:
    try:
        d = json.loads(result)
        return d.get("error") if isinstance(d, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _compact_list_item(item: Any) -> Any:
    """Strip nested objects and null values from a dict so activity lists stay small.

    Always-kept keys (id, name, date, type, …) are preserved verbatim regardless of
    string length so the model always sees activity names and dates. Other string
    fields are kept if ≤80 chars. Nested dicts and lists are dropped.
    This lets 40–100 activity records fit within a reasonable context budget.
    """
    if not isinstance(item, dict):
        return item
    result = {}
    for k, v in item.items():
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        if isinstance(v, str):
            if k in _ALWAYS_KEEP_KEYS or len(v) <= 80:
                result[k] = v
        else:
            result[k] = v
    return result


def _clip(result: str, limit: int = 6000) -> str:
    """Compact large arrays + cap length before feeding a tool result back to the model."""
    try:
        d = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result[:limit]
    if isinstance(d, dict):
        for k in list(d.keys()):
            v = d[k]
            if not isinstance(v, list):
                continue
            if k in LARGE_ARRAY_KEYS:
                # GPS points / waypoints etc. — replace with a placeholder
                if len(v) > 20:
                    d[k] = f"[{len(v)} items — rendered below]"
            elif len(v) > 5:
                # Activity/record lists (e.g. "activities") — compact so the
                # model sees ALL items, not just the first ~10 after JSON truncation.
                d[k] = [_compact_list_item(item) for item in v]
                limit = 20_000
    elif isinstance(d, list) and len(d) > 5:
        # Top-level list responses — same compaction.
        d = [_compact_list_item(item) for item in d]
        limit = 20_000
    s = json.dumps(d)
    return s[:limit] + ("…[truncated]" if len(s) > limit else "")


def _summary(results: List[Dict]) -> str:
    ok = [r["label"] for r in results if not r.get("error")]
    err = [r["label"] for r in results if r.get("error")]
    parts = []
    if ok:  parts.append("retrieved: " + ", ".join(ok))
    if err: parts.append("failed: " + ", ".join(err))
    return " · ".join(parts) or "no data fetched"


def _route_data(results: List[Dict]) -> Optional[Dict]:
    """First successful route-tool result → {tool(bare), data} for the folium map."""
    for r in results:
        bare = r["tool"].split(SEP, 1)[-1]
        if bare in ROUTE_TOOLS and not r.get("error"):
            try:
                return {"tool": bare, "data": json.loads(r["result"])}
            except (json.JSONDecodeError, TypeError):
                pass
    return None


def _cb(fn: Optional[Callable], msg: str) -> None:
    if fn:
        try:
            fn(msg)
        except Exception:
            pass


def _flythrough_from_results(results: List[Dict]) -> Optional[Dict]:
    """Detect any tool result with action='show_flythrough' and build a trace action.

    Tool-name agnostic — works with flythrough__prepare_flythrough,
    strava__launch_flythrough, or any future server returning this action key.
    """
    for r in results:
        if r.get("error"):
            continue
        try:
            data = json.loads(r["result"])
            if not isinstance(data, dict) or data.get("action") != "show_flythrough":
                continue
            return {
                "type":          "flythrough",
                "activity_id":   data.get("activity_id"),
                "activity_name": data.get("activity_name", "Activity"),
                "mode":          data.get("mode", "satellite_3d"),
                "duration_sec":  int(data.get("duration_sec", 60)),
                "orientation":   data.get("orientation", "landscape"),
                "resolution":    data.get("resolution", "2K"),
                "hidden":        True,
            }
        except Exception:
            continue
    return None


def _write_log(trace: Dict) -> None:
    """High-level interaction log — one entry per orchestrator run."""
    try:
        tool_calls = trace.get("tool_calls") or []
        entry = {
            "run_id":       trace["run_id"],
            "ts":           trace["ts"],
            "model":        os.getenv("AGENT_MODEL", ""),
            "base_url":     os.getenv("OPENAI_BASE_URL", ""),
            "user_input":   trace["user_input"],
            "n_tool_calls": len(tool_calls),
            "tools":        [r["tool"] for r in tool_calls],
            "tool_details": [
                {
                    "tool": r["tool"],
                    "args": r.get("args"),
                    "duration_ms": r.get("duration_ms"),
                    "error": r.get("error"),
                    "result_chars": len(r.get("result") or ""),
                    "result_preview": (r.get("result") or "")[:300],
                }
                for r in tool_calls
            ],
            "timing":       trace.get("timing", {}),
            "error":        trace.get("error"),
            "has_route":    bool(trace.get("route_data")),
            "answer":       trace.get("answer") or "",
        }
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _write_llm_log(
    run_id: str, rnd: int, ts: str, model: str, base_url: str,
    tool_choice: str, n_tools: int, messages: List[Dict], meta: Dict,
    content: str, tcs: list, error: Optional[str],
) -> None:
    """Per-LLM-call log — timing, usage, input/output previews for every round."""
    try:
        msgs_log = []
        total_chars = 0
        for m in messages:
            c = m.get("content") or ""
            if isinstance(c, list):
                c = json.dumps(c)
            chars = len(c)
            total_chars += chars
            msgs_log.append({
                "role":    m["role"],
                "chars":   chars,
                "preview": c[:300] + ("…" if chars > 300 else ""),
            })
        entry = {
            "run_id":            run_id,
            "round":             rnd,
            "ts":                ts,
            "model":             model,
            "base_url":          base_url,
            "tool_choice":       tool_choice,
            "n_tools":           n_tools,
            "n_messages":        len(messages),
            "input_chars_total": total_chars,
            "messages":          msgs_log,
            "t_first_token_ms":  meta.get("t_first_token_ms"),
            "t_total_ms":        meta.get("t_total_ms"),
            "finish_reason":     meta.get("finish_reason"),
            "usage":             meta.get("usage"),
            "output_chars":      len(content),
            "output_preview":    content[:500] + ("…" if len(content) > 500 else ""),
            "output_tool_calls": [
                {"name": tc.function.name, "args": (tc.function.arguments or "")[:400]}
                for tc in tcs
            ],
            "error":             error,
        }
        with open(LLM_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
