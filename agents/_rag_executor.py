"""A2A executor for the Fitness specialist — the one agent backed by RAG, not MCP.

Every other specialist discovers tools from a scoped ToolHost (live APIs). The
Fitness agent instead gets a single local tool, ``search_fitness_literature``,
that retrieves passages from the public-domain fitness-book vector index
(:mod:`core.fitness_rag`). The retrieved-call is recorded into the same recorder
shape the MCP wrapper uses, so it flows into the orchestrator's ``trace`` and the
UI's agent-trace panel exactly like any other tool call — no UI change needed.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Part, TaskState, TextPart
from a2a.utils import new_task
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from agents._base_executor import last_text, run_agent_server
from agents.prompts import specialist_prompt
from core.agent_trace import clip, error_of
from core.fitness_rag import get_retriever
from core.llm import get_chat_model
from core.tracing import trace_span

_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string",
                  "description": "What to look up in the fitness literature "
                                 "(a concept, technique, principle, or question)."},
        "k": {"type": "integer", "description": "How many passages to retrieve (default 5).",
              "default": 5},
    },
    "required": ["query"],
}


def _make_search_tool(recorder: List[Dict[str, Any]]) -> StructuredTool:
    """A recording ``search_fitness_literature`` tool over the vector index."""
    async def _search(query: str, k: int = 5) -> str:
        t = time.perf_counter()
        try:
            hits = await asyncio.to_thread(get_retriever().search, query, int(k or 5))
            payload: Dict[str, Any] = {
                "query": query,
                "results": [
                    {"rank": i + 1, "score": h.get("score"),
                     "source": f"{h.get('title', '?')} — {h.get('author', '?')}",
                     "passage": h.get("text", "")}
                    for i, h in enumerate(hits)
                ],
            }
            result = json.dumps(payload, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 — surface as a tool error, don't crash
            result = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        dur = int((time.perf_counter() - t) * 1000)
        recorder.append({
            "tool":        "search_fitness_literature",
            "args":        {"query": query, "k": int(k or 5)},
            "label":       "search_fitness_literature",
            "result":      result,
            "duration_ms": dur,
            "error":       error_of(result),
        })
        return clip(result)

    return StructuredTool(
        name="search_fitness_literature",
        description="Search a curated library of fitness/physical-culture books for "
                    "relevant passages. Use it for questions about training principles, "
                    "exercise technique, conditioning, recovery theory and general "
                    "fitness/health knowledge. Returns ranked passages with their source.",
        args_schema=_SEARCH_SCHEMA,
        coroutine=_search,
    )


class FitnessExecutor(AgentExecutor):
    """Runs the Fitness specialist; emits the same {agent, tool_calls} artifact."""

    def __init__(self, name: str, system_prompt: str) -> None:
        self.name = name
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
            tools = [_make_search_tool(recorder)]
            await updater.update_status(
                TaskState.working,
                message=updater.new_agent_message(
                    [Part(root=TextPart(text=f"{self.name}: consulting the fitness library…"))]),
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


def run_fitness() -> None:
    """Entry point for ``python -m agents.fitness_agent``."""
    executor = FitnessExecutor("fitness", specialist_prompt("fitness"))
    run_agent_server(
        "fitness", executor,
        description="FitDash Fitness Expert — answers training, technique and exercise-science "
                    "questions from a curated library of fitness literature (RAG over a vector DB).",
        skill_id="fitness_knowledge", skill_name="Fitness knowledge (RAG)",
        skill_desc="Answer fitness, training-method, technique and exercise-science questions "
                   "grounded in a vector database of fitness books.",
        tags=["fitness", "rag", "knowledge", "training", "technique"],
    )
