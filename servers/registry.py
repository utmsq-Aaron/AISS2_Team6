"""
Server Registry — single place to register all MCP servers.

HOW TO ADD A NEW SERVER:
  1. Build servers/myserver.py  (class MyServer(BaseMCPServer))
  2. Add one line here:  register("my_server", MyServer, required_env=["MY_API_KEY"])
  3. Done — agent and UI discover it automatically.

No changes needed in shared.py, orchestrator.py, or any agent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Type

from servers._base_server import BaseMCPServer


@dataclass
class ServerEntry:
    key: str                          # short identifier, e.g. "strava"
    cls: Type[BaseMCPServer]          # the server class
    required_env: List[str]           # env vars that must be set
    description: str = ""
    _instance: Optional[BaseMCPServer] = field(default=None, repr=False)

    def is_available(self) -> bool:
        return all(os.getenv(k) for k in self.required_env)

    def get_instance(self) -> Optional[BaseMCPServer]:
        if not self.is_available():
            return None
        if self._instance is None:
            try:
                self._instance = self.cls()
            except Exception:
                return None
        return self._instance

    def missing_env(self) -> List[str]:
        return [k for k in self.required_env if not os.getenv(k)]


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: List[ServerEntry] = []


def register(
    key: str,
    cls: Type[BaseMCPServer],
    required_env: List[str] = [],
    description: str = "",
) -> None:
    _REGISTRY.append(ServerEntry(
        key=key, cls=cls, required_env=required_env, description=description
    ))


def get_server(key: str) -> Optional[BaseMCPServer]:
    for entry in _REGISTRY:
        if entry.key == key:
            return entry.get_instance()
    return None


def all_servers() -> List[BaseMCPServer]:
    """Return instances of all available (configured) servers."""
    return [s for e in _REGISTRY if (s := e.get_instance()) is not None]


def _openai_tools_from(server) -> list:
    """Works with both BaseMCPServer subclasses and legacy server classes."""
    if hasattr(server, "to_openai_tools"):
        return server.to_openai_tools()
    # Legacy format: .tools list with "name"/"inputSchema" keys
    return [
        {
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t.get("description", ""),
                "parameters":  t.get("inputSchema", {"type": "object", "properties": {}, "required": []}),
            },
        }
        for t in (server.tools or [])
    ]


def all_openai_tools() -> list:
    """Return combined OpenAI tool specs from every available server."""
    tools = []
    for server in all_servers():
        tools.extend(_openai_tools_from(server))
    return tools


def all_tool_names() -> set:
    names = set()
    for server in all_servers():
        names |= {t["name"] for t in (server.tools or [])}
    return names


def config_status() -> List[dict]:
    """Used by validate_config() to report missing credentials."""
    status = []
    for entry in _REGISTRY:
        status.append({
            "key":         entry.key,
            "description": entry.description,
            "available":   entry.is_available(),
            "missing_env": entry.missing_env(),
        })
    return status


async def dispatch(tool_name: str, args: dict) -> str:
    """Route any tool call to the correct server. Supports both legacy and BaseMCPServer."""
    import json
    for server in all_servers():
        tool_names = {t["name"] for t in (server.tools or [])}
        if tool_name in tool_names:
            return await server._dispatch(tool_name, args)
    return json.dumps({"error": f"No server handles tool '{tool_name}'"})


# ── Register all servers here ─────────────────────────────────────────────────

def _setup() -> None:
    from servers.strava import SimpleMCPServer
    register("strava",   SimpleMCPServer,
             required_env=["CLIENT_ID", "CLIENT_SECRET"],
             description="Strava activities, athlete stats, GPS streams")

    try:
        from servers.garmin import GarminMCPServer
        register("garmin", GarminMCPServer,
                 required_env=["GARMIN_EMAIL"],
                 description="Garmin health: sleep, HRV, Body Battery, steps")
    except ImportError:
        pass

    from servers.routes import RoutesMCPServer
    register("routes",   RoutesMCPServer,
             required_env=["ORS_API_KEY"],
             description="Route planning and trail discovery via OpenRouteService")

    # ── Add new servers below this line ──────────────────────────────────────
    # from servers.calendar import CalendarMCPServer
    # register("calendar", CalendarMCPServer,
    #          required_env=["GOOGLE_CALENDAR_CREDENTIALS"],
    #          description="Google Calendar: events, free slots, scheduling")

    # from servers.nutrition import NutritionMCPServer
    # register("nutrition", NutritionMCPServer,
    #          required_env=["CRONOMETER_API_KEY"],
    #          description="Food logging and nutrition tracking")

    from servers.weather import WeatherMCPServer
    register("weather", WeatherMCPServer,
             required_env=[],
             description="Weather, pollen, and UV index via Open-Meteo (no API key required)")


_setup()
