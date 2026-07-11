"""Orchestrator Agent — A2A server :9000.

A LangGraph agent that decomposes the user request, delegates to specialist agents
over A2A (each exposed as an ``ask_<name>`` tool), then synthesises one answer. It
collects the specialists' DataPart artifacts and assembles the UI ``trace`` via
``core.agent_trace.build_trace``, returning it as a ``trace`` DataPart alongside the
answer text. Runs non-streaming (``ainvoke``) for robustness against the gateway.

    python -m core.orchestrator_agent
"""

from __future__ import annotations

import threading
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
from datetime import datetime, timezone

from core.a2a_client import call_agent
from core.agent_trace import build_trace, collect_sources, ensure_sources
from core.config import A2A_AGENTS, ORCHESTRATOR_SPECIALISTS
from core.llm import get_chat_model
from core.schedule_store import to_utc as schedule_to_utc
from core.schedule_store import upsert as schedule_upsert
from core.tracing import trace_span


def _ask_tool(spec: str, url: str, collected: List[dict],
              status: Callable[[str], Awaitable[None]],
              user: str = "") -> StructuredTool:
    """An ``ask_<spec>`` tool: A2A-call the specialist, stash its artifact, return text.

    The signed-in account rides on the message metadata (same channel as
    ``delegation_depth``) so per-user specialists (coach → athlete store) act on
    the right athlete — identity is never a tool argument the model fills in.
    """
    async def _ask(question: str) -> str:
        await status(f"Consulting {spec} agent…")
        meta = {"delegation_depth": 1, **({"user": user} if user else {})}
        try:
            answer, artifacts = await call_agent(url, question, metadata=meta)
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


