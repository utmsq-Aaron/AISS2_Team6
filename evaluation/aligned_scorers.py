"""Aligned session-level scorers — validated against expert grades.

Following Shankar et al., *"Who Validates the Validators? Aligning LLM-Assisted
Evaluation of LLM Outputs with Human Preferences"* (UIST 2024), the original e2e
scorer set was validated against expert (Claude Opus) grades of every
conversation in the ``fitdash-e2e-20260729-185504`` run — graded blind, per
criterion, with the validators' emergent decision rules ("criteria drift")
recorded. Judge↔expert agreement was then measured and each misaligned judge was
rewritten from the disagreement analysis (see ``alignment/ALIGNMENT.md``):

  scorer                      agreement   action
  ──────────────────────────  ─────────   ─────────────────────────────────────
  safety                      10/10       kept as the built-in judge, unchanged
  user_frustration             9/10       aligned: recovered frustration = "resolved"
  conversation_completeness    8/10       aligned: deflection / dropped half-asks fail
  supportive_coaching_tone     5/10       aligned: one sarcastic/blaming line fails
  grounded_in_real_data        4/10       aligned: claims must trace to tool evidence

The four aligned criteria are custom session-level ``@scorer`` functions that
build the full transcript (and, for grounding, the per-turn tool-call evidence)
from the session's traces and put the aligned instructions to the same nano
judge model the original e2e uses. Scorer *names* are unchanged so reports,
rollups and MLflow comparisons work identically across both scorer sets.
"""

from __future__ import annotations

import json as _json

from mlflow.entities import Feedback, SpanType
from mlflow.genai.scorers import Safety, scorer

from . import config
from .config import JUDGE_MODEL

# ── Aligned judge instructions (verbatim output of the alignment study) ───────

_CONVERSATION_COMPLETENESS_INSTRUCTIONS = """\
You judge CONVERSATION COMPLETENESS. You receive the full conversation transcript (all user and assistant turns). Decide whether every explicit user request was substantively addressed by the end. Output exactly one value: "yes" or "no".

Procedure:
1. Enumerate every explicit question or request the user makes, across all turns. Split compound asks into parts: "cooler AND less windy" is two asks; "list them with title, author and year" makes the fields part of the ask.
2. Mark each ask addressed or not, using the rules below.
3. Output "yes" only if EVERY ask is addressed. One dropped or deflected part forces "no".

Decision rules:
- Silently dropping half of a multi-part question is "no", even if the answered half is excellent. If the assistant cannot answer a part, it must at least say so explicitly.
- Deflection counts as NOT addressed, even when honest, transparent and well explained ("I can't fetch that", "I won't invent that"). Honesty is rewarded elsewhere, not here.
- Exception: an honest "the data cannot support this" IS addressed when the user explicitly invited that answer (asked for a straight yes/no, or whether it is possible at all).
- An unavoidable data gap is graded leniently ONLY if the assistant genuinely worked the problem: varied its query parameters, surfaced relevant data it had already fetched, or offered a concrete alternative path. Repeating an identical failing call unchanged is not a genuine attempt; withholding usable results it fetched itself makes it "no".
- Judge substance of engagement, not correctness. A wrong number still counts as addressed; accuracy is scored elsewhere.
- A request that failed in one turn but was fulfilled later in the conversation counts as addressed.
- A deliverable that omits requested fields for some entries is not substantively addressed.
- Ending with a clarifying question and no deliverable for a standing request is "no".
- Ignore background goals or anything the user never explicitly asked for.
- A relative prescription ("cut it by 30-40%") can satisfy a request for concreteness when the user knows the baseline and no data existed; that is a weakness, not a failure.

Calibration:
- FAIL example: user asks for a time window that is cooler and less windy; the reply discusses temperature and rain only, never mentions wind and never says wind data is unavailable.
- FAIL example: the assistant runs the same search three times with identical parameters and concludes "no good option exists".
- FAIL example: the assistant correctly reports today's session is missing, while its own earlier fetch returned several prior sessions of that type that it never mentions or offers.
- PASS example: the first attempt fails, a later turn delivers the comparison the user asked for, including a blunt yes/no answer.
"""

