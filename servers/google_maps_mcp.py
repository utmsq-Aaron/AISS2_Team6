"""Google Maps — Streamable-HTTP proxy for the official stdio MCP server.

The upstream ``@modelcontextprotocol/server-google-maps`` package is a stdio
server distributed via npm. This proxy keeps the app's existing architecture:
``core.host.ToolHost`` talks Streamable HTTP to every MCP server, while this
process bridges Google Maps' stdio server onto the same bus.

Run locally:   python -m servers.google_maps_mcp
Endpoint:      http://127.0.0.1:8108/mcp   (override host/port via env)

Requires:
    GOOGLE_MAPS_API_KEY      Google Maps Platform API key
    npx                      used to launch @modelcontextprotocol/server-google-maps
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys

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

HOST = os.getenv("GOOGLE_MAPS_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("GOOGLE_MAPS_MCP_PORT", "8108"))
PACKAGE = os.getenv("GOOGLE_MAPS_MCP_PACKAGE", "@modelcontextprotocol/server-google-maps")


def _check_prereqs() -> None:
    """Fail fast with actionable errors; unreachable servers are skipped by ToolHost."""
    if not os.getenv("GOOGLE_MAPS_API_KEY"):
        sys.exit("[google_maps] missing required env: GOOGLE_MAPS_API_KEY (see .env.example).")
    if shutil.which("npx") is None:
        sys.exit("[google_maps] `npx` not found on PATH — install Node.js/npm to run the upstream server.")


def _upstream_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=shutil.which("npx") or "npx",
        args=["-y", PACKAGE],
        env={**os.environ},
    )


_state: dict = {"session": None, "tools": []}
server = Server("google_maps")


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return _state["tools"]


@server.call_tool()
async def _call_tool(name: str, arguments: dict | None):
    session: ClientSession | None = _state["session"]
    if session is None:
        raise RuntimeError("google_maps upstream not connected")
    result = await session.call_tool(name, arguments or {})
    if result.isError:
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        raise RuntimeError(text or "google_maps tool error")
    return list(result.content)


_manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)


async def _handle_http(scope, receive, send):
    await _manager.handle_request(scope, receive, send)


@contextlib.asynccontextmanager
async def _lifespan(_app):
    print(f"[google_maps] launching upstream: npx -y {PACKAGE}", file=sys.stderr)
    async with stdio_client(_upstream_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            _state["session"] = session
            _state["tools"] = (await session.list_tools()).tools
            print(
                f"[google_maps] {len(_state['tools'])} tool(s) ready on http://{HOST}:{PORT}/mcp",
                file=sys.stderr,
            )
            async with _manager.run():
                yield


app = Starlette(routes=[Mount("/mcp", app=_handle_http)], lifespan=_lifespan)


if __name__ == "__main__":
    _check_prereqs()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
