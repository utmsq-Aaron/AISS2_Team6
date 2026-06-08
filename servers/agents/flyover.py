"""FlyoverAgent — detects and coordinates 3D flythrough video requests.

Multi-agent role: flythrough specialist.
  - Receives the user query and the FetchingAgent's results
  - Fast path: if launch_flythrough already ran and returned show_flythrough,
    extract the action directly (no LLM needed)
  - LLM path: if query mentions a flythrough but the action isn't in data yet,
    use LLM to extract parameters and determine if all required fields are present
  - Returns a flyover_action dict or null

Exposed as MCP tool:       handle_flyover(query, data_results)
Callable in-process via:   call_sync(query, data_results)

Standalone usage:
    python servers/agents/flyover.py
"""

import json

from mcp.server.fastmcp import FastMCP

from servers.agents._base import llm_call, truncate, extract_json, FLYTHROUGH_KEYWORDS

mcp = FastMCP(
    "FlyoverAgent",
    instructions="Detects 3D flythrough requests and extracts rendering parameters.",
)

_SYSTEM = """\
ROLE: You are FlyoverAgent — Phase 2b of 4, running in parallel with VisualizationAgent.
You coordinate 3D flythrough video requests. A flythrough is an animated GPS route on a
satellite or dark map. It has NO overlays — no HR, no pace, no elevation, no text.

FAST PATH (always try this first — no LLM reasoning needed):
  If any result in the fetched data contains action="show_flythrough", extract and
  return the flyover_action directly. FetchingAgent already validated all parameters.

LLM PATH (only when the fast path finds nothing and the query mentions flythrough):
  Determine if ALL FOUR required parameters are explicitly present in the query or
  conversation history:

  1. activity_id  — a numeric Strava activity ID from the fetched data results.
                    Never invent or guess an ID.
  2. orientation  — must be stated: "landscape" or "portrait" (or clear synonym).
                    Never infer from device type or context.
  3. mode         — must be stated: "satellite_3d" or "dark" (satellite / map style).
                    Never assume a default.
  4. duration_sec — must be stated: a number of seconds or minutes (30–120 s range).
                    Never guess.

STRICT RULE: If ANY of the four is missing or cannot be confirmed from the query/history,
return {"flyover_action": null} — do NOT infer defaults, do NOT ask questions.
The ChatAgent is responsible for asking the user for whatever is missing.

When ALL FOUR are confirmed, return:
{"flyover_action": {"type":"flythrough","activity_id":<int>,"activity_name":"<str>",
  "orientation":"<str>","mode":"<str>","duration_sec":<int>,"resolution":"2K"}}
"""


# ── MCP tool ──────────────────────────────────────────────────────────────────

@mcp.tool()
def handle_flyover(query: str, data_results: str) -> str:
    """Detect and resolve a 3D flythrough request from the user query + fetched data.

    Args:
        query:        The user's original question.
        data_results: JSON from FetchingAgent.call_sync() containing results list.

    Returns:
        JSON string: {flyover_action: {...} | null}
    """
    return call_sync(query, data_results)


# ── In-process entry point ────────────────────────────────────────────────────

def call_sync(query: str, data_results: str) -> str:
    """Callable directly in-process by the orchestrator."""
    try:
        fetch   = json.loads(data_results)
        results = fetch.get("results") or []
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"flyover_action": None})

    # ── Fast path: launch_flythrough already ran ───────────────────────────────
    # If FetchingAgent called launch_flythrough and got back show_flythrough,
    # extract the action directly — no LLM needed.
    for r in results:
        if r.get("error"):
            continue
        try:
            data = json.loads(r.get("result") or "{}")
            if data.get("action") == "show_flythrough":
                return json.dumps({
                    "flyover_action": {
                        "type":          "flythrough",
                        "activity_id":   data["activity_id"],
                        "activity_name": data.get("activity_name", ""),
                        "orientation":   data.get("orientation", "landscape"),
                        "mode":          data.get("mode", "satellite_3d"),
                        "duration_sec":  int(data.get("duration_sec", 60)),
                        "resolution":    data.get("resolution", "2K"),
                    }
                })
        except (json.JSONDecodeError, TypeError, KeyError):
            continue

    # ── Quick exit: query doesn't mention flythrough at all ───────────────────
    q_lower = query.lower()
    if not any(k in q_lower for k in FLYTHROUGH_KEYWORDS):
        return json.dumps({"flyover_action": None})

    # ── LLM path: query mentions flythrough, try to extract params from data ──
    summaries = [
        f"- {r['tool']}: {truncate(r.get('result_summary',''), 500)}"
        for r in results
        if not r.get("error")
    ]
    user_msg = f"User query: {query}\n\nFetched data:\n" + "\n".join(summaries)
    raw = llm_call(_SYSTEM, user_msg, json_mode=True)

    try:
        result = extract_json(raw)
        return json.dumps(result)
    except Exception:
        return json.dumps({"flyover_action": None})


# ── Standalone MCP server entry point ────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
