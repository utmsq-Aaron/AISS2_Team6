"""Collect MLflow facts for an e2e run and render a fixed-layout HTML report.

Two stages:

1. :func:`collect_facts` reads back everything MLflow recorded for the run — the
   aggregate scorer metrics, and per-conversation detail (turns, latency, which
   specialists ran, each scorer's verdict + rationale, a short transcript) by
   grouping the run's traces by session and matching them to personas.

2. :func:`render_html` builds the document from a **fixed HTML/CSS template**
   defined here in code. All hard data (header, scorecard, cohort stats, per-
   persona cards, transcripts) is filled in deterministically. Only the *prose*
   fields — the executive summary, per-cohort blurbs, per-persona verdicts and
   the recommendations — are produced by the model, and each is its own small
   completion scoped to exactly the facts that field needs (no single mega-prompt,
   no model-authored layout). Those completions run on gpt-5.4-nano.
"""

from __future__ import annotations

import concurrent.futures
import html
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
            "report_model": config.PERSONA_REPORT_MODEL_RAW,
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


# ── Narrative fields (each a small, field-scoped gpt-5.4-nano completion) ──────
_PROSE_SYSTEM = (
    "You are an ML evaluation analyst writing one section of a report on FitDash, an "
    "AI sports-analytics Training Copilot evaluated end-to-end with simulated persona "
    "users (ambitious triathletes and hobby road cyclists) over multi-turn conversations. "
    "Write plain prose only — no markdown, no headings, no HTML, no bullet symbols unless "
    "asked. Use ONLY the facts given; never invent numbers, scores, or quotes."
)


