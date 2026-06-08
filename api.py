"""
FitDash REST API — FastAPI wrapper around the MCP server registry.

Start with:
    uvicorn api:app --reload --port 8000

Swagger UI:  http://localhost:8000/docs
ReDoc:       http://localhost:8000/redoc
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import servers.registry as registry  # noqa: F401 — triggers _setup()

app = FastAPI(
    title="FitDash MCP API",
    description=(
        "REST interface for all FitDash MCP servers.\n\n"
        "Every tool registered in `servers/registry.py` is automatically exposed here. "
        "Adding a new MCP server to the registry makes it available via this API without "
        "any further changes.\n\n"
        "**Servers:** Strava · Garmin · Routes · Weather"
    ),
    version="1.0.0",
    contact={"name": "FitDash", "url": "https://github.com/your-org/fitdash"},
    license_info={"name": "MIT"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Response / Request models ─────────────────────────────────────────────────

class ToolSpec(BaseModel):
    name: str
    description: str
    server: str
    input_schema: Dict[str, Any] = Field(alias="inputSchema")

    model_config = {"populate_by_name": True}


class ServerStatus(BaseModel):
    key: str
    description: str
    available: bool
    missing_env: List[str]


class ToolCallRequest(BaseModel):
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments matching the tool's inputSchema.",
        examples=[{"activity_id": "12345678"}],
    )


class ToolCallResponse(BaseModel):
    tool: str
    server: str
    result: Any


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message to send to the multi-agent orchestrator.")
    history: List[Dict[str, str]] = Field(
        default_factory=list,
        description='Prior conversation turns: [{"role": "user"|"assistant", "content": "..."}]',
    )


class ChatResponse(BaseModel):
    answer: str
    trace: Dict[str, Any] = Field(description="Full agent execution trace for debugging.")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get(
    "/servers",
    response_model=List[ServerStatus],
    summary="List all registered MCP servers",
    tags=["Registry"],
)
def list_servers():
    """Return the status of every server in the registry.

    Shows which servers are **available** (all required env vars are set)
    and which are **unavailable** (missing credentials).
    """
    return [
        ServerStatus(
            key=s["key"],
            description=s["description"],
            available=s["available"],
            missing_env=s["missing_env"],
        )
        for s in registry.config_status()
    ]


@app.get(
    "/tools",
    response_model=List[ToolSpec],
    summary="List all available tools across all servers",
    tags=["Tools"],
)
def list_tools():
    """Return every tool exposed by all **available** servers, with full JSON Schema.

    Use the `name` field to call a tool via `POST /tools/{name}`.
    """
    result = []
    for entry in registry._REGISTRY:
        server = entry.get_instance()
        if server is None:
            continue
        for tool in server.tools:
            result.append(
                ToolSpec(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    server=entry.key,
                    inputSchema=tool.get("inputSchema", {"type": "object", "properties": {}, "required": []}),
                )
            )
    return result


@app.post(
    "/tools/{tool_name}",
    response_model=ToolCallResponse,
    summary="Call a specific MCP tool",
    tags=["Tools"],
)
async def call_tool(tool_name: str, body: ToolCallRequest):
    """Execute a single MCP tool by name and return its result.

    Tool names and their argument schemas are listed at `GET /tools`.

    **Examples:**
    - `get_current_weather` — no arguments needed
    - `get_recent_activities` — `{"limit": 5}`
    - `plan_route` — `{"start": "Karlsruhe Hauptbahnhof", "end": "Ettlingen", "profile": "cycling-regular"}`
    """
    # Find which server owns this tool
    server_key = _find_server_key(tool_name)
    if server_key is None:
        available = sorted(registry.all_tool_names())
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool_name}' not found. Available tools: {available}",
        )

    raw = await registry.dispatch(tool_name, body.arguments)
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        result = raw

    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return ToolCallResponse(tool=tool_name, server=server_key, result=result)


@app.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send a message to the multi-agent FitDash orchestrator",
    tags=["Chat"],
)
def chat(body: ChatRequest):
    """Run the full 3-phase agent pipeline (Fetch → Visualize/Flyover → Chat).

    Returns the natural-language answer plus the full execution trace
    (agent timings, tool calls, action list).

    **Note:** This endpoint calls Strava/Garmin/Weather APIs and an LLM —
    expect response times of 5–15 seconds.
    """
    try:
        from ui.orchestrator import FitDashOrchestrator
        orch = FitDashOrchestrator()
        answer, trace = orch.run(body.message, body.history)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return ChatResponse(answer=answer, trace=trace)


@app.get("/health", summary="Health check", tags=["System"])
def health():
    """Returns 200 OK if the API is running."""
    available = [s["key"] for s in registry.config_status() if s["available"]]
    return {"status": "ok", "available_servers": available}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_server_key(tool_name: str) -> Optional[str]:
    for entry in registry._REGISTRY:
        server = entry.get_instance()
        if server is None:
            continue
        tool_names = {t["name"] for t in (server.tools or [])}
        if tool_name in tool_names:
            return entry.key
    return None
