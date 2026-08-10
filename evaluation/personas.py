"""Persona test cases for the end-to-end evaluation.

The persona matrix is **three sports × two levels × two concrete personas**
(12 personas in total):

  • sports: ``cyclist`` / ``runner`` / ``swimmer``
  • levels: ``hobby`` (casual, plain-language, enjoyment- and consistency-driven)
    and ``ambitious`` (structured, data-hungry, chasing a concrete race goal)

which yields the six persona *types* ``hobby_cyclist``, ``ambitious_cyclist``,
``hobby_runner``, ``ambitious_runner``, ``hobby_swimmer``, ``ambitious_swimmer``
(the type string is always ``f"{level}_{sport}"``). Each type holds 2 personas,
each pursuing a *different* multi-turn goal against the Training Copilot; taken
together the 12 goals exercise recovery, training load & trends, activity
detail, route planning, weather + calendar scheduling and the fitness-literature
RAG. Route planning only covers running/cycling/hiking, so the swimmers never
ask for a swim route — they use literature, load from uploaded swim activities,
recovery readiness, weather/calendar scheduling and cross-training instead.

The slide persona **Sophie** (hobby road cyclist) is included verbatim as the
first ``hobby_cyclist``.

Every persona is made aware of the Copilot's capabilities via
``CAPABILITY_AWARENESS`` (see ``copilot_brief.py``), so the simulated users ask
realistic, on-topic questions.

Shape consumed by ``mlflow.genai.simulators.ConversationSimulator``:
``goal`` / ``persona`` / ``simulation_guidelines`` / ``expectations``.
"""

from __future__ import annotations

from typing import Any

from .copilot_brief import CAPABILITY_AWARENESS

SPORTS = ("cyclist", "runner", "swimmer")
LEVELS = ("hobby", "ambitious")

#: The six persona types, in the canonical order used everywhere downstream.
PERSONA_TYPES = (
    "hobby_cyclist",
    "ambitious_cyclist",
    "hobby_runner",
    "ambitious_runner",
    "hobby_swimmer",
    "ambitious_swimmer",
)

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


# ── hobby cyclists ────────────────────────────────────────────────────────────
_HOBBY_CYCLISTS: list[dict[str, Any]] = [
    {
        "id": "hob_cyc_sophie",
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
        "id": "hob_cyc_ben",
        "name": "Ben",
        "goal": (
            "Work out which day and time in the next three days is actually nice for a "
            "relaxed 40 km ride, given the weather and the gaps in your calendar, and get "
            "that slot put into your calendar so you finally go."
        ),
        "identity": (
            "You are Ben, 29, a graphic designer who rides for fun and headspace, not "
            "performance. You check the weather obsessively, keep postponing rides because "
            "something always lands in your calendar, and you want a simple, friendly answer "
            "about when to go. Numbers like watts or TSS mean nothing to you; wind, rain and "
            "temperature do."
        ),
        "guidelines": [
            "Ask the Copilot to combine the forecast with your free time in the calendar.",
            "Care about wind, rain and temperature for comfort, not power numbers.",
            "Once you like a slot, ask it to actually put the ride in your calendar.",
        ],
        "expectations": {
            "expected_focus": "weather + calendar mashup that ends in a scheduled casual ride",
        },
    },
]

