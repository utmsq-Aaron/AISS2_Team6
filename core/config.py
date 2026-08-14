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

# name → default port. THE single numeric source for every MCP server's port —
# scripts/export_ports.py projects this (plus AGENT_PORTS / FASTAPI_PORT below)
# into whatever ports.sh, docker-compose.yml and web/vite.config.ts need, so none
# of them carry a second, independently-typo-able copy of these numbers. A given
# server's own `os.getenv("<NAME>_MCP_PORT", …)` default (in servers/*.py) is a
# deliberate exception — those files are intentionally standalone/no core import.
MCP_PORTS: dict[str, int] = {
    "weather":     8101,
    "routes":      8102,
    "strava":      8103,
    "garmin":      8104,
    "calendar":    8105,
    "telegram":    8106,
    "flythrough":  8107,
    "google_maps": 8108,
    "athlete":     8109,
}


def _url(name: str) -> str:
    return os.getenv(f"{name.upper()}_MCP_URL", f"http://127.0.0.1:{MCP_PORTS[name]}/mcp")


# name → Streamable-HTTP MCP endpoint. Own servers today; external/user servers
# get appended here (per-user, at runtime) in the multi-tenant build.
MCP_SERVERS: dict[str, str] = {name: _url(name) for name in MCP_PORTS}

# The FastAPI seam's port (api/main.py). Read live by web/vite.config.ts (via
# scripts/export_ports.py) so its dev-server proxy target can never drift from
# what the launcher scripts actually start FastAPI on.
FASTAPI_PORT: int = int(os.getenv("FASTAPI_PORT", "8000"))

# The Vite dev server's port. Here rather than only in vite.config.ts so `./run.sh
# stop` can actually free it: it kills what the registry lists, and while this port
# lived solely in vite.config.ts, stop left the dev server running. Vite then took
# the next free port on the following start, quietly accumulating one stale dev
# server per launch — each serving a stale bundle behind a dead /api proxy.
VITE_PORT: int = int(os.getenv("VITE_PORT", "5173"))


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
    "coach":        9006,
}

A2A_AGENTS: dict[str, str] = {name: _a2a_url(name, port) for name, port in AGENT_PORTS.items()}

# Which MCP servers each specialist may reach. The agent discovers tools from
# only these servers (scoped ToolHost) — "tools discovered, never hardcoded",
# just narrowed per agent. The orchestrator has no MCP scope; it talks to agents.
AGENT_MCP_SCOPE: dict[str, list[str]] = {
    "recovery": ["garmin"],
    # load also owns the flythrough server: prepare_flythrough needs a Strava
    # activity_id (from strava tools), so the chat-triggered 3D flythrough lives
    # with the specialist that can obtain that id in the same turn.
    "load":     ["strava", "garmin", "flythrough"],
    "context":  ["weather", "calendar"],
    "route":    ["routes", "google_maps"],
    # fitness has NO MCP scope — it answers from a RAG vector DB of fitness
    # literature (core.fitness_rag), not from a live MCP server.
    "fitness":  [],
    # coach: structured athlete state + deterministic training math (athlete),
    # plus strava/garmin to FETCH the real numbers those computations need.
    # It additionally gets the fitness-RAG search tool locally (see coach_agent).
    "coach":    ["athlete", "strava", "garmin"],
}

# Which specialists the orchestrator may delegate to (one A2A ask_* tool each).
# Override with ORCHESTRATOR_SPECIALISTS=recovery,load (e.g. to run a subset).
# Unreachable specialists degrade gracefully — the orchestrator reports them as
# unavailable rather than failing the whole turn.
ORCHESTRATOR_SPECIALISTS: list[str] = [
    s.strip() for s in os.getenv("ORCHESTRATOR_SPECIALISTS", "recovery,load,context,route,fitness,coach").split(",") if s.strip()
]
