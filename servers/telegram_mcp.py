"""Telegram — Streamable-HTTP proxy for the upstream stdio telegram-mcp.

Unlike the other ``servers/*_mcp.py`` files, this is NOT a native FastMCP server: the
Telegram tools come from the external project https://github.com/chigwell/telegram-mcp,
which is **stdio-only** and pins a newer Python + Telethon. Rather than fork its ~80
tools, we run it UNMODIFIED in its own ``uv``-managed environment and bridge it onto
this app's Streamable-HTTP bus. ``core.host.ToolHost`` then reaches it exactly like any
other server — one URL in ``core.config.MCP_SERVERS``, tools *discovered* (never
hardcoded), and the Telegram API credentials declared as connection env, kept separate
from the tool definitions and out of model context.

Topology::

    ToolHost ──HTTP──▶ this proxy ──stdio──▶ `uv run main.py` (telegram-mcp)
                       (stateless)            (persistent: one Telegram login +
                                               one cache warm, reused per call)

The HTTP front is stateless (a fresh connection per call, matching ToolHost), while the
stdio back-end is a single long-lived session — so logging in to Telegram and warming
the dialog cache happens once for the process, not per request.

Run locally:   python -m servers.telegram_mcp
Endpoint:      http://127.0.0.1:8106/mcp   (override host/port via env)

Requires (in the app's ``.env``; passed through to the upstream subprocess):
    TELEGRAM_API_ID, TELEGRAM_API_HASH   — from https://my.telegram.org/apps
    TELEGRAM_SESSION_STRING              — generate once (interactive login is disabled
        over stdio): ``uv run --directory external/telegram-mcp session_string_generator.py``
Optional:
    TELEGRAM_MCP_DIR        path to the cloned upstream repo (default ./external/telegram-mcp)
    TELEGRAM_EXPOSED_TOOLS  set to "read-only" to expose only read tools
    TELEGRAM_MCP_HOST / TELEGRAM_MCP_PORT   bind address (default 127.0.0.1:8106)
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

import mcp.types as types
import uvicorn
from dotenv import load_dotenv
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount

load_dotenv()

HOST = os.getenv("TELEGRAM_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("TELEGRAM_MCP_PORT", "8106"))

# Where the upstream repo is checked out. It is run unmodified via `uv`, which
# provisions its pinned Python + Telethon in an isolated env (no impact on app deps).
_REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_DIR = Path(
    os.getenv("TELEGRAM_MCP_DIR", str(_REPO_ROOT / "external" / "telegram-mcp"))
).expanduser()

# Credentials are connection auth — handed to the subprocess environment, never exposed
# as tool arguments or fed into model context (same principle as the other servers).
_REQUIRED_CREDS = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH")
_SESSION_KEYS = ("TELEGRAM_SESSION_STRING", "TELEGRAM_SESSION_NAME")


def _check_prereqs() -> None:
    """Fail fast with an actionable message; a crashed proxy is simply skipped by ToolHost."""
    if not UPSTREAM_DIR.joinpath("main.py").is_file():
        sys.exit(
            f"[telegram] upstream not found at {UPSTREAM_DIR}\n"
            f"  git clone https://github.com/chigwell/telegram-mcp \"{UPSTREAM_DIR}\"\n"
            f"  (or point TELEGRAM_MCP_DIR at an existing checkout)."
        )
    if shutil.which("uv") is None:
        sys.exit("[telegram] `uv` not found on PATH — required to run the upstream server (https://docs.astral.sh/uv/).")
    missing = [k for k in _REQUIRED_CREDS if not os.getenv(k)]
    if missing:
        sys.exit(f"[telegram] missing required env: {', '.join(missing)} (see .env.example).")
    if not any(os.getenv(k) for k in _SESSION_KEYS):
        sys.exit(
            "[telegram] no Telegram session configured. Set TELEGRAM_SESSION_STRING in .env "
            f"(generate it with: uv run --directory \"{UPSTREAM_DIR}\" session_string_generator.py)."
        )


def _upstream_params() -> StdioServerParameters:
    # Run the upstream entrypoint via uv (isolated env, pinned Python). The full parent
    # environment is forwarded so PATH/HOME and every TELEGRAM_* var reach the child.
    return StdioServerParameters(
        command=shutil.which("uv") or "uv",
        args=["run", "--directory", str(UPSTREAM_DIR), "main.py"],
        env={**os.environ},
    )


# ── Proxy: a low-level MCP server whose tools are the upstream's, discovered live ──

_state: dict = {"session": None, "tools": []}
server = Server("telegram")


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return _state["tools"]


@server.call_tool()
async def _call_tool(name: str, arguments: dict | None):
    session: ClientSession | None = _state["session"]
    if session is None:
        raise RuntimeError("telegram upstream not connected")
    result = await session.call_tool(name, arguments or {})
    if result.isError:
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        raise RuntimeError(text or "telegram tool error")
    return list(result.content)


_manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)


async def _handle_http(scope, receive, send):
    await _manager.handle_request(scope, receive, send)


@contextlib.asynccontextmanager
async def _lifespan(_app):
    # One persistent upstream session for the whole process: a single Telegram login and
    # one cache warm, reused across all (stateless) HTTP tool calls.
    print(f"[telegram] launching upstream: uv run --directory {UPSTREAM_DIR} main.py", file=sys.stderr)
    async with stdio_client(_upstream_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            _state["session"] = session
            _state["tools"] = (await session.list_tools()).tools
            print(
                f"[telegram] {len(_state['tools'])} tool(s) ready on http://{HOST}:{PORT}/mcp",
                file=sys.stderr,
            )
            async with _manager.run():
                yield


app = Starlette(routes=[Mount("/mcp", app=_handle_http)], lifespan=_lifespan)


if __name__ == "__main__":
    _check_prereqs()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