# ── ambitious cyclists ────────────────────────────────────────────────────────
_AMBITIOUS_CYCLISTS: list[dict[str, Any]] = [
    {
        "id": "amb_cyc_priya",
        "name": "Priya",
        "goal": (
            "Understand whether your training load over the last 8 weeks is building fitness "
            "or tipping into overreaching (CTL/ATL/TSB), and get a concrete weekly ramp plus "
            "taper for the last three weeks before your target granfondo."
        ),
        "identity": (
            "You are Priya, 34, a data analyst and a serious amateur road cyclist targeting a "
            "sub-6-hour finish at a 160 km granfondo this season. You think in numbers and "
            "trends, watch Training Stress Balance obsessively, and want to peak without "
            "digging a fatigue hole. You expect the Copilot to pull your real Strava training "
            "trends rather than recite textbook generalities."
        ),
        "guidelines": [
            "Ask for actual CTL/ATL/TSB or training-trend numbers from your own activities.",
            "Probe for a specific weekly load ramp (percentages, hours) and a dated taper.",
            "Challenge any advice that is not backed by your own numbers.",
        ],
        "expectations": {
            "expected_focus": "training-load trend analysis + a concrete ramp and taper before the granfondo",
        },
    },
    {
        "id": "amb_cyc_marco",
        "name": "Marco",
        "goal": (
            "Design a hilly training loop with roughly 1200 m of climbing that mimics the "
            "profile of your goal granfondo, and see its elevation profile before you commit "
            "a whole Sunday to it."
        ),
        "identity": (
            "You are Marco, 41, a mechanical engineer and a long-time racing cyclist who "
            "prepares specifically: if the event has long climbs, your training rides must "
            "have long climbs too. You are precise about distance, vertical metres and "
            "gradient, and you distrust route suggestions that hide their climbing profile."
        ),
        "guidelines": [
            "Ask for a circular route from a place you name with a specific climbing target.",
            "Insist on the elevation profile — where the climbs sit, how steep, how long.",
            "Compare the suggestion against the demands of your goal event and ask for tweaks.",
        ],
        "expectations": {
            "expected_focus": "a race-specific climbing loop with a real elevation profile",
        },
    },
]

# ── hobby runners ─────────────────────────────────────────────────────────────
_HOBBY_RUNNERS: list[dict[str, Any]] = [
    {
        "id": "hob_run_lena",
        "name": "Lena",
        "goal": (
            "Find two or three fresh, mostly green 5–7 km running loops near you — ideally "
            "through a park and without brutal hills — because you are bored of the same "
            "street route."
        ),
        "identity": (
            "You are Lena, 30, a primary-school teacher who runs three times a week to clear "
            "her head. You run by feel, never look at pace, and mostly care about running "
            "somewhere pretty and safe. You are bored of your one usual loop and would like "
            "variety without having to study a map."
        ),
        "guidelines": [
            "Ask for loops that start and end at a place you name, and mention a nearby park.",
            "Care about scenery, surface and not too many hills; ask how hilly each option is.",
            "Ask for a couple of different options so you can vary your week.",
        ],
        "expectations": {
            "expected_focus": "several easy park/green running loops with hilliness context",
        },
    },
    {
        "id": "hob_run_carlos",
        "name": "Carlos",
        "goal": (
            "Find out, in plain language, whether the running you have done since January "
            "has actually made you fitter — and whether your 5 km has really got faster."
        ),
        "identity": (
            "You are Carlos, 45, an accountant who started running to get healthier and "
            "uploads everything to Strava without ever looking at the graphs. Training jargon "
            "puts you off; you just want an honest, encouraging read on whether the effort is "
            "paying off, in words you would use yourself."
        ),
        "guidelines": [
            "Ask in plain words whether you have got fitter and faster since the start of the year.",
            "Ask about your best 5 km time and whether it has improved.",
            "If the answer is full of jargon, say so and ask for it in simple language.",
        ],
        "expectations": {
            "expected_focus": "year-to-date progress and 5 km personal best in plain, encouraging language",
        },
    },
]

