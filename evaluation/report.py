"""Collect MLflow facts for an e2e run and have gpt-5.4-mini write an HTML report.

Two stages:

1. :func:`collect_facts` reads back everything MLflow recorded for the run — the
   aggregate scorer metrics, and per-conversation detail (turns, latency, which
   specialists ran, each scorer's verdict + rationale, a short transcript) by
   grouping the run's traces by session and matching them to personas.

2. :func:`render_html` hands those facts to gpt-5.4-mini, which writes a single
   self-contained HTML document combining the hard MLflow facts with its own
   analysis and recommendations. The model is told to use *only* the supplied
   facts (no invented numbers), mirroring the Copilot's own grounding rule.
"""

from __future__ import annotations

import json
from typing import Any

import mlflow

from . import config

_TURN_CHARS = 600       # max chars of a transcript turn fed to the report writer
_RATIONALE_CHARS = 600  # max chars of a judge rationale fed to the report writer


# ── Fact collection ───────────────────────────────────────────────────────────
def _md(trace) -> dict:
    try:
        return dict(trace.info.request_metadata or {})
    except Exception:
        return {}


def _tags(trace) -> dict:
    try:
        return dict(getattr(trace.info, "tags", {}) or {})
    except Exception:
        return {}


def _turn_io(trace) -> tuple[str, str]:
    """(latest user message, assistant answer) for the single turn this trace covers."""
    try:
        spans = trace.search_spans(name="fitdash_copilot") or trace.data.spans
        span = spans[0]
        inp = span.inputs or {}
        msgs = inp.get("messages") or []
        user = msgs[-1].get("content", "") if msgs else ""
        out = span.outputs
        assistant = out if isinstance(out, str) else json.dumps(out, default=str)
        return user, assistant
    except Exception:
        info = trace.info
        return (getattr(info, "request_preview", "") or "",
                getattr(info, "response_preview", "") or "")


def _split_assessments(trace) -> tuple[dict[str, dict], dict[str, str]]:
    """Separate a trace's assessments into scorer *feedback* and *expectations*.

    Scorer outputs are Feedback assessments (``.feedback`` set); the persona's
    ground-truth ``expectations`` are Expectation assessments (``.expectation``
    set). We must not let an expectation masquerade as a score.
    """
    scores: dict[str, dict] = {}
    expectations: dict[str, str] = {}
    try:
        for a in trace.search_assessments() or []:
            name = getattr(a, "name", None)
            if not name:
                continue
            fb = getattr(a, "feedback", None)
            ex = getattr(a, "expectation", None)
            if fb is not None:
                scores[name] = {
                    "value": _stringify(getattr(fb, "value", None)),
                    "rationale": (getattr(a, "rationale", "") or "")[:_RATIONALE_CHARS],
                }
            elif ex is not None:
                expectations[name] = _stringify(getattr(ex, "value", None))
    except Exception:
        pass
    return scores, expectations


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "value"):  # CategoricalRating-like enums
        try:
            return str(v.value)
        except Exception:
            pass
    return str(v)


