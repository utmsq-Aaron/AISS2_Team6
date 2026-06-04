"""ChatAgent — synthesizes the final natural-language response.

Multi-agent role: sports data analyst + communicator.
  - Receives the user query, all fetched data, viz context, and flyover context
  - Uses an LLM to write the final answer, knowing that charts will render automatically
  - Keeps numerical descriptions concise (charts show the detail)

Exposed as MCP tool:       synthesize(query, data_results, viz_context, flyover_context, ...)
Callable in-process via:   call_sync(...)

Standalone usage:
    python servers/agents/chat.py
"""

import json

from mcp.server.fastmcp import FastMCP

from servers.agents._base import truncate

# Large array keys that are useless as raw data in the chat context window.
# Replace them with a count string; the visualization layer shows the actual data.
_LARGE_ARRAY_KEYS = {"points", "timeline", "buckets_15min"}


def _compact_for_chat(result_text: str) -> str:
    """Strip large GPS/timeline arrays from a JSON result string.

    Replaces any array under a key in _LARGE_ARRAY_KEYS that has more than 20
    elements with a compact count string, saving thousands of context tokens.
    """
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return result_text

    changed = False
    for key in _LARGE_ARRAY_KEYS:
        if key in data and isinstance(data[key], list) and len(data[key]) > 20:
            data[key] = f"[{len(data[key])} data points — rendered as chart]"
            changed = True

    return json.dumps(data, indent=2) if changed else result_text

mcp = FastMCP(
    "ChatAgent",
    instructions="Synthesizes final natural-language fitness analytics responses.",
)

_SYSTEM = """\
ROLE: You are ChatAgent — Phase 4 of 4 in the HealthBot analytics pipeline.
You receive: the user's query, data fetched by FetchingAgent, and context from
VisualizationAgent (which charts will render) and FlyoverAgent (whether a
flythrough video was triggered). Write the final natural-language answer.

PIPELINE CONTEXT — what is already handled for you:
  • Charts render AUTOMATICALLY below your message. Do not recreate raw data tables.
    Reference them naturally: "the chart below", "you can see the trend below."
  • If AUTO-RENDERING lines appear at the top of the data context:
    - "AUTO-RENDERING: N chart(s)" → those charts appear below. Mention briefly.
    - "AUTO-RENDERING: Flythrough…" → video renders below. See FLYTHROUGH rules.
  • You must NOT describe what is in the charts or video in detail — let them speak.

DATA HONESTY:
  • Ground every claim in the retrieved data. Never fabricate figures.
  • null / None = no data recorded. Never treat as zero.
  • Tool error → say so in one plain sentence.
  • Partial data → answer what you can, name what's missing in one sentence.
  • ALL sources failed → one sentence, then suggest checking Strava/Garmin connection.

TONE AND STYLE:
  • Sports analyst, not bureaucrat. Lead with the insight, not the method.
  • Concise. Include units and clear rounding. Answer in the user's language.
  • No motivational filler unless explicitly asked.
  • Open with a brief data context line: "Looking at your last 14 days of sleep…"
  • Pace: always format as M:SS /km.
  • Temporal patterns: call out specific dates, days, or streaks when meaningful.
  • If you have related data the user didn't ask for but might find useful:
    mention it briefly at the end in one sentence.

CLARIFICATION:
  • When data is insufficient or query is still unclear AFTER seeing results:
    ask ONE short specific question. Do NOT answer AND ask — pick one.
  • Never ask about something already established in conversation history.
  • If a CLARIFICATION HINT is provided: rephrase it naturally as that one question.

FLYTHROUGH STATE MACHINE — pick exactly one state per flythrough turn:

  A) RENDERING — AUTO-RENDERING flythrough line is present in the context:
     Write one sentence only: "[Activity Name] ([date], [dist] km) — \
flythrough is rendering, video appears below."
     Do not add anything else about the flythrough.

  B) ACTIVITY IDENTIFIED, PARAMS MISSING — fetched data contains an activity
     but orientation/mode/duration were not all confirmed yet:
     • Name the activity with its date so the user can confirm it's correct.
     • Ask for ALL missing params in ONE casual sentence.
     • Example (found by criteria): "Your fastest run is the 10 km Stadtpark Run \
(4:12 /km, 2026-04-15) — portrait or landscape, Satellite 3D or Dark Flat, \
and how long (30–120 s)?"
     • Example (found by name): "Found Karlsruhe Laufen (9 km, 2026-05-03) — \
is that the right run? If yes: portrait or landscape, Satellite 3D or Dark Flat, \
and duration (30–120 s)?"

  C) ACTIVITY AMBIGUOUS — user asked for flythrough but didn't name an activity:
     List 2–3 recent activities with key stats; offer "most recent" as a shortcut.
     Example: "Which run? Recent ones: Morning Run (8 km yesterday), Bergen Wandern \
(18.75 km last week). Or just say 'most recent'."

FLYTHROUGH NEVER:
  • Never mention numeric activity IDs, resolution, codec, bitrate, or export format.
  • Never suggest features the video doesn't have (HR zones, pace overlay, elevation
    graph, split markers, text overlays). If asked: "The flythrough shows only the
    animated route — data overlays aren't supported."
  • Never say "I cannot determine the activity" — always offer alternatives.
  • Never start rendering (state A language) without first confirming the activity when
    it was found by search criteria rather than exact name from the user.
  • Never re-ask for a parameter the user has already provided.

Today is {today}.
"""


# ── MCP tool ──────────────────────────────────────────────────────────────────

