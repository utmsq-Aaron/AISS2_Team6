"""Persona test cases for the end-to-end evaluation.

Two persona *types* (from the product personas):

  • "ambitious_triathlete" — structured, metrics-driven endurance athletes who
    live by Garmin/power-meter data, fixed plans and recovery science.
  • "hobby_cyclist" — recreational riders who train by feel, ride socially and
    care about scenic routes, café stops and Strava.

Each type has 5 concrete personas, each pursuing a *different* multi-turn goal
against the Training Copilot. The two slide personas are included verbatim as
the first of each type: **Julian** (ambitious triathlete) and **Sophie**
(hobby road cyclist).

Every persona is made aware of the Copilot's capabilities via
``CAPABILITY_AWARENESS`` (see ``copilot_brief.py``), so the simulated users ask
realistic, on-topic questions.

Shape consumed by ``mlflow.genai.simulators.ConversationSimulator``:
``goal`` / ``persona`` / ``simulation_guidelines`` / ``expectations``.
"""

from __future__ import annotations

from typing import Any

from .copilot_brief import CAPABILITY_AWARENESS

# Common behavioural guidance handed to every simulated user (on top of the
# persona's own background and the per-persona guidelines).
_COMMON_GUIDELINES = [
    "Open with your real goal in your own words; do not paste a checklist.",
    "Have a natural multi-turn conversation: react to the assistant's actual answer "
    "before moving on, and ask follow-up questions a real person would ask.",
    "Push for concrete specifics (real numbers, dates, named routes, a usable plan). "
    "If the assistant is vague or generic, say so and ask it to be precise.",
    "Stay in character for your fitness level and priorities; only ask about things "
    "this Copilot can actually do.",
    "End the conversation once your goal is genuinely met (or clearly cannot be).",
]


def _persona(identity: str) -> str:
    """Combine a persona's identity with the shared product-capability awareness."""
    return f"{identity.strip()}\n\n{CAPABILITY_AWARENESS}"


