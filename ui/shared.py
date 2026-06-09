"""Shared utilities for the FitDash UI.

  - run_async   : sync bridge for coroutines (ThreadPoolExecutor-safe)
  - get_host    : cached ToolHost singleton (single MCP client for all servers)
  - call_tool   : convenience wrapper — ``call_tool("server__tool", {args})``
  - Connection checks: strava_connected, garmin_connected, routes_connected
  - validate_config  : startup warnings
  - get_openai_client: cached OpenAI client for direct LLM calls in the UI
"""

import json
import os
from pathlib import Path
from typing import Any, List

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


# ── Async bridge ──────────────────────────────────────────────────────────────

def run_async(coro) -> Any:
    """Run an async coroutine from synchronous Streamlit / thread code.

    Creates a fresh event loop per call — required when called from
    ThreadPoolExecutor workers (each thread must own its loop).
    """
    import asyncio
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


# ── ToolHost singleton ────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def get_host():
    """Return the process-wide ToolHost (MCP client for all servers)."""
    from core.host import ToolHost
    return ToolHost()


def call_tool(name: str, args: dict) -> str:
    """Call a tool by its namespaced name ``server__tool_name``.

    Routes through ToolHost; unreachable servers return a JSON error string.
    """
    return get_host().call_tool(name, args)


# ── Connection checks ─────────────────────────────────────────────────────────

def strava_connected() -> bool:
    """True only if the Strava token file exists and contains an access_token."""
    token_path = Path(".tokens/strava.json")
    if not token_path.is_file():
        return False
    try:
        data = json.loads(token_path.read_text())
        return bool(data.get("access_token"))
    except Exception:
        return False


def garmin_connected() -> bool:
    """True only if the Garmin token directory contains at least one token file."""
    token_dir = Path(".tokens")
    if not token_dir.is_dir():
        return False
    return any(
        f.is_file() and f.suffix in (".json", ".txt", "") and f.name != "strava.json"
        for f in token_dir.iterdir()
    )


def routes_connected() -> bool:
    return bool(os.getenv("ORS_API_KEY", ""))


def telegram_connected() -> bool:
    """True when Telegram API credentials AND a session string are configured.

    Like the other sidebar dots this reflects *configuration*, not a live ping:
    the telegram proxy (servers/telegram_mcp.py) reads these env vars on start.
    Reads `.env` fresh (merged with the process env) so it reflects edits made
    after the app started — e.g. via the Settings tab.
    """
    from dotenv import dotenv_values
    file_vals = dotenv_values(".env")

    def _real(v: str) -> bool:
        return bool(v) and not v.startswith("your_")

    def _get(k: str) -> str:
        return os.getenv(k) or file_vals.get(k) or ""

    return all(_real(_get(k)) for k in
               ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_STRING"))


# ── Config validation ─────────────────────────────────────────────────────────

def validate_config() -> List[str]:
    """Return human-readable warnings for missing or incomplete configuration."""
    issues = []
    if not strava_connected():
        issues.append("Strava nicht verbunden — öffne den Settings-Tab um dich zu verbinden")
    if not garmin_connected():
        issues.append("Garmin nicht verbunden — führe python auth/garmin_setup.py aus")
    if not os.getenv("OPENAI_API_KEY"):
        issues.append("OPENAI_API_KEY nicht gesetzt — KI-Features deaktiviert")
    return issues


# ── OpenAI client (for direct LLM calls in UI components) ────────────────────

@st.cache_resource(show_spinner=False)
def get_openai_client() -> OpenAI:
    return OpenAI(
        api_key  = os.getenv("OPENAI_API_KEY") or "",
        base_url = os.getenv("OPENAI_BASE_URL") or None,
    )


MODEL: str = os.getenv("AGENT_MODEL") or "gpt-4o"