@mcp.tool()
def synthesize(
    query: str,
    data_results: str,
    viz_context: str = "{}",
    flyover_context: str = "{}",
    history: str = "[]",
    today: str = "",
    clarification_question: str = "",
) -> str:
    """Generate the final natural-language answer.

    Args:
        query:                   The user's original question.
        data_results:            JSON from FetchingAgent (results list).
        viz_context:             JSON from VisualizationAgent ({viz_actions: [...]}).
        flyover_context:         JSON from FlyoverAgent ({flyover_action: ... | null}).
        history:                 JSON-encoded conversation history.
        today:                   Today's date YYYY-MM-DD.
        clarification_question:  Hint from FetchingAgent when it couldn't confidently plan.

    Returns:
        The final markdown-formatted answer string.
    """
    try:
        hist = json.loads(history) if history else []
    except (json.JSONDecodeError, TypeError):
        hist = []
    return call_sync(query, data_results, viz_context, flyover_context, hist, today,
                     clarification_question)


# ── In-process entry point ────────────────────────────────────────────────────

def call_sync(
    query: str,
    data_results: str,
    viz_context: str,
    flyover_context: str,
    history: list = None,
    today: str = "",
    clarification_question: str = "",
) -> str:
    """Callable directly in-process by the orchestrator."""
    from datetime import datetime
    from servers.agents._base import get_llm_client

    today = today or datetime.now().strftime("%Y-%m-%d")

    # ── 1. Rendering context (always at top — ChatAgent needs this upfront) ──────
    # AUTO-RENDERING lines tell ChatAgent what will appear below its answer so it
    # can frame its response correctly without re-describing what the visuals show.
    render_parts: list = []
    try:
        viz = json.loads(viz_context)
        viz_actions = viz.get("viz_actions") or []
        if viz_actions:
            labels = [va.get("label", va.get("tool", "?")) for va in viz_actions]
            render_parts.append(
                f"AUTO-RENDERING: {len(viz_actions)} chart(s) below — {', '.join(labels)}"
            )
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        fly = json.loads(flyover_context)
        fa  = fly.get("flyover_action")
        if fa:
            render_parts.append(
                f"AUTO-RENDERING: Flythrough for '{fa.get('activity_name','')}' "
                f"(activity_id={fa.get('activity_id')}, "
                f"{fa.get('orientation','landscape')}, {fa.get('mode','satellite_3d')}, "
                f"{fa.get('duration_sec',60)}s) — video renders below when ready."
            )
    except (json.JSONDecodeError, TypeError):
        pass

    # ── 2. Fetch metadata (data quality and plan context) ─────────────────────
    try:
        fetch        = json.loads(data_results)
        results      = fetch.get("results") or []
        reasoning    = fetch.get("reasoning", "")
        data_summary = fetch.get("data_summary", "")
        key_findings = fetch.get("key_findings") or []
    except (json.JSONDecodeError, TypeError):
        results      = []
        reasoning    = ""
        data_summary = ""
        key_findings = []

    ok_results  = [r for r in results if not r.get("error")]
    err_results = [r for r in results if r.get("error")]

    meta_lines: list = []
    if reasoning:
        meta_lines.append(f"FETCH PLAN: {reasoning}")
    if data_summary:
        meta_lines.append(f"DATA SUMMARY: {data_summary}")
    elif ok_results:
        meta_lines.append(
            f"RETRIEVED ({len(ok_results)} source(s)): "
            + ", ".join(r["label"] for r in ok_results)
        )
    if err_results:
        meta_lines.append(
            "ERRORS — these sources failed: "
            + "; ".join(f"{r['label']}: {r.get('error','?')}" for r in err_results)
        )
    if key_findings:
        meta_lines.append("KEY FINDINGS: " + " | ".join(key_findings))
    if clarification_question:
        meta_lines.append(f"CLARIFICATION HINT: {clarification_question}")

    # ── 3. Raw data sections ──────────────────────────────────────────────────
    # Large GPS/timeline arrays are stripped (they're shown as charts, not text).
    data_sections: list = []
    for r in ok_results:
        header = f"### {r['label']}  [{r['tool']}]"
        body = truncate(_compact_for_chat(r.get("result", "")), 4000)
        data_sections.append(f"{header}\n{body}")

    # ── 4. Assemble: rendering context first, then meta, then raw data ─────────
    block_parts: list = []
    if render_parts:
        block_parts.append("\n".join(render_parts))
    if meta_lines:
        block_parts.append("\n".join(meta_lines))
    if data_sections:
        block_parts.append("\n\n".join(data_sections))
    else:
        # No data retrieved — include tool list so ChatAgent can explain capabilities
        try:
            from ui.shared import get_all_openai_tools
            tools = get_all_openai_tools()
            tool_list = "\n".join(
                f'- {t["function"]["name"]}: {t["function"]["description"]}'
                for t in tools
            )
            block_parts.append(f"(no data retrieved)\n\nAvailable tools:\n{tool_list}")
        except Exception:
            block_parts.append("(no data retrieved)")

    data_block = "\n\n".join(block_parts)

    # ── Call LLM ──────────────────────────────────────────────────────────────
    client, model = get_llm_client()

    messages = [{"role": "system", "content": _SYSTEM.format(today=today)}]
    for msg in (history or [])[-10:]:
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({
        "role": "user",
        "content": f"{query}\n\n---\nData retrieved:\n\n{data_block}",
    })

    resp = client.chat.completions.create(model=model, messages=messages, temperature=0.3)
    return resp.choices[0].message.content or ""


# ── Standalone MCP server entry point ────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