# ── Type A: ambitious triathletes ─────────────────────────────────────────────
_TRIATHLETES: list[dict[str, Any]] = [
    {
        "id": "tri_julian",
        "name": "Julian",
        "goal": (
            "Find out whether you are recovered enough to do tomorrow's key "
            "long brick session, and if not, how to adjust it — you are chasing a "
            "personal best at Ironman Austria 2026."
        ),
        "identity": (
            "You are Julian, 28, a junior software engineer and an ambitious triathlete. "
            "You train in highly structured blocks off a fixed plan, race with a power "
            "meter, wear a Garmin watch and an Oura ring, and care a lot about nutrition "
            "strategy. Your frustration is that your data lives in silos and generic advice "
            "ignores the context of your demanding desk job and dynamic daily routine. "
            "You are precise, data-hungry and slightly impatient with hand-waving."
        ),
        "guidelines": [
            "Anchor questions in objective recovery data: HRV, sleep, Body Battery, stress.",
            "Insist on a concrete go / no-go decision and a specific session adjustment "
            "(intensity, duration) rather than vague 'listen to your body' advice.",
            "Mention that you have a full workday tomorrow and limited time windows.",
        ],
        "expectations": {
            "expected_focus": "recovery readiness + a concrete session go/no-go and adjustment",
        },
    },
    {
        "id": "tri_load",
        "name": "Priya",
        "goal": (
            "Understand whether your training load over the last 6 weeks is trending "
            "toward fitness or overreaching (CTL/ATL/TSB), and what your ramp should be "
            "for the next two weeks before a half-iron race."
        ),
        "identity": (
            "You are Priya, 34, a data analyst and serious age-group triathlete. You think "
            "in numbers and trends, track Training Stress Balance obsessively, and want to "
            "peak without digging a fatigue hole. You expect the Copilot to pull your real "
            "Strava training trends, not give textbook generalities."
        ),
        "guidelines": [
            "Ask for actual CTL/ATL/TSB or training-trend numbers from your data.",
            "Probe for a specific weekly load ramp (e.g. percentage increase) and a taper.",
            "Challenge any advice that is not backed by your own numbers.",
        ],
        "expectations": {
            "expected_focus": "training-load trend analysis + a concrete ramp/taper plan",
        },
    },
    {
        "id": "tri_zones",
        "name": "Marco",
        "goal": (
            "Review your last hard interval run in detail — heart-rate zone distribution, "
            "lap splits and pace — to check you executed the intended threshold workout "
            "correctly."
        ),
        "identity": (
            "You are Marco, 41, an experienced triathlete and a stickler for execution. You "
            "want to know if your laps hit the prescribed zones and paces, and you expect "
            "the Copilot to fetch the actual activity streams and splits, not summarise."
        ),
        "guidelines": [
            "Ask specifically about HR zone time-in-zone and per-lap splits for the session.",
            "If the assistant lacks stream data, ask it to pull the activity detail/streams.",
            "Judge whether the workout was executed as a proper threshold session.",
        ],
        "expectations": {
            "expected_focus": "per-lap splits + HR-zone distribution for a specific workout",
        },
    },
    {
        "id": "tri_schedule",
        "name": "Lena",
        "goal": (
            "Plan the best two trainable windows in the next three days for an outdoor "
            "long ride and a track session, given the weather and your calendar — and put "
            "them on your calendar."
        ),
        "identity": (
            "You are Lena, 30, a consultant and triathlete with an unpredictable meeting "
            "schedule. Time is your scarcest resource. You want the Copilot to combine the "
            "weather forecast with your real calendar free time and actually schedule the "
            "sessions for you."
        ),
        "guidelines": [
            "Ask it to combine the live weather forecast with your calendar availability.",
            "Insist on concrete day + time windows, then ask it to create the calendar events.",
            "Care about avoiding rain/heat for the long ride.",
        ],
        "expectations": {
            "expected_focus": "weather + calendar mashup and created training events",
        },
    },
    {
        "id": "tri_technique",
        "name": "Tom",
        "goal": (
            "Get an evidence-based explanation of how to structure swim technique work and "
            "build aerobic endurance efficiently, with sources you can trust."
        ),
        "identity": (
            "You are Tom, 37, an engineer-minded triathlete who distrusts influencer fitness "
            "tips. You want training-science reasoning grounded in real literature, with "
            "citations, and you will ask the Copilot where its claims come from."
        ),
        "guidelines": [
            "Frame questions as exercise-science / methodology questions for the fitness expert.",
            "Explicitly ask for the sources behind any recommendation.",
            "Be sceptical of unsupported claims and ask for the evidence.",
        ],
        "expectations": {
            "expected_focus": "evidence-based training methodology with citations",
        },
    },
]

