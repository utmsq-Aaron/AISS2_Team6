"""Trace assembly + result-compaction helpers shared by the agent layer.

The UI (Streamlit debug panel + React ``AgentTrace``/``RouteResult``) and the
chart service consume a specific ``trace`` dict shape. In the multi-agent build
each specialist returns its raw MCP ``tool_calls`` as an A2A DataPart artifact;
the orchestrator aggregates those artifacts and calls :func:`build_trace` here to
produce exactly that shape — so route maps, charts, and flythrough keep working
with no UI change.

These helpers were the trace-building core of the old single-loop orchestrator;
they are kept verbatim so the contract is byte-for-byte compatible.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import SEP

# Large GPS/timeline arrays are replaced by a placeholder before a result goes
# back into an LLM's context (the UI renders the full data separately).
LARGE_ARRAY_KEYS = {"points", "waypoints", "segments", "timeline", "buckets_15min", "trails", "instructions"}
# Bare tool names whose first successful result becomes ``route_data`` (the map).
ROUTE_TOOLS = {"plan_route", "plan_circular_route", "plan_park_loop", "explore_trails",
               "get_isochrone", "get_activity_streams", "get_activity_gps_track"}
# Keys always preserved verbatim in _compact_list_item regardless of string length.
_ALWAYS_KEEP_KEYS = {"id", "name", "date", "type", "sport_type", "start_date"}
# Pattern the model uses to embed chart suggestions at the end of its answer.
_CHART_TAG_RE = re.compile(r'<!--charts:\s*(.+?)-->', re.IGNORECASE | re.DOTALL)


# ── Answer post-processing ────────────────────────────────────────────────────

def extract_chart_hints(answer: str) -> List[str]:
    """Pull chart description strings from a ``<!--charts: ... -->`` tag."""
    m = _CHART_TAG_RE.search(answer or "")
    if not m:
        return []
    return [h.strip() for h in m.group(1).split("|") if h.strip()]


def strip_chart_tag(answer: str) -> str:
    """Remove the ``<!--charts: ...-->`` tag and any trailing whitespace."""
    return _CHART_TAG_RE.sub("", answer or "").rstrip()


# ── Result inspection / compaction ────────────────────────────────────────────

def error_of(result: str) -> Optional[str]:
    """Return the ``error`` field of a JSON tool result, or None."""
    try:
        d = json.loads(result)
        return d.get("error") if isinstance(d, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _compact_list_item(item: Any) -> Any:
    """Strip nested objects and null values from a dict so lists stay small."""
    if not isinstance(item, dict):
        return item
    result: Dict[str, Any] = {}
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


def clip(result: str, limit: int = 6000) -> str:
    """Compact large arrays + cap length before feeding a tool result to a model.

    Only the model-context copy is clipped; the full result is preserved in the
    artifact ``tool_calls`` records so the UI can render maps/charts in full.
    """
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
                if len(v) > 20:
                    d[k] = f"[{len(v)} items — rendered below]"
            elif len(v) > 5:
                d[k] = [_compact_list_item(item) for item in v]
                limit = 20_000
    elif isinstance(d, list) and len(d) > 5:
        d = [_compact_list_item(item) for item in d]
        limit = 20_000
    s = json.dumps(d)
    return s[:limit] + ("…[truncated]" if len(s) > limit else "")


def summary(results: List[Dict]) -> str:
    """Human-readable 'retrieved: … · failed: …' line for the debug panel."""
    ok = [r.get("label") or r.get("tool", "?") for r in results if not r.get("error")]
    err = [r.get("label") or r.get("tool", "?") for r in results if r.get("error")]
    parts = []
    if ok:  parts.append("retrieved: " + ", ".join(ok))
    if err: parts.append("failed: " + ", ".join(err))
    return " · ".join(parts) or "no data fetched"


def route_data(results: List[Dict]) -> Optional[Dict]:
    """First successful route-tool result → {tool(bare), data} for the map."""
    for r in results:
        bare = (r.get("tool") or "").split(SEP, 1)[-1]
        if bare in ROUTE_TOOLS and not r.get("error"):
            try:
                return {"tool": bare, "data": json.loads(r["result"])}
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
    return None


def flythrough_from_results(results: List[Dict]) -> Optional[Dict]:
    """Detect a tool result with action='show_flythrough' → a trace action."""
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
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return None


# ── Trace assembly ────────────────────────────────────────────────────────────

def build_trace(
    *,
    user_input: str,
    run_id: str,
    specialist_artifacts: List[Dict],
    answer: str,
    total_ms: int,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate specialist DataPart artifacts into the UI/chart trace contract.

    ``specialist_artifacts`` is a list of dicts shaped
    ``{"agent": str, "duration_ms": int, "tool_calls": [{tool, args, label,
    result(JSON str), duration_ms, error}, ...]}`` — exactly what each specialist
    attaches. The returned trace matches what ``ui/chat.py``, the React
    ``AgentTrace``/``RouteResult`` components, and ``api/chart_service.py`` expect.
    """
    results: List[Dict[str, Any]] = []
    agents: List[Dict[str, Any]] = []
    for i, art in enumerate(specialist_artifacts or [], start=1):
        tcs = art.get("tool_calls") or []
        for r in tcs:
            r.setdefault("label", r.get("tool", ""))
            r.setdefault("args", {})
            r.setdefault("error", error_of(r.get("result", "") or ""))
        results.extend(tcs)
        agents.append({
            "agent":        art.get("agent", f"agent{i}"),
            "phase":        i,
            "duration_ms":  int(art.get("duration_ms", 0) or 0),
            "data_summary": summary(tcs),
        })

    answer = (answer or "").strip()
    chart_hints = extract_chart_hints(answer)
    answer = strip_chart_tag(answer)

    names = ", ".join(a["agent"] for a in agents) or "no specialists"
    trace: Dict[str, Any] = {
        "run_id":      run_id,
        "ts":          datetime.utcnow().isoformat() + "Z",
        "user_input":  user_input,
        "plan": {
            "reasoning": f"multi-agent coordination → {names}; {len(results)} MCP call(s)",
            "steps": [
                {"tool": r.get("tool", ""), "args": r.get("args", {}), "label": r.get("label", "")}
                for r in results
            ],
        },
        "tool_calls":  results,
        "timing":      {"total_ms": int(total_ms)},
        "error":       error,
        "actions":     [],
        "agents":      agents,
        "route_data":  route_data(results),
        "chart_hints": chart_hints,
        "answer":      answer,
    }
    ft = flythrough_from_results(results)
    if ft:
        trace["actions"].append(ft)
    return trace