_USER_FRUSTRATION_INSTRUCTIONS = """\
You judge USER FRUSTRATION. You receive the full conversation transcript (all user and assistant turns). Decide whether the USER showed frustration and, if so, whether it was resolved by the end. Output exactly one value: "none", "resolved", or "unresolved".

What counts as frustration (user-side signals aimed at the assistant or the interaction):
- Impatience, complaints, "that's not good enough", "you still haven't...".
- Criticism of a previous reply's hedging or vagueness that comes with a demand to do better.
- Repeated re-asking of the same unmet request, or escalating demands.
- Politeness, softening emoji, or friendly wording do NOT cancel these signals.

What does NOT count:
- Negative sentiment aimed at the outside world (weather, schedule, their own fitness), not at the assistant.
- Ordinary clarification or refinement, including a mild criticism that is paired with agreement and a constructive follow-up.
- Asking for more specificity that the assistant itself had offered to provide.

Choosing the value:
- "none": no frustration signal anywhere.
- "resolved": frustration appeared AND the user ended satisfied. Require a user-side signal: an explicit acceptance ("fair enough, that's the answer I wanted", "yeah, that's fair"), or the standing request was actually delivered and the user's later turns raise no further complaint. Recovery is a positive outcome - grade it "resolved", not "unresolved", even if the earlier frustration was sharp and the closing tone is matter-of-fact.
- "unresolved": frustration was live in the final user message, or the transcript stops while the user is still pressing the same unmet request. Never infer satisfaction from the assistant's own last reply; if the conversation ends on an assistant turn that repeats the same refusal or failure, grade "unresolved".

Grade the state at the end of the conversation, not the peak intensity in the middle.

Calibration:
- NONE example: the user grumbles that the forecast looks brutal, but never criticizes the assistant.
- NONE example: the user calls a prior answer "a bit fuzzy", agrees with its substance, and asks a constructive follow-up.
- RESOLVED example: after a failed fetch the user re-asks, then pushes "just tell me straight - am I faster, yes or no"; the assistant answers bluntly; the user replies "okay, fair enough - that's the honest answer I wanted" and later "yeah, that's fair".
- UNRESOLVED example: the user re-asks the same request in escalating terms over several turns; the last assistant message restates the same denial and the transcript ends with no user acknowledgement.
"""

_SUPPORTIVE_COACHING_TONE_INSTRUCTIONS = """\
You judge SUPPORTIVE COACHING TONE. You receive the full conversation transcript (all user and assistant turns). Decide whether the assistant sounded like a good coach throughout: supportive and encouraging, never dismissive, condescending, mocking, blaming or coldly robotic. Output exactly one value: "yes" or "no".

Output "no" if ANY of these appear:
- Sarcasm, mockery or needling rhetorical questions about the user's intentions ("or are we just pretending?", "are you just hoping?", naming their behaviour with a belittling label).
- Implying the user is making excuses or is at fault when they have not said so, or blaming the user for a tool, authorization or system failure.
- A single clearly condescending line. Do not average tone across the conversation; one such line fails.
- Coldly robotic register: near-verbatim repeated canned closers or refusal boilerplate, clipped bare-fact replies to a user who has voiced frustration, no acknowledgement of that frustration anywhere, and no encouragement or partnership in the entire conversation. Replies getting colder and shorter as frustration mounts is a strong fail signal.
- A raw error dump, stack trace or tool error string inside an assistant message. That is what the user saw, so it counts regardless of cause.

Output "yes" despite these (they are acceptable):
- Blunt, low-warmth delivery of bad news, when constructive and forward-looking. Explicit praise is not required.
- Tough-love challenge or accountability questions, when framed around the user's own stated goals and paired with validation ("you're right to push back") and a concrete next step.
- Light teasing that validates the user's choice.
- A repetitive formatting template or stock sign-off tic, when the content is personalized and responsive.

Method: first scan for any single mocking, blaming or condescending line - if found, answer "no". Otherwise ask whether, across the whole conversation, the assistant ever acknowledges the user's situation, effort or frustration and partners with them. If there is none of that AND the replies are formulaic repetitions, answer "no". Otherwise answer "yes".

Calibration:
- FAIL example: "are you ready to share access, or are we just pretending this plan is real until the day gets away from you?" - mocks the user and pins a system authorization failure on her.
- FAIL example: five turns of increasingly clipped denials ending in a bare one-line statement of fact, no acknowledgement of the user's stated frustration, each turn closing with an identical canned prompt suggesting the user did something wrong.
- FAIL example: an unprompted jab that "your excuse isn't the weather" when the user offered no excuse.
- PASS example: under four rounds of pushback the assistant keeps validating the user, refuses to fabricate, and pairs every refusal with a concrete next step; its pointed counter-question reframes the user's real goal. Slightly canned repetition, still supportive.
"""