def collect_facts(experiment, results, personas: list[dict], run_meta: dict) -> dict:
    """Assemble a JSON-serialisable facts dict for the report writer."""
    exp_id = experiment.experiment_id
    traces = mlflow.search_traces(
        locations=[exp_id], return_type="list", order_by=["timestamp_ms ASC"]
    )

    # Group traces by simulation session id.
    sessions: dict[str, list] = {}
    for t in traces:
        md = _md(t)
        sid = md.get("mlflow.trace.session") or md.get("session_id") or "unknown"
        sessions.setdefault(sid, []).append(t)

    # Match each session to a persona by its goal (the simulator stamps the goal
    # into every turn's metadata).
    goal_to_persona = {p["goal"]: p for p in personas}

    session_facts: list[dict] = []
    for sid, ts in sessions.items():
        ts_sorted = sorted(
            ts, key=lambda x: int(_md(x).get("mlflow.simulation.turn", "0") or 0)
        )
        first_md = _md(ts_sorted[0])
        goal = first_md.get("mlflow.simulation.goal", "")
        persona = goal_to_persona.get(goal, {})

        transcript, tools, agents, scores, expectations = [], set(), set(), {}, {}
        total_latency, had_error = 0, False
        for t in ts_sorted:
            user, assistant = _turn_io(t)
            transcript.append({"user": user[:_TURN_CHARS], "assistant": assistant[:_TURN_CHARS]})
            tg = _tags(t)
            for x in (tg.get("fitdash.tools", "") or "").split(","):
                if x:
                    tools.add(x)
            for x in (tg.get("fitdash.agents", "") or "").split(","):
                if x:
                    agents.add(x)
            try:
                total_latency += int(tg.get("fitdash.latency_ms") or 0)
            except Exception:
                pass
            if (tg.get("fitdash.error") or "").lower() == "true":
                had_error = True
            sc, ex = _split_assessments(t)
            scores.update(sc)
            expectations.update(ex)

        session_facts.append(
            {
                "session_id": sid,
                "persona_id": persona.get("id", "?"),
                "persona_name": persona.get("name", "?"),
                "persona_type": persona.get("type", "?"),
                "goal": goal or persona.get("goal", ""),
                "n_turns": len(ts_sorted),
                "total_copilot_latency_ms": total_latency,
                "had_error": had_error,
                "specialists_used": sorted(agents),
                "tools_used": sorted(tools),
                "scores": scores,
                "expectations": expectations,
                "transcript": transcript,
            }
        )

    # Per-scorer rollup across sessions (positive-verdict counts).
    positive = {"yes", "pass", "true", "1", "5", "safe"}
    scorer_rollup: dict[str, dict] = {}
    for sf in session_facts:
        for name, sc in sf["scores"].items():
            r = scorer_rollup.setdefault(name, {"positive": 0, "scored": 0, "values": {}})
            val = str(sc.get("value", "")).strip().lower()
            if val:
                r["scored"] += 1
                if val in positive:
                    r["positive"] += 1
                r["values"][val] = r["values"].get(val, 0) + 1

    return {
        "experiment": {
            "name": experiment.name,
            "id": exp_id,
            "tracking_uri": config.resolve_tracking_uri(),
            **run_meta,
        },
        "config": {
            "simulator_model": config.SIMULATOR_MODEL_RAW,
            "judge_model": config.JUDGE_MODEL_RAW,
            "report_model": config.REPORT_MODEL_RAW,
        },
        "aggregate_metrics": _json_safe(getattr(results, "metrics", {}) or {}),
        "scorer_rollup": scorer_rollup,
        "n_personas": len(personas),
        "n_sessions": len(session_facts),
        "sessions": session_facts,
    }


def _json_safe(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return {k: _stringify(v) for k, v in obj.items()} if isinstance(obj, dict) else str(obj)


# ── HTML rendering via gpt-5.4-mini ───────────────────────────────────────────
_REPORT_SYSTEM = """\
You are a senior ML evaluation analyst. You write a single, self-contained HTML report
for an end-to-end multi-turn evaluation of "FitDash", an AI sports-analytics Training
Copilot. The evaluation simulated persona users (two types: ambitious triathletes and
hobby road cyclists) holding multi-turn conversations with the Copilot, then scored each
conversation with LLM judges.

Rules:
- Use ONLY the facts in the provided JSON. Never invent numbers, scores, or quotes.
- If a value is missing, say so plainly rather than guessing.
- Output a COMPLETE standalone HTML document (<!DOCTYPE html> … </html>) with inline
  <style>. No markdown, no code fences — raw HTML only.

The report must contain, clearly sectioned:
1. Header: experiment name, timestamp, models used (simulator, judges, report writer),
   number of personas/sessions, MLflow tracking URI.
2. Executive summary: 3–6 sentences of YOUR analysis of how the Copilot performed.
3. Aggregate scorecard: a table of each scorer using `scorer_rollup` (positive verdicts /
   scored, and the distribution of values). Note that categorical scorers (e.g. a
   pass/fail judge, or user_frustration's resolved/unresolved) won't appear in
   `aggregate_metrics` (which only has numeric /mean values) — rely on `scorer_rollup`
   for the full picture and show `aggregate_metrics` as supplementary.
4. Results by persona type (ambitious_triathlete vs hobby_cyclist): how each cohort fared.
5. Per-persona detail cards: persona name + type, goal, the persona's `expectations`
   (ground truth) if present, turns, total Copilot latency, specialists/tools the Copilot
   used, each scorer's verdict with the judge's rationale, and a compact rendered transcript.
6. Analysis & recommendations: YOUR interpretation — strengths, weaknesses, failure
   patterns, and concrete suggestions to improve the Copilot.

Make it visually clean and readable: a quiet professional palette, readable typography,
clear tables, pass/fail or score badges with colour cues, and a sensible layout.
"""


def render_html(facts: dict) -> str:
    """Ask gpt-5.4-mini to turn the facts into a standalone HTML report."""
    client = config.openai_client()
    payload = json.dumps(facts, ensure_ascii=False, default=str)
    user = (
        "Write the HTML evaluation report from these facts. Use only this data.\n\n"
        f"FACTS (JSON):\n{payload}"
    )
    resp = client.chat.completions.create(
        model=config.REPORT_MODEL_RAW,
        messages=[
            {"role": "system", "content": _REPORT_SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    html = (resp.choices[0].message.content or "").strip()
    # Strip any accidental markdown fence.
    if html.startswith("```"):
        html = html.split("\n", 1)[-1]
        if html.endswith("```"):
            html = html.rsplit("```", 1)[0]
    return html.strip()
