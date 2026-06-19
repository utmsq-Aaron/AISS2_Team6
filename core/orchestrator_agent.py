"""Orchestrator Agent — A2A server :9000.

A LangGraph agent that decomposes the user request, delegates to specialist agents
over A2A (each exposed as an ``ask_<name>`` tool), then synthesises one answer. It
collects the specialists' DataPart artifacts and assembles the UI ``trace`` via
``core.agent_trace.build_trace``, returning it as a ``trace`` DataPart alongside the
answer text. Runs non-streaming (``ainvoke``) for robustness against the gateway.

    python -m core.orchestrator_agent
"""

from __future__ import annotations

import time
import uuid
from typing import Awaitable, Callable, List

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Part, TaskState, TextPart
from a2a.utils import new_task
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from agents._base_executor import last_text, run_agent_server
from agents.prompts import orchestrator_prompt
from core.a2a_client import call_agent
from core.agent_trace import build_trace, collect_sources, ensure_sources
from core.config import A2A_AGENTS, ORCHESTRATOR_SPECIALISTS
from core.llm import get_chat_model
from core.tracing import trace_span


def _ask_tool(spec: str, url: str, collected: List[dict],
              status: Callable[[str], Awaitable[None]]) -> StructuredTool:
    """An ``ask_<spec>`` tool: A2A-call the specialist, stash its artifact, return text."""
    async def _ask(question: str) -> str:
        await status(f"Consulting {spec} agent…")
        try:
            answer, artifacts = await call_agent(url, question, metadata={"delegation_depth": 1})
        except Exception as exc:  # specialist down / transport error — degrade gracefully
            await status(f"{spec} agent unavailable.")
            return f"(The {spec} specialist is currently unavailable: {type(exc).__name__})"
        for a in artifacts:
            if isinstance(a, dict):
                collected.append(a)
        await status(f"{spec} agent responded.")
        return answer or f"(no answer from {spec})"

    return StructuredTool(
        name=f"ask_{spec}",
        description=f"Delegate to the {spec} specialist. Pass a focused, self-contained question.",
        args_schema={
            "type": "object",
            "properties": {"question": {"type": "string",
                           "description": f"Self-contained question for the {spec} specialist."}},
            "required": ["question"],
        },
        coroutine=_ask,
    )


class OrchestratorExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        t0 = time.perf_counter()
        user_text = context.get_user_input() or ""
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

        collected: List[dict] = []
        answer, error = "", None
        try:
            tools = [_ask_tool(s, A2A_AGENTS[s], collected, status) for s in ORCHESTRATOR_SPECIALISTS]
            agent = create_agent(model=get_chat_model(), tools=tools,
                                 system_prompt=orchestrator_prompt(ORCHESTRATOR_SPECIALISTS))
            await status("Coordinating specialists…")
            with trace_span("orchestrator_agent", service="orchestrator",
                            role="orchestrator", question=user_text):
                out = await agent.ainvoke({"messages": [HumanMessage(user_text)]})
            answer = last_text(out.get("messages", []))
        except Exception as exc:  # noqa: BLE001 — surface as trace error
            error = f"{type(exc).__name__}: {exc}"
            answer = answer or f"Orchestrator error: {error}"

        dur = int((time.perf_counter() - t0) * 1000)
        trace = build_trace(
            user_input=user_text, run_id=uuid.uuid4().hex[:8],
            specialist_artifacts=collected, answer=answer, total_ms=dur, error=error,
        )
        # Guarantee the user sees the real book citations whenever a RAG specialist
        # (fitness) was consulted — the synthesis model otherwise sometimes drops them.
        trace["answer"] = ensure_sources(trace["answer"], collect_sources(trace["tool_calls"]))
        await updater.add_artifact([Part(root=DataPart(data=trace))], name="trace")
        await updater.complete(
            message=updater.new_agent_message([Part(root=TextPart(text=trace["answer"]))]),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise RuntimeError("cancel not supported")


if __name__ == "__main__":
    run_agent_server(
        "orchestrator", OrchestratorExecutor(),
        description="FitDash Orchestrator — decomposes the request and coordinates the "
                    "recovery, load, context and route specialists via A2A.",
        skill_id="orchestrate", skill_name="Training coordination",
        skill_desc="Coordinate specialist agents and synthesise a training recommendation.",
        tags=["orchestrator", "coordination", "training"],
    )
