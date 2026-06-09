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
    "weather":  _url("weather",  8101),
    "routes":   _url("routes",   8102),
    "strava":   _url("strava",   8103),
    "garmin":   _url("garmin",   8104),
    "calendar": _url("calendar", 8105),
    "telegram": _url("telegram", 8106),
}