def _goal_tools(user: str) -> List[StructuredTool]:
    """``add_goal`` / ``update_goal`` / ``set_goal_panel`` — the coach's write path to
    the user's freeform, possibly-MULTIPLE goals (``data/user_memory/<slug>/goals.json``).
    The web app writes the same store with ``source="user"``; here ``source="coach"``.
    A newly added goal's dashboard panel builds in the BACKGROUND — spawned directly
    (this orchestrator process is durable, unlike FastAPI's ``--reload``, which is why
    web-created goals instead enqueue for the bridge to build; see ``core.goal_panel``
    / ``core.goal_build_queue``)."""
    from core import goal_store

    async def _add(text: str, sport: str = None) -> str:
        if not user:
            return "(no signed-in user — goal not saved)"
        g = goal_store.add_goal(user, text, sport=sport, source="coach")
        if not g:
            return "(goal not saved)"
        try:
            from core import goal_panel
            threading.Thread(target=goal_panel.build_panel,
                             args=(user, g["id"]), daemon=True).start()
        except Exception:  # noqa: BLE001 — the goal itself is still saved
            pass
        return f"Goal added (id={g['id']}): {g['text']}. Building its dashboard panel now."

    add_tool = StructuredTool(
        name="add_goal",
        description=("Record a new training goal for the user, in their own words. Sport-"
                     "specific goals are common — pass sport when it's clear (e.g. 'running', "
                     "'swimming'). A dashboard panel builds automatically in the background."),
        args_schema={
            "type": "object",
            "properties": {
                "text":  {"type": "string", "description": "The goal, close to how the user said it."},
                "sport": {"type": "string", "description": "Optional sport/discipline tag."},
            },
            "required": ["text"],
        },
        coroutine=_add,
    )

    async def _update(goal_id: str, text: str = None, sport: str = None,
                      status: str = None) -> str:
        if not user:
            return "(no signed-in user — goal not updated)"
        g = goal_store.update_goal(user, goal_id, text=text, sport=sport, status=status)
        return f"Goal updated (id={g['id']}): {g['text']} [{g['status']}]" if g \
            else "(goal not found or nothing changed)"

    update_tool = StructuredTool(
        name="update_goal",
        description=("Revise an existing goal's text, sport, or status (active/achieved/"
                     "archived). Pass only the fields that change. Use this to mark a goal "
                     "achieved or archive one the user no longer wants tracked."),
        args_schema={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "The goal's id (from the Active goals list)."},
                "text":    {"type": "string"},
                "sport":   {"type": "string"},
                "status":  {"type": "string", "enum": ["active", "achieved", "archived"]},
            },
            "required": ["goal_id"],
        },
        coroutine=_update,
    )

    async def _set_panel(goal_id: str, headline: str, status: str, tiles: list,
                        progress: dict = None, chart: dict = None, note: str = "") -> str:
        if not user:
            return "(no signed-in user — panel not saved)"
        panel = {"headline": headline, "status": status, "tiles": tiles,
                "progress": progress, "chart": chart, "note": note}
        g = goal_store.set_panel(user, goal_id, panel)
        return "Panel saved." if g else "(panel not saved — goal not found)"

    panel_tool = StructuredTool(
        name="set_goal_panel",
        description=("Build/refresh a goal's dashboard panel from real data you just gathered "
                     "via the specialists. tiles: 2-4 concrete {label,value,sub?} stats. "
                     "progress: optional {pct 0-100, label}. chart: optional {kind:'line'|'bar', "
                     "points:[{x,y}], y_label?}. note: a short markdown note. Use status:"
                     "'unknown' rather than guessing if you don't have real data for this goal."),
        args_schema={
            "type": "object",
            "properties": {
                "goal_id":  {"type": "string"},
                "headline": {"type": "string"},
                "status":   {"type": "string",
                             "enum": ["on_track", "at_risk", "behind", "reached", "unknown"]},
                "tiles":    {"type": "array", "minItems": 2, "maxItems": 4,
                             "items": {"type": "object",
                                       "properties": {"label": {"type": "string"},
                                                       "value": {"type": "string"},
                                                       "sub": {"type": "string"}},
                                       "required": ["label", "value"]}},
                "progress": {"type": "object",
                             "properties": {"pct": {"type": "number"}, "label": {"type": "string"}}},
                "chart":    {"type": "object",
                             "properties": {
                                 "kind": {"type": "string", "enum": ["line", "bar"]},
                                 "points": {"type": "array",
                                            "items": {"type": "object",
                                                      "properties": {"x": {}, "y": {"type": "number"}},
                                                      "required": ["x", "y"]}},
                                 "y_label": {"type": "string"}}},
                "note":     {"type": "string"},
            },
            "required": ["goal_id", "headline", "status", "tiles"],
        },
        coroutine=_set_panel,
    )

    return [add_tool, update_tool, panel_tool]


def _schedule_tool(user: str) -> StructuredTool:
    """``schedule_followup`` — the coach schedules its own future re-activation.
    Backed by ``core.schedule_store``; the bridge poll loop fires it (dedup by
    reason_key, fire-once, cross-chat)."""
    async def _schedule(fire_at_iso: str, reason_key: str, note: str) -> str:
        if not user:
            return "(no signed-in user — nothing scheduled)"
        fa = schedule_to_utc(fire_at_iso)
        if fa is None:
            return "(not scheduled: could not parse the time — use ISO 8601)"
        if fa <= datetime.now(timezone.utc):
            return "(not scheduled: the time is in the past)"
        try:
            e = schedule_upsert(user, (reason_key or "followup").strip(), fire_at_iso, note,
                                kind="wakeup", source="coach")
            return f"Follow-up scheduled for {fa.isoformat()} ({reason_key})." if e \
                else "(schedule skipped)"
        except Exception as exc:  # noqa: BLE001
            return f"(schedule skipped: {type(exc).__name__})"

    return StructuredTool(
        name="schedule_followup",
        description=("Schedule your own future re-activation (a proactive check-in). Use before/"
                     "after a calendar workout, for a goal check-in, or to verify the user acted. "
                     "reason_key is a short stable slug that DEDUPS across chats — reusing it "
                     "replaces the pending follow-up instead of stacking."),
        args_schema={
            "type": "object",
            "properties": {
                "fire_at_iso": {"type": "string",
                                "description": "When to re-activate, ISO 8601 (e.g. 2026-07-12T07:00:00). "
                                               "Bare local time is interpreted as Europe/Berlin."},
                "reason_key":  {"type": "string",
                                "description": "Short stable slug for WHY (e.g. 'weekly-goal-checkin'). Reuse to replace."},
                "note":        {"type": "string",
                                "description": "The instruction future-you runs then, grounded in fresh data."},
            },
            "required": ["fire_at_iso", "reason_key", "note"],
        },
        coroutine=_schedule,
    )


