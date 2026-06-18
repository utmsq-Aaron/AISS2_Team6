"""ToolHost → LangChain tools, scoped per agent.

Keeps the repo's load-bearing rule intact — "one uniform MCP client (ToolHost);
tools discovered, never hardcoded" — while narrowing each agent to its servers.
Each specialist builds a ToolHost over only its MCP servers, discovers their tools
at runtime, and wraps each as a LangChain ``StructuredTool``. The wrapper records
every call's FULL result (for the A2A artifact → trace) and returns a clipped copy
into the model's context (so GPS/timeline arrays don't blow up the prompt).

``build_tools`` is async on purpose: it must run inside the agent's event loop and
therefore uses ``ToolHost.alist_tools``/``acall_tool`` rather than the sync facade
(whose fresh-event-loop bridge would deadlock inside a running loop).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from langchain_core.tools import StructuredTool

from core.agent_trace import clip, error_of
from core.config import MCP_SERVERS
from core.host import ToolHost


def scoped_host(server_names: List[str], **kwargs: Any) -> ToolHost:
    """A ToolHost restricted to the named MCP servers (others are invisible)."""
    servers = {k: MCP_SERVERS[k] for k in server_names if k in MCP_SERVERS}
    return ToolHost(servers=servers, **kwargs)


async def build_tools(host: ToolHost, recorder: List[Dict[str, Any]]) -> List[StructuredTool]:
    """Discover every tool on ``host`` and wrap each as a recording StructuredTool.

    ``recorder`` is appended to on every call with the full record shape that
    ``core.agent_trace.build_trace`` consumes: ``{tool, args, label, result(JSON
    str), duration_ms, error}``.
    """
    specs = await host.alist_tools()
    tools: List[StructuredTool] = []
    for spec in specs:
        fn = spec.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        schema = fn.get("parameters") or {"type": "object", "properties": {}}
        tools.append(_make_tool(host, name, fn.get("description") or name, schema, recorder))
    return tools


def _make_tool(host: ToolHost, name: str, description: str,
               schema: Dict[str, Any], recorder: List[Dict[str, Any]]) -> StructuredTool:
    async def _call(**kwargs: Any) -> str:
        t = time.perf_counter()
        result = await host.acall_tool(name, kwargs)
        dur = int((time.perf_counter() - t) * 1000)
        recorder.append({
            "tool":        name,
            "args":        kwargs,
            "label":       name,
            "result":      result,          # FULL, unclipped → artifact/trace
            "duration_ms": dur,
            "error":       error_of(result),
        })
        return clip(result)                 # CLIPPED → model context
    # StructuredTool accepts a raw JSON-schema dict as args_schema (verified on
    # langchain-core 1.4); no pydantic model generation needed.
    return StructuredTool(
        name=name,
        description=description,
        args_schema=schema,
        coroutine=_call,
    )