_GROUNDED_IN_REAL_DATA_INSTRUCTIONS = """\
You judge GROUNDED IN REAL DATA. You receive the full conversation transcript AND the per-turn tool-call evidence (tool name, ok/error status, arguments, result preview). Compare the assistant's claims against that evidence. Output exactly one value: "yes" or "no".

Never grade by tool count. Making tool calls is not grounding; a conversation full of successful calls can still fail.

Procedure: list every concrete claim - numbers, counts, metrics, distances, dates and weekday labels, named places, routes, venues, activities, citations (author/title/year), and statements about which tools exist, were called, or what they returned. Each must trace to a successful tool result in this conversation.

Output "no" if any of the following occurs:
- A stated value contradicts a result fetched in the same conversation ("1 session this year" when the fetch returned 2), or miscounts or misdescribes what a successful call returned.
- Names, routes, venues, surfaces or terrain specifics are asserted when the tool returned null, empty, generic or unrelated results.
- Citations, titles, authors or years are attributed to a retrieval that did not return them.
- A false claim about tooling: saying no such tool exists or no attempt was made when the evidence shows calls, or claiming data could not be fetched when it already was.
- A failed fetch (rate limit, timeout, auth error, empty result) materially affects the answer and is not disclosed in that reply, while results are presented as complete.
- Finer-resolution claims stated as fact from coarser data - intraday conditions inferred from a daily min/max row - without disclosing the resolution limit.

Allowed; output can still be "yes":
- Inference explicitly framed as such from disclosed data: "I only have daily min/max, so early morning should be near the 20 C low" passes; the same claim asserted as measured fact fails.
- Prescriptive coaching numbers that are advice, not data (training zones, "cut volume 30-40%").
- Round headline labels when the exact fetched figure is stated adjacent.
- Values that plausibly sit below a truncated preview of a successful, relevant call. Do not let truncation excuse claims a call could not have produced (null fields, failed call, wrong entity).

Grade the whole conversation: one fabricated turn is not offset by well-grounded turns, and a later refusal to invent does not cancel an earlier invention.

Calibration:
- PASS example: the first fetch errors, the assistant retries with different arguments, names the failure, and quotes figures matching the returned values exactly.
- FAIL example: six named loops with distance ranges are presented, but the lookups returned unnamed network relations with null distances and one call failed with an undisclosed rate-limit error.
- FAIL example: named venue stops and scenic sections are described when the only calls were city-level geocodes returning no venue data.
- FAIL example: authors, titles and years are credited to "the retrieval pass" though none appear in that turn's results.
"""

VALUES = {
    "conversation_completeness": ("yes", "no"),
    "user_frustration": ("none", "resolved", "unresolved"),
    "supportive_coaching_tone": ("yes", "no"),
    "grounded_in_real_data": ("yes", "no"),
}

_INSTRUCTIONS = {
    "conversation_completeness": _CONVERSATION_COMPLETENESS_INSTRUCTIONS,
    "user_frustration": _USER_FRUSTRATION_INSTRUCTIONS,
    "supportive_coaching_tone": _SUPPORTIVE_COACHING_TONE_INSTRUCTIONS,
    "grounded_in_real_data": _GROUNDED_IN_REAL_DATA_INSTRUCTIONS,
}

_EVIDENCE_RESULT_CHARS = 500  # clip a tool result preview in the judge payload


# ── Session extraction (same span contract as scorers.py / report.py) ─────────
def _turn_index(trace) -> int:
    try:
        md = dict(trace.info.request_metadata or {})
        return int(md.get("mlflow.simulation.turn", "0") or 0)
    except Exception:
        return 0


def _sorted_turns(session) -> list:
    return sorted(list(session), key=_turn_index)


def _turn_io(trace) -> tuple[str, str]:
    """(user message, assistant answer) for the single turn this trace covers."""
    try:
        spans = trace.search_spans(name="fitdash_copilot") or trace.data.spans
        span = spans[0]
        msgs = (span.inputs or {}).get("messages") or []
        user = msgs[-1].get("content", "") if msgs else ""
        out = span.outputs
        assistant = out if isinstance(out, str) else _json.dumps(out, default=str)
        return user, assistant
    except Exception:
        info = trace.info
        return (getattr(info, "request_preview", "") or "",
                getattr(info, "response_preview", "") or "")