def _deep_tool(user: str) -> StructuredTool:
    """``start_deep_analysis`` — fire-and-return a long background analysis. Records a
    job and spawns the worker off-thread; the report is delivered to the Coach chat
    later (the user does NOT wait). See core.deep_analysis / core.deep_jobs."""
    async def _start(topic: str, rationale: str = "") -> str:
        if not user:
            return "(deep analysis needs a signed-in user — answer inline instead)"
        try:
            from core import deep_analysis, deep_jobs
            job_id = deep_jobs.create_job(user, topic, rationale)
            if not job_id:
                return "(could not start deep analysis)"
            threading.Thread(target=deep_analysis.run_deep_job,
                             args=(user, job_id, topic, rationale), daemon=True).start()
            return f"DEEP_JOB_STARTED {job_id}: {topic}"
        except Exception as exc:  # noqa: BLE001
            return f"(could not start deep analysis: {type(exc).__name__})"

    return StructuredTool(
        name="start_deep_analysis",
        description=("Kick off a LONG, multi-round background analysis for an open-ended/complex "
                     "request that can't be answered in one quick turn. Fire-and-return: it runs "
                     "in the background and delivers a full report to the user's Coach chat later. "
                     "After calling this, reply with ONE short line saying you're on it and will "
                     "report back — do NOT try to do the deep work yourself."),
        args_schema={
            "type": "object",
            "properties": {
                "topic":     {"type": "string", "description": "What to deeply analyze (self-contained)."},
                "rationale": {"type": "string", "description": "Why it needs deep multi-round work (optional)."},
            },
            "required": ["topic"],
        },
        coroutine=_start,
    )


def _detect_deep_job(messages: list) -> dict | None:
    """If the orchestrator kicked off a background deep analysis this turn, return an
    action the UI can render (an accepted "I'll report back" card)."""
    for m in reversed(messages or []):
        name = getattr(m, "name", None)
        content = getattr(m, "content", "") or ""
        if name == "start_deep_analysis" and isinstance(content, str) \
                and content.startswith("DEEP_JOB_STARTED"):
            rest = content[len("DEEP_JOB_STARTED"):].strip()
            job_id, _, topic = rest.partition(":")
            return {"type": "background_job", "job_id": job_id.strip(), "topic": topic.strip()}
    return None


class OrchestratorExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        t0 = time.perf_counter()
        user_text = context.get_user_input() or ""
        # The signed-in account rides on the A2A message metadata (set by
        # core.orchestrator.run), same channel the mesh uses for delegation_depth.
        try:
            user = (getattr(context.message, "metadata", None) or {}).get("user")
        except Exception:  # noqa: BLE001
            user = None
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
        deep_action = None
        try:
            tools = [_ask_tool(s, A2A_AGENTS[s], collected, status, user=user or "")
                     for s in ORCHESTRATOR_SPECIALISTS]
            if user:
                tools.extend(_goal_tools(user))
                tools.append(_schedule_tool(user))
                tools.append(_deep_tool(user))
            agent = create_agent(model=get_chat_model(), tools=tools,
                                 system_prompt=orchestrator_prompt(ORCHESTRATOR_SPECIALISTS))
            await status("Coordinating specialists…")
            with trace_span("orchestrator_agent", service="orchestrator",
                            role="orchestrator", question=user_text):
                out = await agent.ainvoke({"messages": [HumanMessage(user_text)]})
            answer = last_text(out.get("messages", []))
            deep_action = _detect_deep_job(out.get("messages", []))
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
        if deep_action:
            trace.setdefault("actions", []).append(deep_action)
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