# ── ambitious runners ─────────────────────────────────────────────────────────
_AMBITIOUS_RUNNERS: list[dict[str, Any]] = [
    {
        "id": "amb_run_nora",
        "name": "Nora",
        "goal": (
            "Pick apart your last threshold run — lap splits, pace and heart-rate zone "
            "distribution — to judge whether you actually executed it as prescribed or ran "
            "it too hard."
        ),
        "identity": (
            "You are Nora, 33, a hospital pharmacist chasing a sub-2:59 marathon in Berlin, "
            "and a stickler for execution. You follow a written plan with prescribed paces and "
            "zones, and you want the Copilot to fetch the actual splits and time-in-zone from "
            "the session rather than summarise how it 'felt'."
        ),
        "guidelines": [
            "Ask specifically for per-lap splits and heart-rate time-in-zone for that run.",
            "If the assistant only gives averages, ask it to pull the activity detail/streams.",
            "Judge the session against your prescribed threshold paces and say what you'd change.",
        ],
        "expectations": {
            "expected_focus": "per-lap splits + HR-zone distribution and an execution verdict",
        },
    },
    {
        "id": "amb_run_tobias",
        "name": "Tobias",
        "goal": (
            "Decide whether you are recovered enough for tomorrow's key 32 km long run with "
            "marathon-pace blocks, and if not, exactly how to modify it."
        ),
        "identity": (
            "You are Tobias, 37, a project manager deep in a marathon build-up, wearing a "
            "Garmin day and night and watching HRV, sleep and Body Battery closely. You have "
            "had two poor nights and a stressful work week, and you are impatient with "
            "'listen to your body' answers — you want a decision you can act on."
        ),
        "guidelines": [
            "Anchor the question in your objective recovery data: HRV, sleep, Body Battery, stress.",
            "Insist on a clear go / no-go plus a specific adjustment (distance, pace, blocks).",
            "Mention your full workday and limited time window tomorrow.",
        ],
        "expectations": {
            "expected_focus": "recovery readiness + a concrete go/no-go and long-run adjustment",
        },
    },
]

# ── hobby swimmers ────────────────────────────────────────────────────────────
_HOBBY_SWIMMERS: list[dict[str, Any]] = [
    {
        "id": "hob_swim_anika",
        "name": "Anika",
        "goal": (
            "Understand why your front crawl falls apart after two lengths and get a few "
            "trustworthy technique drills for breathing and body position, with the sources "
            "they come from."
        ),
        "identity": (
            "You are Anika, 27, a veterinary assistant who swims twice a week at the local "
            "pool for fitness and calm. You taught yourself front crawl from YouTube, get out "
            "of breath far too quickly, and you are sceptical of influencer swim tips — if "
            "someone tells you to do a drill, you want to know where that advice comes from."
        ),
        "guidelines": [
            "Describe the problem in everyday words: breathless, sinking legs, panicky breathing.",
            "Ask for concrete drills you can do in a 25 m pool, not theory.",
            "Ask where the advice comes from and expect real sources, not opinions.",
        ],
        "expectations": {
            "expected_focus": "evidence-based front-crawl technique drills with citations",
        },
    },
    {
        "id": "hob_swim_jonas",
        "name": "Jonas",
        "goal": (
            "Find the two best evenings this week for a relaxed open-water swim in the lake, "
            "given the weather and your calendar, and get them booked into your calendar."
        ),
        "identity": (
            "You are Jonas, 35, a carpenter who swims in the lake all summer because he finds "
            "pools boring. You care about air and water conditions, wind and thunderstorms, "
            "and daylight — not about pace. Your evenings fill up quickly, so if it is not in "
            "the calendar it does not happen."
        ),
        "guidelines": [
            "Ask it to combine the evening weather forecast with your free evenings.",
            "Care about wind, rain, storms and how late it stays light — safety, not performance.",
            "Ask it to create the calendar entries for the two evenings you settle on.",
        ],
        "expectations": {
            "expected_focus": "weather + calendar windows for lake swims, created as events",
        },
    },
]

