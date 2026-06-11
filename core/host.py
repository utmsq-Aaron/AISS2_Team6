"""ToolHost — the single MCP client/host for the whole app.

One uniform code path for every tool, regardless of which server provides it:
the agents, the FastAPI layer and the UI all call ``list_tools()`` / ``call_tool()``
here. Own servers and external/user-added servers are treated identically — each is
just a Streamable-HTTP MCP endpoint in ``core.config.MCP_SERVERS``.

Design (follows the Anthropic/MCP standard):
  - Tools are discovered from the servers, never hard-coded. No code names a tool.
  - Tool names are namespaced ``server__tool`` (OpenAI-function-name-safe).
  - A server that is unreachable (e.g. not started / missing credentials) is simply
    skipped during discovery — it never breaks the others.
  - Per-server credentials are passed as connection headers (declaration separate
    from auth); they never enter a tool's execution context.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from core.config import MCP_SERVERS, SEP


class ToolHost:
    """Uniform MCP client over a set of named Streamable-HTTP servers."""

    def __init__(
        self,
        servers: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, Dict[str, str]]] = None,
        timeout: float = 60.0,
    ) -> None:
        # name → url. Copy so per-user hosts can extend without mutating the global.
        self.servers: Dict[str, str] = dict(servers if servers is not None else MCP_SERVERS)
        # name → connection headers (e.g. {"Authorization": "Bearer ..."}). Auth is
        # declared here, separate from the tool definition — never in tool context.
        self.headers: Dict[str, Dict[str, str]] = headers or {}
        self.timeout = timeout

    # ── Async API (the real implementation) ───────────────────────────────────

    async def alist_tools(self) -> List[Dict[str, Any]]:
        """Discover every tool from every reachable server, in OpenAI tool format.

        Names are namespaced ``server__tool``. Unreachable servers are skipped.
        All servers are queried in parallel so one slow/missing server doesn't
        block the others.
        """
        async def _fetch(name: str, url: str) -> List[Dict[str, Any]]:
            async def _do():
                async with streamablehttp_client(url, headers=self.headers.get(name)) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        return [{
                            "type": "function",
                            "function": {
                                "name":        f"{name}{SEP}{t.name}",
                                "description": t.description or "",
                                "parameters":  t.inputSchema or {
                                    "type": "object", "properties": {}, "required": []
                                },
                            },
                        } for t in result.tools]
            try:
                return await asyncio.wait_for(_do(), timeout=self.timeout)
            except Exception:
                return []

        batches = await asyncio.gather(*[
            _fetch(name, url) for name, url in self.servers.items()
        ])
        tools: List[Dict[str, Any]] = []
        for batch in batches:
            tools.extend(batch)
        return tools

    async def acall_tool(self, name: str, args: Optional[Dict[str, Any]] = None) -> str:
        """Route a namespaced ``server__tool`` call to its server and return text/JSON."""
        server, _, tool = name.partition(SEP)
        url = self.servers.get(server)
        if not url:
            return json.dumps({"error": f"No server '{server}' for tool '{name}'"})

        async def _do() -> str:
            async with streamablehttp_client(url, headers=self.headers.get(server)) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments=args or {})
                    texts = [
                        getattr(c, "text", "")
                        for c in result.content
                        if getattr(c, "type", "") == "text"
                    ]
                    if result.isError:
                        return json.dumps({"error": "\n".join(texts) or "tool error"})
                    return "\n".join(texts) if texts else json.dumps({"result": "ok"})

        try:
            return await asyncio.wait_for(_do(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return json.dumps({"error": f"Tool '{name}' timed out after {self.timeout}s"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    # ── Sync facade (for the current sync codebase: shared.py / agents) ────────

    def list_tools(self) -> List[Dict[str, Any]]:
        return _run(self.alist_tools())

    def call_tool(self, name: str, args: Optional[Dict[str, Any]] = None) -> str:
        return _run(self.acall_tool(name, args))


# ── Async bridge ───────────────────────────────────────────────────────────────

def _run(coro):
    """Run a coroutine from sync code, even when called inside ThreadPool workers.

    A fresh event loop per call keeps it safe outside any running loop.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.close()


# Process-wide default host (own servers). Per-user hosts are constructed explicitly.
default_host = ToolHost()
