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

import functools
import os
import time
from typing import Awaitable, Callable, List, Union

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
from langchain_core.tools import StructuredTool

from agents.prompts import specialist_prompt
from core.a2a_client import call_agent
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


# ── peer-to-peer mesh ─────────────────────────────────────────────────────────

def _mesh_enabled() -> bool:
    return os.getenv("AGENT_MESH", "1").strip().lower() not in ("0", "false", "no", "off", "")


def _max_peer_depth() -> int:
    try:
        return int(os.getenv("AGENT_MAX_PEER_DEPTH", "2"))
    except ValueError:
        return 2


def _mesh_excluded() -> set[str]:
    """Specialists that must not consult peers, by name — an escape hatch for an
    agent that turns out to over-delegate in practice. Empty by default: taking an
    agent out of the mesh is an operational decision, so it belongs in .env rather
    than in an `if name == …` here."""
    raw = os.getenv("AGENT_MESH_EXCLUDE", "")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _incoming_depth(context: RequestContext) -> int:
    """Delegation depth carried on the inbound A2A message (orchestrator sends 1)."""
    meta = getattr(getattr(context, "message", None), "metadata", None) or {}
    try:
        return int(meta.get("delegation_depth", 1))
    except (TypeError, ValueError):
        return 1


def _incoming_user(context: RequestContext) -> str:
    """Signed-in account carried on the inbound A2A message (may be empty)."""
    meta = getattr(getattr(context, "message", None), "metadata", None) or {}
    return str(meta.get("user") or "").strip().lower()


def _peers_for(name: str, depth: int) -> List[str]:
    """Specialists this agent may consult, given how deep we already are.

    Full mesh (every specialist may consult every other), capped by depth so a
    consulted peer cannot re-delegate — that bounds the call tree and rules out
    cycles (A→B→A). Toggle with AGENT_MESH=0; cap with AGENT_MAX_PEER_DEPTH;
    take single agents out of the mesh with AGENT_MESH_EXCLUDE=recovery,route.
    """
    if not _mesh_enabled() or depth >= _max_peer_depth() or name in _mesh_excluded():
        return []
    return [s for s in AGENT_MCP_SCOPE if s != name]


_PEER_GUIDANCE = (
    "\n\nPEER CONSULTATION\n"
    "When a question genuinely needs another domain, you may consult a fellow "
    "specialist via these tools:\n{tools}\n"
    "Use them sparingly — only when their data is actually needed (e.g. you need "
    "today's weather, or recovery status to qualify your advice). Pass a focused, "
    "self-contained question and fold the answer into yours. Never delegate work "
    "your own tools can already do."
)


def _peer_prompt(peers: List[str]) -> str:
    tools = "\n".join(f"  • ask_{p} — the {p} specialist" for p in peers)
    return _PEER_GUIDANCE.format(tools=tools)


def _peer_tool(caller: str, peer: str, depth: int, sink: List[dict],
               status: Callable[[str], Awaitable[None]]) -> StructuredTool:
    """An ``ask_<peer>`` tool: A2A-call a fellow specialist one level deeper.

    The peer's artifact is stashed in ``sink`` (surfaced as ``sub_artifacts`` so
    the trace stays complete); the peer runs at ``depth + 1`` so it cannot itself
    re-delegate once the cap is reached.
    """
    url = A2A_AGENTS[peer]

    async def _ask(question: str) -> str:
        await status(f"{caller} → consulting {peer}…")
        try:
            ans, arts = await call_agent(url, question, metadata={"delegation_depth": depth + 1})
        except Exception as exc:  # peer down — degrade, don't fail the caller
            return f"(the {peer} specialist is unavailable: {type(exc).__name__})"
        for a in arts:
            if isinstance(a, dict):
                sink.append(a)
        return ans or f"(no answer from {peer})"

    return StructuredTool(
        name=f"ask_{peer}",
        description=f"Consult the {peer} specialist for its domain. Pass a focused, self-contained question.",
        args_schema={"type": "object",
                     "properties": {"question": {"type": "string",
                                    "description": f"Self-contained question for the {peer} specialist."}},
                     "required": ["question"]},
        coroutine=_ask,
    )


class SpecialistExecutor(AgentExecutor):
    """Runs one domain specialist; emits {agent, duration_ms, tool_calls} artifact.

    ``system_prompt`` may be a plain string OR a zero-arg callable rebuilt PER
    request (e.g. ``functools.partial(specialist_prompt, name)``). The callable
    form keeps the "Today is …" line fresh on a long-running server; a bare string
    is still accepted (the tests construct with strings).
    """

    def __init__(self, name: str, server_names: List[str],
                 system_prompt: Union[str, Callable[[], str]],
                 extra_tools: Callable[[List[dict]], List[StructuredTool]] | None = None) -> None:
        self.name = name
        self.server_names = server_names
        self.system_prompt = system_prompt
        # Optional factory for non-MCP tools (given the recorder so their calls
        # land in the same artifact/trace) — used by the coach's RAG search.
        self.extra_tools = extra_tools

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        t0 = time.perf_counter()
        user_text = context.get_user_input() or ""
        depth = _incoming_depth(context)
        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        async def status(msg: str) -> None:
            await updater.update_status(
                TaskState.working,
                message=updater.new_agent_message([Part(root=TextPart(text=msg))]),
            )

        recorder: List[dict] = []
        peer_artifacts: List[dict] = []
        answer = ""
        try:
            # The signed-in account (A2A metadata) becomes a connection header on
            # every scoped server — per-user servers (athlete) key their store on
            # it, the rest ignore it. Identity via header, never as a tool arg.
            user = _incoming_user(context)
            headers = ({name: {"X-FitDash-User": user} for name in self.server_names}
                       if user else None)
            host = scoped_host(self.server_names, headers=headers)
            tools = await build_tools(host, recorder)
            if self.extra_tools:
                tools = tools + self.extra_tools(recorder)
            peers = _peers_for(self.name, depth)
            prompt = self.system_prompt() if callable(self.system_prompt) else self.system_prompt
            if peers:
                tools = tools + [_peer_tool(self.name, p, depth, peer_artifacts, status) for p in peers]
                prompt = prompt + _peer_prompt(peers)
            await status(f"{self.name}: analysing…")
            agent = create_agent(model=get_chat_model(), tools=tools, system_prompt=prompt)
            with trace_span(f"{self.name}_agent", service=self.name,
                            role="specialist", question=user_text):
                out = await agent.ainvoke({"messages": [HumanMessage(user_text)]})
            answer = last_text(out.get("messages", []))
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, report upstream
            answer = f"({self.name} specialist error: {type(exc).__name__}: {exc})"

        dur = int((time.perf_counter() - t0) * 1000)
        data = {"agent": self.name, "duration_ms": dur, "tool_calls": recorder}
        if peer_artifacts:
            data["sub_artifacts"] = peer_artifacts
        await updater.add_artifact(
            [Part(root=DataPart(data=data))],
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
    print(f"[{name}] A2A server -> {url}  (bind {bind_host}:{port})", flush=True)
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
    # Rebuild the prompt PER request so the "Today is …" line never goes stale on a
    # long-running uvicorn server (GH #13).
    executor = SpecialistExecutor(name, AGENT_MCP_SCOPE[name],
                                  functools.partial(specialist_prompt, name))
    run_agent_server(name, executor, description=sdesc,
                     skill_id=sid, skill_name=sname, skill_desc=sdesc, tags=tags)