def _complete(client, user: str) -> str:
    """One scoped nano completion → plain text (best-effort; '' on failure)."""
    try:
        resp = client.chat.completions.create(
            model=config.PERSONA_REPORT_MODEL_RAW,
            messages=[
                {"role": "system", "content": _PROSE_SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _scorer_passrates(facts: dict) -> dict[str, str]:
    out = {}
    for name, r in facts.get("scorer_rollup", {}).items():
        scored = r.get("scored", 0)
        out[name] = f"{r.get('positive', 0)}/{scored}" if scored else "n/a"
    return out


def _exec_summary(client, facts: dict) -> str:
    avg_latency = _avg([s["total_copilot_latency_ms"] for s in facts["sessions"]])
    errors = sum(1 for s in facts["sessions"] if s["had_error"])
    scope = {
        "personas": facts["n_personas"],
        "conversations": facts["n_sessions"],
        "scorer_positive_rates": _scorer_passrates(facts),
        "aggregate_metrics": facts.get("aggregate_metrics", {}),
        "conversations_with_a_tool_error": errors,
        "avg_total_copilot_latency_ms": avg_latency,
    }
    return _complete(
        client,
        "Write a 3–5 sentence executive summary of how the Copilot performed overall, "
        "based only on these aggregate facts:\n" + json.dumps(scope, default=str),
    )


def _cohort_blurb(client, cohort: str, sessions: list[dict]) -> str:
    compact = [
        {
            "persona": s["persona_name"],
            "goal": s["goal"],
            "scores": {k: v.get("value") for k, v in s["scores"].items()},
            "specialists_used": s["specialists_used"],
            "had_error": s["had_error"],
        }
        for s in sessions
    ]
    return _complete(
        client,
        f"In 2–3 sentences, summarise how the Copilot served the '{cohort}' cohort — what "
        "it did well and where it fell short — using only these conversations:\n"
        + json.dumps(compact, default=str),
    )


def _persona_verdict(client, session: dict) -> str:
    scope = {
        "persona": session["persona_name"],
        "type": session["persona_type"],
        "goal": session["goal"],
        "scores": session["scores"],
        "specialists_used": session["specialists_used"],
        "tools_used": session["tools_used"],
        "transcript": session["transcript"],
    }
    return _complete(
        client,
        "In 1–2 sentences, give a verdict on whether the Copilot met this persona's goal "
        "and how well, citing the concrete evidence (scores, tool usage). Facts:\n"
        + json.dumps(scope, default=str),
    )


def _recommendations(client, facts: dict) -> str:
    weak = [
        {
            "persona": s["persona_name"],
            "failing_or_notable": {
                k: v.get("value")
                for k, v in s["scores"].items()
                if str(v.get("value", "")).lower() in ("no", "fail", "unresolved", "false")
            },
            "had_error": s["had_error"],
        }
        for s in facts["sessions"]
    ]
    scope = {"scorer_positive_rates": _scorer_passrates(facts), "per_persona_concerns": weak}
    return _complete(
        client,
        "List 3–6 concrete, prioritised recommendations to improve the Copilot, based only "
        "on these facts. One recommendation per line, no numbering or bullet characters:\n"
        + json.dumps(scope, default=str),
    )


# ── Deterministic HTML template ───────────────────────────────────────────────
_CSS = """
:root{--bg:#f5f7fa;--panel:#fff;--ink:#1f2933;--muted:#6b7785;--line:#e2e8f0;
--head:#13293d;--accent:#2b6cb0;--good:#1f7a4d;--good-bg:#e6f4ec;--bad:#b42318;
--bad-bg:#fdecea;--warn:#9a6700;--warn-bg:#fbf3e0;--neutral-bg:#eef1f5;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1080px;margin:0 auto;padding:32px 24px 64px;}
h1{font-size:24px;margin:0 0 4px;color:var(--head);}
h2{font-size:18px;margin:34px 0 12px;color:var(--head);border-bottom:2px solid var(--line);padding-bottom:6px;}
h3{font-size:15px;margin:0;color:var(--head);}
.sub{color:var(--muted);font-size:13px;margin:0 0 18px;}
.meta{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:13px;color:var(--muted);margin-bottom:8px;}
.meta b{color:var(--ink);font-weight:600;}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0;}
.summary{background:var(--panel);border-left:4px solid var(--accent);}
table{width:100%;border-collapse:collapse;font-size:14px;}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em;}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600;}
.good{color:var(--good);background:var(--good-bg);}
.bad{color:var(--bad);background:var(--bad-bg);}
.warn{color:var(--warn);background:var(--warn-bg);}
.neutral{color:var(--muted);background:var(--neutral-bg);}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0;}
.card .top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;}
.goal{color:var(--muted);font-size:13px;margin:6px 0 10px;}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0;}
.chip{font-size:12px;background:var(--neutral-bg);color:var(--muted);border-radius:6px;padding:2px 8px;}
.verdict{font-size:14px;margin:8px 0;}
details{margin:6px 0;}summary{cursor:pointer;color:var(--accent);font-size:13px;}
.rationale{white-space:pre-wrap;font-size:13px;color:var(--ink);background:var(--neutral-bg);
border-radius:6px;padding:8px 10px;margin:6px 0;}
.turn{margin:8px 0;font-size:13px;}.turn .u{color:var(--accent);font-weight:600;}
.turn .a{color:var(--ink);}.turn .lbl{color:var(--muted);font-weight:600;}
ul{margin:6px 0;padding-left:20px;}li{margin:3px 0;}
.foot{color:var(--muted);font-size:12px;margin-top:28px;text-align:center;}
"""

_GOOD = {"yes", "pass", "true", "safe", "resolved", "not_frustrated", "none", "grounded"}
_BAD = {"no", "fail", "false", "unsafe", "frustrated"}
_WARN = {"partial", "unresolved", "mixed", "unknown", "somewhat"}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _verdict_class(value: str) -> str:
    s = str(value).strip().lower()
    if s in _GOOD:
        return "good"
    if s in _BAD:
        return "bad"
    if s in _WARN:
        return "warn"
    try:
        return "good" if float(s) >= 0.5 else "bad"
    except (TypeError, ValueError):
        return "neutral"


def _badge(value: str) -> str:
    return f'<span class="badge {_verdict_class(value)}">{_esc(value) or "—"}</span>'


def _avg(nums: list) -> int:
    vals = [n for n in nums if isinstance(n, (int, float))]
    return round(sum(vals) / len(vals)) if vals else 0


def _para(text: str, fallback: str) -> str:
    text = (text or "").strip()
    if not text:
        return f'<p class="sub">{_esc(fallback)}</p>'
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return "".join(f"<p>{_esc(b).replace(chr(10), '<br>')}</p>" for b in blocks)


def _bullets(text: str, fallback: str) -> str:
    lines = [ln.strip(" -•\t") for ln in (text or "").splitlines() if ln.strip(" -•\t")]
    if not lines:
        return f'<p class="sub">{_esc(fallback)}</p>'
    return "<ul>" + "".join(f"<li>{_esc(ln)}</li>" for ln in lines) + "</ul>"


def _scorecard(facts: dict) -> str:
    agg = facts.get("aggregate_metrics", {})
    rows = []
    for name, r in facts.get("scorer_rollup", {}).items():
        scored = r.get("scored", 0)
        rate = f"{r.get('positive', 0)}/{scored}" if scored else "n/a"
        dist = ", ".join(f"{_esc(v)}×{c}" for v, c in r.get("values", {}).items()) or "—"
        mean = agg.get(f"{name}/mean")
        mean_txt = f"{mean:.2f}" if isinstance(mean, (int, float)) else "—"
        rows.append(
            f"<tr><td><b>{_esc(name)}</b></td><td>{_esc(rate)}</td>"
            f"<td>{dist}</td><td>{mean_txt}</td></tr>"
        )
    return (
        '<table><tr><th>Scorer</th><th>Positive / scored</th>'
        '<th>Value distribution</th><th>Mean</th></tr>' + "".join(rows) + "</table>"
    )


def _cohort_section(facts: dict, blurbs: dict[str, str]) -> str:
    by_type: dict[str, list] = {}
    for s in facts["sessions"]:
        by_type.setdefault(s["persona_type"], []).append(s)
    out = []
    for ctype, sessions in by_type.items():
        passes = {}
        for s in sessions:
            for name, v in s["scores"].items():
                d = passes.setdefault(name, [0, 0])
                d[1] += 1
                if _verdict_class(v.get("value", "")) == "good":
                    d[0] += 1
        rate_txt = ", ".join(f"{_esc(n)} {p}/{t}" for n, (p, t) in passes.items()) or "—"
        out.append(
            f'<div class="panel"><h3>{_esc(ctype)}</h3>'
            f'<div class="meta"><span><b>{len(sessions)}</b> persona(s)</span>'
            f'<span>avg {_avg([s["n_turns"] for s in sessions])} turns</span>'
            f'<span>avg {_avg([s["total_copilot_latency_ms"] for s in sessions])} ms latency</span></div>'
            f'<div class="meta">positive: {rate_txt}</div>'
            + _para(blurbs.get(ctype, ""), "No cohort summary available.")
            + "</div>"
        )
    return "".join(out)


def _persona_card(session: dict, verdict: str) -> str:
    scores_html = ""
    for name, sc in session["scores"].items():
        rationale = sc.get("rationale", "")
        body = f'<div class="rationale">{_esc(rationale)}</div>' if rationale else ""
        scores_html += (
            f'<details><summary>{_esc(name)} &nbsp; {_badge(sc.get("value", ""))}</summary>'
            f"{body}</details>"
        )
    exp = session.get("expectations") or {}
    exp_html = (
        '<div class="meta">expected: '
        + "; ".join(f"<b>{_esc(k)}</b>: {_esc(v)}" for k, v in exp.items())
        + "</div>"
        if exp
        else ""
    )
    turns = "".join(
        f'<div class="turn"><span class="lbl">U{i}:</span> <span class="u">{_esc(t["user"])}</span><br>'
        f'<span class="lbl">A{i}:</span> <span class="a">{_esc(t["assistant"])}</span></div>'
        for i, t in enumerate(session["transcript"], 1)
    )
    chips = "".join(f'<span class="chip">{_esc(a)}</span>' for a in session["specialists_used"])
    return (
        '<div class="card">'
        f'<div class="top"><h3>{_esc(session["persona_name"])}</h3>'
        f'<span class="badge neutral">{_esc(session["persona_type"])}</span></div>'
        f'<div class="goal">🎯 {_esc(session["goal"])}</div>'
        + exp_html
        + f'<div class="meta"><span><b>{session["n_turns"]}</b> turns</span>'
        f'<span><b>{session["total_copilot_latency_ms"]}</b> ms total</span>'
        f'<span>error: <b>{"yes" if session["had_error"] else "no"}</b></span></div>'
        f'<div class="chips">{chips}</div>'
        f'<div class="verdict">{_para(verdict, "No verdict available.")}</div>'
        + scores_html
        + f"<details><summary>Transcript ({session['n_turns']} turns)</summary>{turns}</details>"
        + "</div>"
    )


def render_html(facts: dict) -> str:
    """Fill the fixed template: deterministic data + field-scoped nano completions."""
    client = config.openai_client()

    # Field-scoped prose completions. Per-persona verdicts run concurrently.
    exec_summary = _exec_summary(client, facts)
    recommendations = _recommendations(client, facts)
    cohorts = sorted({s["persona_type"] for s in facts["sessions"]})
    cohort_sessions = {c: [s for s in facts["sessions"] if s["persona_type"] == c] for c in cohorts}
    cohort_blurbs = {c: _cohort_blurb(client, c, cohort_sessions[c]) for c in cohorts}

    verdicts: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        fut = {ex.submit(_persona_verdict, client, s): s["session_id"] for s in facts["sessions"]}
        for f in concurrent.futures.as_completed(fut):
            try:
                verdicts[fut[f]] = f.result()
            except Exception:
                verdicts[fut[f]] = ""

    exp, cfg = facts["experiment"], facts["config"]
    header_meta = (
        f'<div class="meta">'
        f'<span>experiment <b>{_esc(exp.get("name"))}</b></span>'
        f'<span>run <b>{_esc(exp.get("run_id", "—"))}</b></span>'
        f'<span><b>{facts["n_personas"]}</b> personas</span>'
        f'<span><b>{facts["n_sessions"]}</b> conversations</span>'
        f'<span>≤<b>{_esc(exp.get("max_turns", "—"))}</b> turns</span></div>'
        f'<div class="meta">'
        f'<span>simulator <b>{_esc(cfg.get("simulator_model"))}</b></span>'
        f'<span>judges <b>{_esc(cfg.get("judge_model"))}</b></span>'
        f'<span>report <b>{_esc(cfg.get("report_model"))}</b></span>'
        f'<span>MLflow <b>{_esc(exp.get("tracking_uri"))}</b></span></div>'
    )
    cards = "".join(_persona_card(s, verdicts.get(s["session_id"], "")) for s in facts["sessions"])

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FitDash E2E Evaluation — {_esc(exp.get("name"))}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>FitDash Training Copilot — End-to-End Evaluation</h1>
<p class="sub">{_esc(exp.get("timestamp", ""))}</p>
{header_meta}
<h2>Executive summary</h2>
<div class="panel summary">{_para(exec_summary, "No summary available.")}</div>
<h2>Aggregate scorecard</h2>
<div class="panel">{_scorecard(facts)}</div>
<h2>Results by persona type</h2>
{_cohort_section(facts, cohort_blurbs)}
<h2>Per-persona detail</h2>
{cards}
<h2>Analysis &amp; recommendations</h2>
<div class="panel">{_bullets(recommendations, "No recommendations available.")}</div>
<p class="foot">Generated deterministically; prose fields by {_esc(cfg.get("report_model"))}. Facts from MLflow experiment {_esc(exp.get("id"))}.</p>
</div></body></html>"""
