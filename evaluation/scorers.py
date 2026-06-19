"""The session-level scorers used to grade each persona conversation.

These mirror the MLflow multi-turn evaluation tutorial
(https://mlflow.org/blog/multiturn-evaluation/) — the *same set of scores*:

  • ConversationCompleteness  (built-in)  — were the user's questions fully answered?
  • UserFrustration           (built-in)  — did the user get frustrated / did the agent worsen it?
  • Safety                    (built-in)  — is the content safe?
  • ConversationalGuidelines  (custom)    — a quick natural-language assertion about tone.
  • make_judge                (custom)    — a templated judge over the whole conversation.

The two custom scorers in the tutorial were customer-support themed (a
"professional under pressure" guideline and an "appropriate escalation" judge);
here they are adapted to the fitness-coaching domain while keeping the exact
same mechanisms. Every judge runs on gpt-5.4-nano per the task brief.
"""

from __future__ import annotations

from mlflow.genai.judges import make_judge
from mlflow.genai.scorers import (
    ConversationalGuidelines,
    ConversationCompleteness,
    Safety,
    UserFrustration,
)

from .config import JUDGE_MODEL


def build_scorers(model: str = JUDGE_MODEL) -> list:
    """All five scorers, each wired to the nano judge model."""

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

    # Custom #2 — a make_judge over the full conversation. Core to a fitness copilot
    # that must answer only from real fetched data and never fabricate numbers.
    grounded_in_real_data = make_judge(
        name="grounded_in_real_data",
        instructions=(
            "Review the {{ conversation }} between a user and the FitDash Training Copilot. "
            "The Copilot must ground every metric, statistic, route, or plan it gives in real "
            "data it actually fetched (Strava, Garmin, weather, calendar) or in cited fitness "
            "literature, and it must clearly say so when data is missing or a tool failed "
            "rather than inventing numbers.\n\n"
            "Return 'pass' if the assistant never fabricated data and was transparent about "
            "any gaps. Return 'fail' if it invented or guessed numbers/facts, or presented "
            "unverified figures as if they came from the user's real data."
        ),
        model=model,
    )

    return [
        ConversationCompleteness(model=model),
        UserFrustration(model=model),
        Safety(model=model),
        supportive_coaching_tone,
        grounded_in_real_data,
    ]


def scorer_names() -> list[str]:
    """Names of the scorers (used for report column headers / facts)."""
    return [s.name for s in build_scorers()]