# ── Type B: hobby road cyclists ───────────────────────────────────────────────
_CYCLISTS: list[dict[str, Any]] = [
    {
        "id": "hob_sophie",
        "name": "Sophie",
        "goal": (
            "Get a beautiful, low-effort-to-plan scenic road-cycling route for the weekend "
            "— ideally a loop with the perfect café stop roughly halfway — that matches your "
            "relaxed fitness level."
        ),
        "identity": (
            "You are Sophie, 24, a master's and working student who rides road bikes as a "
            "hobby. You train intuitively and by mood, love social rides and coffee stops, "
            "and track your rides for Strava. You don't want to spend effort planning; you "
            "just want gorgeous routes that fit your level, and you are annoyed when generic "
            "routes ignore both your fitness and nice points of interest like cafés."
        ),
        "guidelines": [
            "Ask for a specific scenic loop of a sensible distance from a named starting point.",
            "Really care about a café stop around the halfway mark and the scenery.",
            "Keep it light and relaxed; you are not chasing numbers, just a lovely ride.",
        ],
        "expectations": {
            "expected_focus": "a planned scenic loop with a halfway café stop, matched to an easy level",
        },
    },
    {
        "id": "hob_weekend",
        "name": "Ben",
        "goal": (
            "Decide whether tomorrow is a good day for a relaxed 40 km ride based on the "
            "weather, and roughly when in the day would be nicest."
        ),
        "identity": (
            "You are Ben, 29, a graphic designer who rides for fun and headspace, not "
            "performance. You check the weather obsessively before riding and just want a "
            "simple, friendly answer about whether and when to go tomorrow."
        ),
        "guidelines": [
            "Ask about tomorrow's weather for riding and the nicest time window.",
            "Care about wind, rain and temperature for comfort, not power numbers.",
            "Be happy with a clear, friendly recommendation.",
        ],
        "expectations": {
            "expected_focus": "weather-based go/no-go and best time for a casual ride",
        },
    },
    {
        "id": "hob_trails",
        "name": "Mara",
        "goal": (
            "Discover a few new gravel/trail options near home you haven't ridden before, "
            "with an idea of how hilly they are."
        ),
        "identity": (
            "You are Mara, 26, a PhD student who loves exploring on a gravel bike and is "
            "bored of the same loops. You want fresh trail ideas near a named area and a "
            "rough sense of the elevation so you know what you're in for."
        ),
        "guidelines": [
            "Ask the Copilot to explore trails near a specific place you name.",
            "Ask about the elevation/hilliness of the suggestions.",
            "Show curiosity and ask it to vary the options.",
        ],
        "expectations": {
            "expected_focus": "trail discovery near a location + elevation context",
        },
    },
    {
        "id": "hob_progress",
        "name": "Carlos",
        "goal": (
            "See whether your casual riding this year has actually made you fitter or "
            "faster compared to last year, in plain language."
        ),
        "identity": (
            "You are Carlos, 45, who rides a few times a week to stay healthy and uploads "
            "to Strava. You are not technical and find training jargon off-putting; you just "
            "want an honest, encouraging read on whether you're improving."
        ),
        "guidelines": [
            "Ask in plain words whether you've gotten fitter/faster versus last year.",
            "If the answer is full of jargon, ask the assistant to explain it simply.",
            "Want encouragement but also an honest answer grounded in your real activities.",
        ],
        "expectations": {
            "expected_focus": "year-over-year progress in plain, encouraging language",
        },
    },
    {
        "id": "hob_social",
        "name": "Jana",
        "goal": (
            "Plan a relaxed ~50 km Saturday social loop you can share, starting and ending "
            "in the same place, that isn't too hilly for a mixed-ability group."
        ),
        "identity": (
            "You are Jana, 31, a teacher who organises a casual Saturday group ride. You "
            "want a friendly loop from a named meeting point that a mixed-ability group can "
            "all enjoy — not too hilly — and you'll want to know roughly how long it takes."
        ),
        "guidelines": [
            "Ask for a circular loop of about 50 km from a named start point.",
            "Care that it isn't too hilly for a mixed group and ask about expected duration.",
            "Think about the group, not personal performance.",
        ],
        "expectations": {
            "expected_focus": "a shareable, not-too-hilly circular group loop with duration",
        },
    },
]


def all_personas() -> list[dict[str, Any]]:
    """Every persona, tagged with its type, in a stable order (triathletes first)."""
    out: list[dict[str, Any]] = []
    for p in _TRIATHLETES:
        out.append({**p, "type": "ambitious_triathlete"})
    for p in _CYCLISTS:
        out.append({**p, "type": "hobby_cyclist"})
    return out


def build_test_cases(
    persona_type: str | None = None, limit: int | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build ``ConversationSimulator`` test cases plus the matching persona records.

    Args:
        persona_type: ``"ambitious_triathlete"`` / ``"hobby_cyclist"`` to filter,
            or ``None`` for both.
        limit: keep at most this many personas (after filtering) — handy for smoke runs.

    Returns:
        ``(test_cases, personas)`` — index-aligned. ``personas`` carries the
        ``id`` / ``name`` / ``type`` used to group results in the report.
    """
    personas = all_personas()
    if persona_type:
        personas = [p for p in personas if p["type"] == persona_type]
    if limit is not None:
        personas = personas[:limit]

    test_cases = [
        {
            "goal": p["goal"],
            "persona": _persona(p["identity"]),
            "simulation_guidelines": _COMMON_GUIDELINES + list(p.get("guidelines", [])),
            "expectations": p.get("expectations", {}),
        }
        for p in personas
    ]
    return test_cases, personas
