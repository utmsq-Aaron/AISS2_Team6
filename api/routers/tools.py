"""Generic MCP tool access — the data path every dashboard tab uses.

These are plain `def` handlers so FastAPI runs them in its threadpool; the
ToolHost sync facade spins its own event loop per call, which is only safe when
there is no event loop already running in the thread.
"""

import json
from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from api.deps import get_host

router = APIRouter()


@router.get("/tools")
def list_tools():
    """All tools discovered across reachable MCP servers (OpenAI tool schema)."""
    tools = get_host().list_tools()
    return {"count": len(tools), "tools": tools}


class ToolCall(BaseModel):
    name: str
    args: Dict[str, Any] = {}


@router.post("/tools/call")
def call_tool(body: ToolCall):
    """Call `server__tool` with args. Result is parsed to JSON when possible."""
    raw = get_host().call_tool(body.name, body.args)
    try:
        return {"name": body.name, "ok": True, "data": json.loads(raw)}
    except (json.JSONDecodeError, TypeError):
        return {"name": body.name, "ok": True, "data": raw, "text": True}