# ── ambitious swimmers ────────────────────────────────────────────────────────
_AMBITIOUS_SWIMMERS: list[dict[str, Any]] = [
    {
        "id": "amb_swim_vera",
        "name": "Vera",
        "goal": (
            "Build an evidence-based three-week taper for your 1500 m masters meet — grounded "
            "in the training literature, with sources — and sanity-check it against the swim "
            "volume you have actually logged."
        ),
        "identity": (
            "You are Vera, 39, a physiotherapist and a masters swimmer aiming to break 19:30 "
            "for 1500 m freestyle at the regional championships. You read training science for "
            "fun, distrust coaching folklore, and you want any taper recommendation both "
            "referenced and reconciled with your own logged pool sessions."
        ),
        "guidelines": [
            "Ask for taper structure (volume drop, intensity retention) from the literature, with sources.",
            "Ask it to check that against your actual weekly swim volume and trend in your activities.",
            "Push back on any claim that has no source or ignores your real data.",
        ],
        "expectations": {
            "expected_focus": "referenced taper methodology reconciled with logged swim load",
        },
    },
    {
        "id": "amb_swim_malte",
        "name": "Malte",
        "goal": (
            "Judge whether your body is ready for tomorrow morning's hard open-water threshold "
            "set, and if it is not, what to swap it for in your race week build-up."
        ),
        "identity": (
            "You are Malte, 31, a logistics planner training for a 5 km open-water race and "
            "swimming six times a week, with easy runs as cross-training. You wear a Garmin "
            "constantly and treat sleep, HRV, stress and Body Battery as the deciding inputs "
            "for hard sessions. You want a verdict with numbers behind it, not reassurance."
        ),
        "guidelines": [
            "Anchor the question in HRV, sleep, stress and Body Battery from the last few days.",
            "Ask for a clear yes/no on the hard set plus a named alternative session if it is a no.",
            "Mention your cross-training runs and ask how they fit into a heavy swim week.",
        ],
        "expectations": {
            "expected_focus": "recovery-based go/no-go for a hard swim set and an alternative session",
        },
    },
]

# Persona type → its two concrete personas, in the canonical PERSONA_TYPES order.
_PERSONAS_BY_TYPE: dict[str, list[dict[str, Any]]] = {
    "hobby_cyclist": _HOBBY_CYCLISTS,
    "ambitious_cyclist": _AMBITIOUS_CYCLISTS,
    "hobby_runner": _HOBBY_RUNNERS,
    "ambitious_runner": _AMBITIOUS_RUNNERS,
    "hobby_swimmer": _HOBBY_SWIMMERS,
    "ambitious_swimmer": _AMBITIOUS_SWIMMERS,
}


def all_personas() -> list[dict[str, Any]]:
    """Every persona, tagged with its sport / level / type, in a stable order.

    Order is ``PERSONA_TYPES`` order, two personas per type (12 in total).
    """
    out: list[dict[str, Any]] = []
    for persona_type in PERSONA_TYPES:
        level, sport = persona_type.split("_", 1)
        for p in _PERSONAS_BY_TYPE[persona_type]:
            out.append({**p, "sport": sport, "level": level, "type": persona_type})
    return out


def build_test_cases(
    persona_type: str | None = None,
    sport: str | None = None,
    level: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build ``ConversationSimulator`` test cases plus the matching persona records.

    Args:
        persona_type: one of ``PERSONA_TYPES`` (e.g. ``"ambitious_runner"``) to keep
            only that type, or ``None`` for all.
        sport: one of ``SPORTS`` (``"cyclist"`` / ``"runner"`` / ``"swimmer"``), or ``None``.
        level: one of ``LEVELS`` (``"hobby"`` / ``"ambitious"``), or ``None``.
        limit: keep at most this many personas (after filtering) — handy for smoke runs.

    The filters combine, so ``sport="runner", level="hobby"`` is equivalent to
    ``persona_type="hobby_runner"``.

    Returns:
        ``(test_cases, personas)`` — index-aligned. ``personas`` carries the
        ``id`` / ``name`` / ``sport`` / ``level`` / ``type`` used to group results
        in the report.
    """
    personas = all_personas()
    if persona_type:
        personas = [p for p in personas if p["type"] == persona_type]
    if sport:
        personas = [p for p in personas if p["sport"] == sport]
    if level:
        personas = [p for p in personas if p["level"] == level]
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
