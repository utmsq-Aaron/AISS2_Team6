"""Deep-analysis worker — a bounded, multi-round background analysis.

The orchestrator triages open-ended requests as DEEP and calls ``start_deep_analysis``,
which spawns ``run_deep_job`` on a daemon thread (fire-and-return — the user gets an
instant "on it" and does NOT wait). This worker reuses the orchestrator's OWN
``ask_<specialist>`` tools but with a raised recursion budget and a plan → gather →
reflect → verify → write prompt, then hands the finished report to
``core.proactive_outbox`` for delivery to the Coach chat + Telegram.

Three independent termination bounds keep it safe: LangGraph ``recursion_limit``, a
wall-clock timeout (``DEEP_JOB_TIMEOUT``), and each LLM call's own 120 s timeout. A
concurrency semaphore caps gateway load. Everything degrades to a delivered failure
note — the worker never leaves the user hanging.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from agents._base_executor import last_text
from agents.prompts import deep_analysis_prompt
from core.agent_trace import build_trace, collect_sources, ensure_sources
from core.config import A2A_AGENTS, ORCHESTRATOR_SPECIALISTS
from core.llm import get_chat_model


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


DEEP_RECURSION_LIMIT = _int_env("DEEP_RECURSION_LIMIT", 60)
DEEP_JOB_TIMEOUT = _int_env("DEEP_JOB_TIMEOUT", 600)
DEEP_MAX_CONCURRENT = max(1, _int_env("DEEP_MAX_CONCURRENT", 2))

_SEM = threading.Semaphore(DEEP_MAX_CONCURRENT)


def _task_message(topic: str, rationale: str) -> str:
    extra = f"\n\nWhy this needs depth: {rationale}" if rationale else ""
    return (
        f"DEEP ANALYSIS TASK: {topic}{extra}\n\n"
        "Work in rounds: (1) plan and gather from every relevant specialist, "
        "(2) reflect on gaps and contradictions in what came back, (3) re-delegate to "
        "close the gaps, (4) write a concrete, specific final report with clear "
        "recommendations. Be exhaustive and cite the actual numbers."
    )


def run_deep_job(user: str, job_id: str, topic: str, rationale: str = "") -> None:
    """Daemon-thread entry point (sync). Runs the async worker in its own loop."""
    if not _SEM.acquire(timeout=DEEP_JOB_TIMEOUT + 60):
        _fail(user, job_id, topic, "busy — too many deep analyses running")
        return
    try:
        asyncio.run(_run_async(user, job_id, topic, rationale))
    except Exception as exc:  # noqa: BLE001 — last-resort guard
        _fail(user, job_id, topic, f"{type(exc).__name__}: {exc}")
    finally:
        _SEM.release()


async def _run_async(user: str, job_id: str, topic: str, rationale: str) -> None:
    from core import deep_jobs, proactive_outbox
    # Lazy import to break the orchestrator_agent ↔ deep_analysis import cycle.
    from core.orchestrator_agent import _ask_tool

    deep_jobs.update_status(user, job_id, "running")
    collected: list = []

    async def _noop_status(_msg: str) -> None:
        return None

    t0 = time.perf_counter()
    try:
        tools = [_ask_tool(s, A2A_AGENTS[s], collected, _noop_status)
                 for s in ORCHESTRATOR_SPECIALISTS]
        agent = create_agent(model=get_chat_model(), tools=tools,
                             system_prompt=deep_analysis_prompt(topic))
        out = await asyncio.wait_for(
            agent.ainvoke({"messages": [HumanMessage(_task_message(topic, rationale))]},
                          config={"recursion_limit": DEEP_RECURSION_LIMIT}),
            timeout=DEEP_JOB_TIMEOUT,
        )
        report = last_text(out.get("messages", [])) or "(the deep analysis produced no output)"
        dur = int((time.perf_counter() - t0) * 1000)
        trace = build_trace(user_input=topic, run_id=job_id, specialist_artifacts=collected,
                            answer=report, total_ms=dur, error=None)
        trace["answer"] = ensure_sources(trace["answer"], collect_sources(trace["tool_calls"]))
        proactive_outbox.enqueue(user, trace["answer"],
                                 title=f"Deep analysis: {topic[:60]}", trace=trace,
                                 kind="deep_report")
        deep_jobs.update_status(user, job_id, "done")
    except asyncio.TimeoutError:
        _fail(user, job_id, topic, f"timed out after {DEEP_JOB_TIMEOUT}s")
    except Exception as exc:  # noqa: BLE001
        _fail(user, job_id, topic, f"{type(exc).__name__}: {exc}")


def _fail(user: str, job_id: str, topic: str, reason: str) -> None:
    from core import deep_jobs, proactive_outbox
    try:
        deep_jobs.update_status(user, job_id, "failed", error=reason)
    except Exception:  # noqa: BLE001
        pass
    try:
        proactive_outbox.enqueue(
            user,
            f"I couldn't finish the deep dive on '{topic}' ({reason}). Want me to retry?",
            title="Deep analysis failed", kind="deep_report")
    except Exception:  # noqa: BLE001
        pass
