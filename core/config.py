"""Declarative registry of MCP server connections.

Each entry is just a name → URL. Own servers and external/user-added servers are
the same shape — the only difference is the URL. Add a server = add one line (or
one env var); no code in the host, agents, or UI changes.

Override any URL via env, e.g. WEATHER_MCP_URL=http://weather-mcp:8101/mcp
(useful in docker-compose, where the service name is the host).
"""

import os

# Separator between server namespace and tool name in the flat, OpenAI-safe tool
# name (dots are NOT allowed in OpenAI function names, so we use a double underscore).
SEP = "__"


def _url(name: str, default_port: int) -> str:
    return os.getenv(f"{name.upper()}_MCP_URL", f"http://127.0.0.1:{default_port}/mcp")


# name → Streamable-HTTP MCP endpoint. Own servers today; external/user servers
# get appended here (per-user, at runtime) in the multi-tenant build.
MCP_SERVERS: dict[str, str] = {
    "weather":    _url("weather",    8101),
    "routes":     _url("routes",     8102),
    "strava":     _url("strava",     8103),
    "garmin":     _url("garmin",     8104),
    "calendar":   _url("calendar",   8105),
    "telegram":   _url("telegram",   8106),
    "flythrough": _url("flythrough", 8107),
    "google_maps": _url("google_maps", 8108),
}


# ── A2A agent layer ───────────────────────────────────────────────────────────
# Each agent is its own A2A server (LangGraph inside). The orchestrator (:9000)
# is an A2A client to the four specialists (:9001–:9004). Same declarative shape
# as MCP_SERVERS: name → base URL, env-overridable (e.g. RECOVERY_A2A_URL=…).

def _a2a_url(name: str, default_port: int) -> str:
    return os.getenv(f"{name.upper()}_A2A_URL", f"http://127.0.0.1:{default_port}/")


AGENT_PORTS: dict[str, int] = {
    "orchestrator": 9000,
    "recovery":     9001,
    "load":         9002,
    "context":      9003,
    "route":        9004,
    "fitness":      9005,
}

A2A_AGENTS: dict[str, str] = {name: _a2a_url(name, port) for name, port in AGENT_PORTS.items()}

# Which MCP servers each specialist may reach. The agent discovers tools from
# only these servers (scoped ToolHost) — "tools discovered, never hardcoded",
# just narrowed per agent. The orchestrator has no MCP scope; it talks to agents.
AGENT_MCP_SCOPE: dict[str, list[str]] = {
    "recovery": ["garmin"],
    "load":     ["strava", "garmin"],
    "context":  ["weather", "calendar"],
    "route":    ["routes"],
    # fitness has NO MCP scope — it answers from a RAG vector DB of fitness
    # literature (core.fitness_rag), not from a live MCP server.
    "fitness":  [],
}

# Which specialists the orchestrator may delegate to (one A2A ask_* tool each).
# Override with ORCHESTRATOR_SPECIALISTS=recovery,load (e.g. to run a subset).
# Unreachable specialists degrade gracefully — the orchestrator reports them as
# unavailable rather than failing the whole turn.
ORCHESTRATOR_SPECIALISTS: list[str] = [
    s.strip() for s in os.getenv("ORCHESTRATOR_SPECIALISTS", "recovery,load,context,route,fitness").split(",") if s.strip()
]
