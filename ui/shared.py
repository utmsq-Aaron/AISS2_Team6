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
    """Run an async coroutine from synchronous Streamlit code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── MCP server singletons ─────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def get_strava_mcp():
    from mcp.strava import SimpleMCPServer
    return SimpleMCPServer()

@st.cache_resource(show_spinner=False)
def get_garmin_mcp():
    try:
        from mcp.garmin import GarminMCPServer
        return GarminMCPServer()
    except Exception:
        return None


# ── Connection checks ─────────────────────────────────────────────────────────

def strava_connected() -> bool:
    return Path(".tokens/strava.json").exists()

def garmin_connected() -> bool:
    return Path(".tokens/garmin_tokens.json").exists()


# ── Tool dispatcher ───────────────────────────────────────────────────────────

def call_tool(name: str, args: dict) -> str:
    """Route a tool call to the correct MCP server and return its JSON result."""
    if name.startswith("get_garmin_"):
        garmin = get_garmin_mcp()
        if garmin is None:
            return json.dumps({"error": "Garmin not connected. Run: python auth/garmin_setup.py"})
        return run_async(garmin._dispatch(name, args))
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
    """Return combined Strava + Garmin tool specs in OpenAI function-calling format."""
    tools = [_to_openai_tool(t) for t in get_strava_mcp().tools]
    garmin = get_garmin_mcp()
    if garmin:
        tools += [_to_openai_tool(t) for t in garmin.tools]
    return tools