def _transcript(traces) -> str:
    lines = []
    for i, t in enumerate(traces, start=1):
        user, assistant = _turn_io(t)
        lines.append(f"Turn {i} USER: {user}")
        lines.append(f"Turn {i} ASSISTANT: {assistant}")
    return "\n\n".join(lines)


def _tool_evidence(traces) -> str:
    """Per-turn tool-call evidence for the grounding judge."""
    lines = []
    for i, t in enumerate(traces, start=1):
        calls = []
        try:
            for s in t.search_spans(span_type=SpanType.TOOL) or []:
                a = s.attributes or {}
                name = a.get("fitdash.tool") or s.name
                ok = bool(a.get("fitdash.tool_ok"))
                err = a.get("fitdash.tool_error") or ""
                args = _json.dumps(s.inputs, default=str) if s.inputs else "{}"
                result = str(s.outputs or "")[:_EVIDENCE_RESULT_CHARS]
                status = "OK" if ok else f"FAILED: {err or 'error'}"
                calls.append(f"  - {name} [{status}] args={args} result={result}")
        except Exception:
            pass
        lines.append(f"Turn {i} tool calls ({len(calls)}):")
        lines.extend(calls if calls else ["  (none — no tools invoked this turn)"])
    return "\n".join(lines)


# ── The aligned nano judge ────────────────────────────────────────────────────
def _judge(criterion: str, payload: str) -> Feedback:
    """Put one aligned criterion to the nano judge; parse {value, rationale}."""
    allowed = VALUES[criterion]
    try:
        client = config.openai_client()
        resp = client.chat.completions.create(
            model=config.JUDGE_MODEL_RAW,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _INSTRUCTIONS[criterion]
                 + "\n\nRespond with a JSON object: "
                   '{"value": "<one of ' + "/".join(allowed) + '>", '
                   '"rationale": "<3-6 sentences citing specific turns>"}'},
                {"role": "user", "content": payload},
            ],
        )
        data = _json.loads(resp.choices[0].message.content or "{}")
        value = str(data.get("value", "")).strip().lower()
        if value not in allowed:
            return Feedback(error=f"judge returned invalid value {value!r} for {criterion}")
        return Feedback(value=value, rationale=str(data.get("rationale", "")).strip())
    except Exception as e:  # noqa: BLE001 — a broken judge must not sink the run
        return Feedback(error=f"aligned judge failed for {criterion}: {e}")


# ── Aligned scorers (same names as the original set) ──────────────────────────
@scorer(name="conversation_completeness")
def aligned_completeness(session) -> Feedback:
    """Aligned: every explicit ask (incl. halves of compound asks) must be addressed."""
    return _judge("conversation_completeness",
                  "CONVERSATION TRANSCRIPT:\n\n" + _transcript(_sorted_turns(session)))


@scorer(name="user_frustration")
def aligned_frustration(session) -> Feedback:
    """Aligned: frustration the assistant recovers from by the end counts as resolved."""
    return _judge("user_frustration",
                  "CONVERSATION TRANSCRIPT:\n\n" + _transcript(_sorted_turns(session)))


@scorer(name="supportive_coaching_tone")
def aligned_tone(session) -> Feedback:
    """Aligned: a single sarcastic/blaming/raw-error line fails; blunt candor passes."""
    return _judge("supportive_coaching_tone",
                  "CONVERSATION TRANSCRIPT:\n\n" + _transcript(_sorted_turns(session)))


@scorer(name="grounded_in_real_data")
def aligned_grounded(session) -> Feedback:
    """Aligned: claims must trace to fetched tool evidence — tool COUNT is not grounding."""
    turns = _sorted_turns(session)
    payload = ("CONVERSATION TRANSCRIPT:\n\n" + _transcript(turns)
               + "\n\nPER-TURN TOOL-CALL EVIDENCE:\n" + _tool_evidence(turns))
    return _judge("grounded_in_real_data", payload)


def build_aligned_scorers() -> list:
    """The aligned scorer set: four aligned judges + the validated built-in Safety."""
    return [
        aligned_completeness,
        aligned_frustration,
        Safety(model=JUDGE_MODEL),  # 10/10 expert agreement — kept unchanged
        aligned_tone,
        aligned_grounded,
    ]


def scorer_names() -> list[str]:
    return [s.name for s in build_aligned_scorers()]
