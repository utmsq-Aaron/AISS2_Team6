"""
Shared utilities for the FitDash UI:
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
    """Return warnings for missing config. Driven by the server registry."""
    from servers.registry import config_status
    issues = []
    for entry in config_status():
        if not entry["available"]:
            missing = ", ".join(entry["missing_env"])
            issues.append(f"{entry['key'].capitalize()} nicht verfügbar — fehlende Env-Vars: {missing}")
    if not os.getenv("OPENAI_API_KEY"):
        issues.append("OPENAI_API_KEY nicht gesetzt — KI-Features deaktiviert")
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
    """True only if Garmin token directory contains at least one token file."""
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


# ── Tool dispatcher ───────────────────────────────────────────────────────────

def call_tool(name: str, args: dict) -> str:
    """Route a tool call to the correct MCP server via the registry."""
    from servers.registry import dispatch
    return run_async(dispatch(name, args))


# ── OpenAI client ─────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def get_openai_client() -> OpenAI:
    return OpenAI(
        api_key  = os.getenv("OPENAI_API_KEY") or "",
        base_url = os.getenv("OPENAI_BASE_URL") or None,
    )

MODEL: str = os.getenv("AGENT_MODEL") or "gpt-4o"


# ── OpenAI tool-spec builder ──────────────────────────────────────────────────

def get_all_openai_tools() -> List[Dict]:
    """Return all tool specs from every registered & available MCP server."""
    from servers.registry import all_openai_tools
    return all_openai_tools()
