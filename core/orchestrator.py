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
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config import SEP
from core.host import ToolHost
from core.llm import get_llm_client

LOG_DIR = Path(".logs")
LOG_FILE = LOG_DIR / "agent_interactions.jsonl"

MAX_ROUNDS = 6
HISTORY_WINDOW = 10
LARGE_ARRAY_KEYS = {"points", "waypoints", "segments", "timeline", "buckets_15min", "trails", "instructions"}
ROUTE_TOOLS = {"plan_route", "plan_circular_route", "explore_trails", "get_isochrone"}


_SYSTEM = """\
You are Training Copilot, an AI assistant for fitness, route planning, weather and calendar.
Today is {today}.

You have tools that fetch the user's REAL data. Use them whenever the question needs
real numbers, dates, weather, a forecast, a route or calendar events — never guess or
invent values. For small talk or questions about yourself, just answer directly.

ACTIVITY DATA — TWO SOURCES, ALWAYS CHECK BOTH:
• strava__get_activities  — Strava-recorded workouts (may return 0 if not connected)
• garmin__get_garmin_activities — Garmin-recorded workouts (runs, rides, hikes, …)
When the user asks about runs, workouts, activities, pace, HR or training — call BOTH
tools in parallel and merge the results. Never stop at one source returning empty.

Compute explicit YYYY-MM-DD dates yourself; never pass relative strings like "Friday".
Maps may render automatically below your answer for route results — reference them
naturally ("see the map below") instead of dumping raw coordinates. Answer concisely in
the user's language."""


class FitDashOrchestrator:
    """Stateless tool-use engine. Create once (st.cache_resource), call run() per turn."""

    def __init__(self, host: Optional[ToolHost] = None) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        self.host = host or ToolHost()
        self._tools: Optional[List[Dict]] = None

    def _discover(self) -> List[Dict]:
        if self._tools is None:
            self._tools = self.host.list_tools()
        return self._tools

    def run(
        self,
        user_input: str,
        history: List[Dict],
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, Dict]:
        client, model = get_llm_client()
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
                messages.append({"role": m["role"], "content": c[:1500]})
        messages.append({"role": "user", "content": user_input})

        results: List[Dict[str, Any]] = []
        answer = ""
        t0 = time.perf_counter()

        try:
            for rnd in range(MAX_ROUNDS):
                _cb(progress_cb, f"Phase {rnd + 1} — Model thinking…")
                resp = client.chat.completions.create(
                    model=model, messages=messages,
                    tools=tools or None, tool_choice=("auto" if tools else "none"),
                    temperature=0, timeout=60,
                )
                msg = resp.choices[0].message
                tcs = msg.tool_calls or []
                if not tcs:
                    answer = msg.content or ""
                    break

                messages.append({
                    "role": "assistant", "content": msg.content or "",
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in tcs
                    ],
                })
                _cb(progress_cb, "Fetching data: " + ", ".join(tc.function.name for tc in tcs))

                for tc in tcs:
                    ts = time.perf_counter()
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    res = self.host.call_tool(tc.function.name, args)
                    results.append({
                        "tool": tc.function.name, "args": args, "label": tc.function.name,
                        "result": res, "duration_ms": int((time.perf_counter() - ts) * 1000),
                        "error": _error_of(res),
                    })
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": _clip(res)})
            else:
                # ran out of rounds — force a final answer without tools
                resp = client.chat.completions.create(model=model, messages=messages, temperature=0, timeout=60)
                answer = resp.choices[0].message.content or ""
        except Exception as exc:
            trace["error"] = str(exc)
            answer = f"Orchestrator error: {exc}"

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
        ft = _flythrough_from_results(results)
        if ft:
            trace["actions"].append(ft)
        trace["answer"] = answer
        _write_log(trace)
        return answer, trace


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _error_of(result: str) -> Optional[str]:
    try:
        d = json.loads(result)
        return d.get("error") if isinstance(d, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _clip(result: str, limit: int = 6000) -> str:
    """Compact large arrays + cap length before feeding a tool result back to the model."""
    try:
        d = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result[:limit]
    if isinstance(d, dict):
        for k in LARGE_ARRAY_KEYS:
            v = d.get(k)
            if isinstance(v, list) and len(v) > 20:
                d[k] = f"[{len(v)} items — rendered below]"
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
    try:
        entry = {
            "run_id": trace["run_id"], "ts": trace["ts"], "user_input": trace["user_input"],
            "n_tool_calls": len(trace.get("tool_calls", [])),
            "timing": trace.get("timing", {}), "error": trace.get("error"),
            "answer_preview": (trace.get("answer") or "")[:300],
        }
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
