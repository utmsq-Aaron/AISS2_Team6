"""Shared A2A-server plumbing for the LangGraph specialist agents.

Each specialist is a LangGraph ReAct agent (``langchain.agents.create_agent``)
over a ToolHost scoped to its MCP servers, hosted as an A2A server. The agent
runs **non-streaming** (``ainvoke``): the KIT gateway is unreliable on streamed
connections, and the plan only requires token-streaming at the orchestrator (and
even there it's optional). Progress is surfaced as A2A status-update messages;
the final answer is returned whole. The specialist's raw MCP calls are attached
as a DataPart artifact so the orchestrator can assemble the UI trace.
"""

from __future__ import annotations

import os
import time
from typing import List

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (AgentCapabilities, AgentCard, AgentSkill, DataPart, Part,
                       TaskState, TextPart)
from a2a.utils import new_task
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from agents.prompts import specialist_prompt
from core.config import A2A_AGENTS, AGENT_MCP_SCOPE, AGENT_PORTS
from core.llm import get_chat_model
from core.mcp_langchain import build_tools, scoped_host
from core.tracing import setup_tracing, trace_span


def last_text(messages: list) -> str:
    """Extract the final assistant text from a LangGraph message list."""
    for m in reversed(messages or []):
        c = getattr(m, "content", None)
        if isinstance(c, str):
            if c.strip():
                return c.strip()
        elif isinstance(c, list):
            txt = "".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if txt.strip():
                return txt.strip()
    return ""


class SpecialistExecutor(AgentExecutor):
    """Runs one domain specialist; emits {agent, duration_ms, tool_calls} artifact."""

    def __init__(self, name: str, server_names: List[str], system_prompt: str) -> None:
        self.name = name
        self.server_names = server_names
        self.system_prompt = system_prompt

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        t0 = time.perf_counter()
        user_text = context.get_user_input() or ""
        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        recorder: List[dict] = []
        answer = ""
        try:
            host = scoped_host(self.server_names)
            tools = await build_tools(host, recorder)
            await updater.update_status(
                TaskState.working,
                message=updater.new_agent_message([Part(root=TextPart(text=f"{self.name}: analysing…"))]),
            )
            agent = create_agent(model=get_chat_model(), tools=tools, system_prompt=self.system_prompt)
            with trace_span(f"{self.name}_agent", service=self.name,
                            role="specialist", question=user_text):
                out = await agent.ainvoke({"messages": [HumanMessage(user_text)]})
            answer = last_text(out.get("messages", []))
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, report upstream
            answer = f"({self.name} specialist error: {type(exc).__name__}: {exc})"

        dur = int((time.perf_counter() - t0) * 1000)
        await updater.add_artifact(
            [Part(root=DataPart(data={"agent": self.name, "duration_ms": dur, "tool_calls": recorder}))],
            name=f"{self.name}_artifact",
        )
        await updater.complete(message=updater.new_agent_message([Part(root=TextPart(text=answer))]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise RuntimeError("cancel not supported")


def run_agent_server(name: str, executor: AgentExecutor, *, description: str,
                     skill_id: str, skill_name: str, skill_desc: str, tags: List[str]) -> None:
    """Build the Agent Card + Starlette app for ``name`` and serve it via uvicorn."""
    port = AGENT_PORTS[name]
    url = A2A_AGENTS[name]
    bind_host = os.getenv("A2A_BIND_HOST", "127.0.0.1")
    setup_tracing(name)  # enable MLflow autologging for this agent process
    card = AgentCard(
        name=f"FitDash {name.capitalize()} Agent",
        description=description,
        url=url,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[AgentSkill(id=skill_id, name=skill_name, description=skill_desc, tags=tags)],
    )
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    app = A2AStarletteApplication(agent_card=card, http_handler=handler)
    print(f"[{name}] A2A server → {url}  (bind {bind_host}:{port})", flush=True)
    uvicorn.run(app.build(), host=bind_host, port=port, log_level="info")


# Per-specialist Agent Card skill metadata.
_SKILLS = {
    "recovery": ("recovery", "Recovery analysis",
                 "Analyse Garmin sleep, HRV, Body Battery and stress to judge recovery, "
                 "readiness and overtraining.", ["garmin", "recovery", "sleep", "hrv", "readiness"]),
    "load":     ("training_load", "Training-load analysis",
                 "Quantify training load (CTL/ATL/TSB), volume, trends and activity detail "
                 "from Strava and Garmin.", ["strava", "garmin", "load", "trends", "splits"]),
    "context":  ("context", "Weather + calendar context",
                 "Combine weather forecast with calendar to find trainable time windows.",
                 ["weather", "calendar", "planning"]),
    "route":    ("route", "Route planning",
                 "Plan routes, loops, trails and isochrones via OpenRouteService.",
                 ["routes", "planning", "trails"]),
}


def run_specialist(name: str) -> None:
    """Entry point for ``python -m agents.<name>_agent``."""
    sid, sname, sdesc, tags = _SKILLS[name]
    executor = SpecialistExecutor(name, AGENT_MCP_SCOPE[name], specialist_prompt(name))
    run_agent_server(name, executor, description=sdesc,
                     skill_id=sid, skill_name=sname, skill_desc=sdesc, tags=tags)
