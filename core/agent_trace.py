"""Trace assembly + result-compaction helpers shared by the agent layer.

The UI (React ``AgentTrace``/``RouteResult``) and the
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


def _identity_score(record: Dict, data: Any, answer_cf: str) -> int:
    """How strongly a route-tool result matches the entity the answer names.

    Tool calls are recorded in COMPLETION order, and the specialists fire parallel
    fetches for several candidates — so the first successful route result is NOT
    necessarily the one the answer recommends (GitHub #11: names Hike A, plots Hike
    B). This scores a candidate against the (chart-tag-stripped, casefolded) answer:

      • id match  → 100: the activity id (``data["activity_id"]``,
        ``data["activity"]["id"]``, or the ``activity_id`` call arg) appears in the
        answer as a bare token.
      • name match → len(name): a name ≥4 chars found case-insensitively in the
        answer (``data["activity"]["name"]``, the ``activity_name`` call arg,
        ``data["name"]``, and each ``data["trails"][i]["name"]``). Longer matches win.

    0 means "no evidence" — the caller falls back to today's first-result behavior.
    """
    if not isinstance(answer_cf, str) or not answer_cf:
        return 0
    args = record.get("args") if isinstance(record, dict) else None
    args = args if isinstance(args, dict) else {}
    d = data if isinstance(data, dict) else {}
    activity = d.get("activity") if isinstance(d.get("activity"), dict) else {}

    # ── id match (exact, wins over any name) ──
    id_candidates = [d.get("activity_id"), activity.get("id"), args.get("activity_id")]
    for raw in id_candidates:
        if raw is None:
            continue
        tok = str(raw).strip()
        if tok and re.search(rf'(?<![0-9a-zA-Z]){re.escape(tok)}(?![0-9a-zA-Z])', answer_cf):
            return 100

    # ── name match (longest ≥4-char name found in the answer) ──
    name_candidates: List[Any] = [activity.get("name"), args.get("activity_name"), d.get("name")]
    for t in d.get("trails") or []:
        if isinstance(t, dict):
            name_candidates.append(t.get("name"))
    best = 0
    for nm in name_candidates:
        if not isinstance(nm, str):
            continue
        nm_cf = nm.strip().casefold()
        if len(nm_cf) >= 4 and nm_cf in answer_cf:
            best = max(best, len(nm_cf))
    return best


def route_data(results: List[Dict], answer: str = "") -> Optional[Dict]:
    """Pick the route-tool result to plot on the map → {tool(bare), data}.

    With 0/1 route candidates the behavior is identical to before (None / that one).
    With several, the answer decides which entity to plot (GitHub #11): each
    candidate is scored via :func:`_identity_score` against the answer, and the
    highest-scoring one wins — ties broken toward the LATER call (the agent's final
    decision). When nothing scores (empty answer or no name/id overlap) it falls
    back byte-identically to the FIRST successful candidate.
    """
    candidates: List[Dict[str, Any]] = []
    for r in results:
        bare = (r.get("tool") or "").split(SEP, 1)[-1]
        if bare in ROUTE_TOOLS and not r.get("error"):
            try:
                candidates.append({"tool": bare, "data": json.loads(r["result"]), "record": r})
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

    if not candidates:
        return None
    if len(candidates) == 1:
        c = candidates[0]
        return {"tool": c["tool"], "data": c["data"]}

    answer_cf = (answer or "").casefold()
    best = None
    best_score = 0
    for c in candidates:  # iterate in completion order; ">=" lets a later tie win
        score = _identity_score(c["record"], c["data"], answer_cf)
        if score > 0 and score >= best_score:
            best_score = score
            best = c
    if best is not None:
        return {"tool": best["tool"], "data": best["data"]}

    c = candidates[0]  # no evidence → first successful (unchanged behavior)
    return {"tool": c["tool"], "data": c["data"]}


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


# ── Source citations (RAG) ────────────────────────────────────────────────────

# A heading line like "Sources:" / "**Sources**" the synthesis model may emit.
_SOURCES_HEADING_RE = re.compile(r'(?im)^[ \t>*_#-]*sources\b[ \t]*:?.*$')


def collect_sources(tool_calls: List[Dict]) -> List[str]:
    """Distinct ``source`` strings from RAG-shaped tool results.

    Detects the retrieval result shape ``{"results": [{"source", "passage", …}]}``
    by structure (``source`` + ``passage`` keys), NOT by tool name — so ``core``
    stays tool-agnostic while still surfacing the fitness library's citations. The
    list is de-duplicated, order-preserved.
    """
    seen: set = set()
    out: List[str] = []
    for r in tool_calls or []:
        if r.get("error"):
            continue
        try:
            d = json.loads(r.get("result") or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = d.get("results") if isinstance(d, dict) else None
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and "passage" in it:
                s = it.get("source")
                if isinstance(s, str) and s.strip() and s not in seen:
                    seen.add(s)
                    out.append(s.strip())
    return out


def ensure_sources(answer: str, sources: List[str]) -> str:
    """Guarantee the answer ends with an authoritative ``Sources:`` list.

    Any trailing ``Sources:`` block the synthesis model wrote is replaced with the
    real, de-duplicated source list derived from the retrieved passages — so the
    user always sees the actual books cited, even if the model dropped or garbled
    them. A no-op when there are no sources (e.g. non-RAG answers).
    """
    if not sources:
        return answer or ""
    text = answer or ""
    matches = list(_SOURCES_HEADING_RE.finditer(text))
    if matches:
        text = text[:matches[-1].start()]
    listed = "\n".join(f"- {s}" for s in sources)
    return f"{text.rstrip()}\n\nSources:\n{listed}"


# ── Trace assembly ────────────────────────────────────────────────────────────

def _flatten_artifacts(artifacts: Optional[List[Dict]]) -> List[Dict]:
    """Depth-first flatten of specialist artifacts incl. peer ``sub_artifacts``.

    In the peer-to-peer mesh a specialist may consult others; it nests their
    artifacts under ``sub_artifacts``. Flattening here means every agent that ran
    (orchestrator's direct specialists AND the peers they consulted) gets its own
    row in ``agents`` and its MCP calls in ``tool_calls`` — so route maps/charts
    stay complete regardless of who fetched the data.
    """
    flat: List[Dict] = []

    def _walk(arts: Optional[List[Dict]]) -> None:
        for a in arts or []:
            if not isinstance(a, dict):
                continue
            flat.append(a)
            _walk(a.get("sub_artifacts"))

    _walk(artifacts)
    return flat


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
    attaches. The returned trace matches what the React
    ``AgentTrace``/``RouteResult`` components, and ``api/chart_service.py`` expect.
    """
    results: List[Dict[str, Any]] = []
    agents: List[Dict[str, Any]] = []
    for i, art in enumerate(_flatten_artifacts(specialist_artifacts), start=1):
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
        "route_data":  route_data(results, answer),
        "chart_hints": chart_hints,
        "answer":      answer,
    }
    ft = flythrough_from_results(results)
    if ft:
        trace["actions"].append(ft)
    return trace
