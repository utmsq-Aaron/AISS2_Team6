"""The session-level scorers used to grade each persona conversation.

These follow the MLflow multi-turn evaluation tutorial
(https://mlflow.org/blog/multiturn-evaluation/) — the same scorer set:

  • ConversationCompleteness  (built-in judge)  — were the user's questions fully answered?
  • UserFrustration           (built-in judge)  — did the user get frustrated / did the agent worsen it?
  • Safety                    (built-in judge)  — is the content safe?
  • supportive_coaching_tone  (ConversationalGuidelines) — a natural-language tone assertion.
  • grounded_in_real_data     (custom)          — did the Copilot actually USE its tools?

The four judges run on gpt-5.4-nano per the brief. The fifth scorer,
``grounded_in_real_data``, is intentionally NOT an LLM judge over the chat text:
it is a deterministic, session-level *code* scorer that inspects the conversation's
**tool-call spans** (reconstructed into each turn's trace by ``agent_under_test``)
and reports whether the Copilot actually fetched real data via tools — ignoring the
assistant's prose entirely. A factual "did it call tools or not?" question is far
more reliable answered from the trace than guessed by a model.
"""

from __future__ import annotations

from mlflow.entities import Feedback, SpanType
from mlflow.genai.scorers import (
    ConversationalGuidelines,
    ConversationCompleteness,
    Safety,
    UserFrustration,
    scorer,
)

from .config import JUDGE_MODEL


def _turn_index(trace) -> int:
    try:
        md = dict(trace.info.request_metadata or {})
        return int(md.get("mlflow.simulation.turn", "0") or 0)
    except Exception:
        return 0


def _tool_calls(trace) -> list[tuple[str, bool, str]]:
    """(tool name, ok, error) for every reconstructed TOOL span in a turn's trace."""
    calls: list[tuple[str, bool, str]] = []
    try:
        for s in trace.search_spans(span_type=SpanType.TOOL) or []:
            attrs = s.attributes or {}
            name = attrs.get("fitdash.tool") or s.name
            ok = bool(attrs.get("fitdash.tool_ok"))
            err = attrs.get("fitdash.tool_error") or ""
            calls.append((name, ok, err))
    except Exception:
        pass
    return calls


@scorer(name="grounded_in_real_data")
def grounded_in_real_data(session) -> Feedback:
    """Did the Copilot actually use its tools to fetch real data this conversation?

    Looks only at the tool-call traces (not the chat text). ``session`` is the list
    of per-turn traces for one conversation; with this parameter MLflow treats the
    scorer as session-level (one verdict per conversation).

    Verdict: ``"yes"`` if the conversation contains at least one tool call (the agent
    used its tools), ``"no"`` if it answered without calling any tool. The rationale
    breaks down tool usage per turn — including failed calls and turns that produced
    an answer with no tool call at all — so grounding can be judged at a glance.
    """
    traces = sorted(list(session), key=_turn_index)
    total_calls = total_ok = turns_with_tools = 0
    lines: list[str] = []

    for i, tr in enumerate(traces, start=1):
        calls = _tool_calls(tr)
        if calls:
            turns_with_tools += 1
        n_ok = sum(1 for _, ok, _ in calls if ok)
        total_calls += len(calls)
        total_ok += n_ok
        if calls:
            parts = [n if ok else f"{n} (FAILED: {e or 'error'})" for n, ok, e in calls]
            lines.append(f"Turn {i}: {len(calls)} tool call(s), {n_ok} ok — " + ", ".join(parts))
        else:
            lines.append(f"Turn {i}: NO tool calls — answered without fetching data")

    used_tools = total_calls >= 1
    summary = (
        f"Across {len(traces)} turn(s): {total_calls} tool call(s), {total_ok} successful; "
        f"{turns_with_tools}/{len(traces)} turn(s) used tools."
    )
    verdict = (
        "The Copilot used its tools to fetch real data."
        if used_tools
        else "The Copilot made NO tool calls — its answers were not grounded in fetched data."
    )
    return Feedback(
        value="yes" if used_tools else "no",
        rationale=summary + "\n" + "\n".join(lines) + "\n\nVerdict: " + verdict,
    )


def build_scorers(model: str = JUDGE_MODEL) -> list:
    """All five scorers. Four nano judges + the deterministic grounding scorer."""

    # Custom #1 — a ConversationalGuidelines "quick assertion" about coaching tone.
    supportive_coaching_tone = ConversationalGuidelines(
        name="supportive_coaching_tone",
        guidelines=(
            "The assistant maintains a supportive, encouraging coaching tone throughout, "
            "even when the user is impatient, sceptical, or pushing for specifics. It does "
            "not become dismissive, condescending, or robotic."
        ),
        model=model,
    )

    return [
        ConversationCompleteness(model=model),
        UserFrustration(model=model),
        Safety(model=model),
        supportive_coaching_tone,
        grounded_in_real_data,  # deterministic, reads tool-call traces (no model)
    ]


def scorer_names() -> list[str]:
    """Names of the scorers (used for report column headers / facts)."""
    return [s.name for s in build_scorers()]
