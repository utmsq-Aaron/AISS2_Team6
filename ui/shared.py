"""
Shared utilities for the HealthBot UI:
  - Async bridge (run_async)
  - Cached MCP server instances
  - Cached OpenAI client
  - Unified tool dispatcher (call_tool)
  - OpenAI tool-spec builder
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ── Async bridge ──────────────────────────────────────────────────────────────

def run_async(coro) -> Any:
    """Run an async coroutine from synchronous Streamlit/thread code.

    Creates a fresh event loop per call (required when called from
    ThreadPoolExecutor workers — each thread must own its loop).
    Cancels any lingering tasks before closing to avoid ResourceWarning.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.close()


# ── MCP server singletons ─────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def get_strava_mcp():
    from servers.strava import SimpleMCPServer
    return SimpleMCPServer()

@st.cache_resource(show_spinner=False)
def get_garmin_mcp():
    try:
        from servers.garmin import GarminMCPServer
        return GarminMCPServer()
    except Exception:
        return None

@st.cache_resource(show_spinner=False)
def get_routes_mcp():
    try:
        from servers.routes import RoutesMCPServer
        return RoutesMCPServer()
    except Exception:
        return None


# ── Config validation ─────────────────────────────────────────────────────────

def validate_config() -> list[str]:
    """Return a list of missing/invalid configuration items.

    Called at app startup. Returns empty list when everything is properly configured.
    Does NOT raise — callers decide how to surface warnings.
    """
    issues = []
    if not os.getenv("CLIENT_ID"):
        issues.append("CLIENT_ID not set — Strava data unavailable")
    if not os.getenv("CLIENT_SECRET"):
        issues.append("CLIENT_SECRET not set — Strava data unavailable")
    if not os.getenv("OPENAI_API_KEY"):
        issues.append("OPENAI_API_KEY not set — AI features unavailable")
    if not os.getenv("AGENT_MODEL"):
        issues.append("AGENT_MODEL not set — using default gpt-4o (may be wrong for your provider)")
    return issues


# ── Connection checks ─────────────────────────────────────────────────────────

def strava_connected() -> bool:
    """True only if the Strava token file exists and contains an access_token."""
    token_path = Path(".tokens/strava.json")
    if not token_path.is_file():
        return False
    try:
        import json as _json
        data = _json.loads(token_path.read_text())
        return bool(data.get("access_token"))
    except Exception:
        return False

def garmin_connected() -> bool:
    """True when Garmin mock mode is active, or real token files exist."""
    if os.getenv("GARMIN_MOCK_HEALTH", "").lower() in ("1", "true"):
        return True
    token_dir = Path(".tokens")
    if not token_dir.is_dir():
        return False
    # garminconnect stores tokens as files inside .tokens/
    return any(
        f.is_file() and f.suffix in (".json", ".txt", "") and f.name != "strava.json"
        for f in token_dir.iterdir()
    )

def routes_connected() -> bool:
    return bool(os.getenv("ORS_API_KEY", ""))

def is_locked() -> bool:
    """True when DO_LOCK=true in .env — blocks all UI access."""
    return os.getenv("DO_LOCK", "").lower() in ("1", "true")


# ── Tool dispatcher ───────────────────────────────────────────────────────────

_ROUTES_TOOLS = {"plan_route", "plan_circular_route", "get_elevation_profile", "explore_trails", "get_isochrone"}

def call_tool(name: str, args: dict) -> str:
    """Route a tool call to the correct MCP server and return its JSON result."""
    if name.startswith("get_garmin_"):
        garmin = get_garmin_mcp()
        if garmin is None:
            return json.dumps({"error": "Garmin not connected. See the Setup tab for instructions."})
        return run_async(garmin._dispatch(name, args))
    if name in _ROUTES_TOOLS:
        routes = get_routes_mcp()
        if routes is None:
            return json.dumps({"error": "Routes server unavailable. Check ORS_API_KEY in .env"})
        return run_async(routes._dispatch(name, args))
    return run_async(get_strava_mcp()._dispatch(name, args))


# ── OpenAI client ─────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def get_openai_client() -> OpenAI:
    return OpenAI(
        api_key  = os.getenv("OPENAI_API_KEY") or "",
        base_url = os.getenv("OPENAI_BASE_URL") or None,
    )

MODEL: str = os.getenv("AGENT_MODEL") or "gpt-4o"


# ── OpenAI tool-spec builder ──────────────────────────────────────────────────

def _to_openai_tool(mcp_tool: Dict) -> Dict:
    return {
        "type": "function",
        "function": {
            "name":        mcp_tool["name"],
            "description": mcp_tool.get("description", ""),
            "parameters":  mcp_tool.get("inputSchema", {"type": "object", "properties": {}, "required": []}),
        },
    }

def get_all_openai_tools() -> List[Dict]:
    """Return combined Strava + Garmin + Routes tool specs in OpenAI function-calling format."""
    tools = [_to_openai_tool(t) for t in get_strava_mcp().tools]
    garmin = get_garmin_mcp()
    if garmin:
        tools += [_to_openai_tool(t) for t in garmin.tools]
    routes = get_routes_mcp()
    if routes:
        tools += [_to_openai_tool(t) for t in routes.tools]
    return tools
