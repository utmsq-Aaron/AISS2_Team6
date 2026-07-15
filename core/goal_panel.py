"""Goal-panel builder — a bounded background agent job.

Building or refreshing a goal's dashboard PANEL is a single-purpose agent job, the
same bounded shape as ``core.deep_analysis``, but it must NOT use
``core.orchestrator.run()`` — that call also does ``mem.remember`` + a soul refresh
+ writes ``.logs/agent_interactions.jsonl``, which would pollute the user's
conversation memory and profile with panel-build prompts that have nothing to do
with what the user actually said. Instead this builds a DEDICATED
``create_agent`` whose only tools are the specialists (``ask_<name>`` over A2A,
reused verbatim from ``core.orchestrator_agent._ask_tool``) plus ONE
``set_goal_panel`` tool bound to this exact ``(user, goal_id)`` via closure, so the
model cannot target another goal.

Two entry points call :func:`build_panel`:
  * ``core.orchestrator_agent``'s ``add_goal`` tool spawns it directly — the
    orchestrator process is durable (plain ``python -m``, no ``--reload``).
  * ``telegram_bridge``'s goal-build loop drains ``core.goal_build_queue`` (a
    form-created/refreshed goal is ENQUEUED, not spawned, because FastAPI runs
    under ``--reload`` and would kill an in-process daemon thread on the next code
    edit) and the hourly staleness check re-enqueues panels older than ~20h.

Bounded by three independent limits (LangGraph ``recursion_limit``, a wall-clock
``asyncio.wait_for`` timeout, each LLM call's own timeout) plus a concurrency
semaphore — exactly like ``deep_analysis``. Success is detected via a closure flag
flipped INSIDE the bound tool (not by parsing text), so a model that never calls it
is correctly marked ``"error"`` while any last-good panel is kept (never nulled).
A **skip-window** (not a hard lock) avoids a redundant concurrent build of the same
goal without risking a permanently wedged "building" state if a build crashes.
"""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from agents.prompts import goal_panel_prompt
from core.config import A2A_AGENTS, ORCHESTRATOR_SPECIALISTS
from core.llm import get_chat_model


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


GOAL_PANEL_RECURSION_LIMIT = _int_env("GOAL_PANEL_RECURSION_LIMIT", 30)
GOAL_PANEL_TIMEOUT = _int_env("GOAL_PANEL_TIMEOUT", 180)
GOAL_PANEL_MAX_CONCURRENT = max(1, _int_env("GOAL_PANEL_MAX_CONCURRENT", 3))

_SEM = threading.Semaphore(GOAL_PANEL_MAX_CONCURRENT)


def _panel_tool(user: str, goal_id: str, result: Dict[str, bool]) -> StructuredTool:
    """``set_goal_panel`` bound to (user, goal_id) via closure — the model cannot
    target another goal. Flips ``result["called"]`` so the caller can detect success
    without parsing text."""
    async def _set(headline: str, status: str, tiles: list,
                   progress: Optional[dict] = None, chart: Optional[dict] = None,
                   note: str = "") -> str:
        from core.goal_store import set_panel
        panel = {"headline": headline, "status": status, "tiles": tiles,
                 "progress": progress, "chart": chart, "note": note}
        g = set_panel(user, goal_id, panel)
        result["called"] = True
        return "Panel saved." if g else "(panel not saved — the goal may have been removed)"

    return StructuredTool(
        name="set_goal_panel",
        description=("Save this goal's dashboard panel. Call this ONCE, after gathering the "
                     "user's real data via the specialists. tiles: 2-4 concrete "
                     "{label,value,sub?} stats you computed. progress: optional "
                     "{pct 0-100, label}. chart: optional {kind:'line'|'bar', "
                     "points:[{x,y}], y_label?} — you supply the data points. note: a short "
                     "markdown note for context, caveats, or a coaching nudge the tiles don't "
                     "capture. If real data is missing, still call this with status:'unknown' "
                     "and say so in the note — never fabricate numbers."),
        args_schema={
            "type": "object",
            "properties": {
                "headline": {"type": "string",
                             "description": "One-line summary, e.g. 'On pace for sub-42'."},
                "status":   {"type": "string",
                             "enum": ["on_track", "at_risk", "behind", "reached", "unknown"]},
                "tiles":    {"type": "array", "minItems": 2, "maxItems": 4,
                             "items": {"type": "object",
                                       "properties": {
                                           "label": {"type": "string"},
                                           "value": {"type": "string"},
                                           "sub":   {"type": "string"},
                                       }, "required": ["label", "value"]}},
                "progress": {"type": "object",
                             "properties": {"pct": {"type": "number"}, "label": {"type": "string"}}},
                "chart":    {"type": "object",
                             "properties": {
                                 "kind": {"type": "string", "enum": ["line", "bar"]},
                                 "points": {"type": "array",
                                            "items": {"type": "object",
                                                      "properties": {"x": {}, "y": {"type": "number"}},
                                                      "required": ["x", "y"]}},
                                 "y_label": {"type": "string"},
                             }},
                "note":     {"type": "string"},
            },
            "required": ["headline", "status", "tiles"],
        },
        coroutine=_set,
    )


def build_panel(user: str, goal_id: str) -> None:
    """Sync entry point for a daemon thread. Never raises."""
    from core import goal_store

    goal = goal_store.get_goal(user, goal_id)
    if goal is None:
        return  # deleted before the build even started — quiet no-op

    if _already_building_recently(goal):
        return

    if not _SEM.acquire(timeout=GOAL_PANEL_TIMEOUT + 30):
        goal_store.set_panel_status(
            user, goal_id, "error",
            error="Couldn't gather your data for this panel. Try Retry.")
        return
    try:
        # Only the REAL builder stamps panel_build_started_at (build_started=True) — the
        # dedicated concurrency marker the skip-window reads. The API's optimistic
        # "building" flip must NOT stamp it, else this first-and-only build skips itself
        # (GH #29, Defect A).
        goal_store.set_panel_status(user, goal_id, "building", build_started=True)
        asyncio.run(_build_async(user, goal_id, goal))
    except Exception:  # noqa: BLE001 — last-resort guard
        goal_store.set_panel_status(
            user, goal_id, "error",
            error="Couldn't gather your data for this panel. Try Retry.")
    finally:
        _SEM.release()


def _already_building_recently(goal: dict) -> bool:
    """Skip-window: true iff a REAL build is already in flight and hasn't timed out yet.

    Reads ``panel_build_started_at`` ONLY — the marker stamped exclusively by the real
    builder (never by the API's optimistic "building" flip). A fresh form goal has it
    None → returns False → this first build runs (GH #29, Defect A); a genuinely
    concurrent second build sees the first's recent stamp → returns True → correctly
    skipped. NOT a hard lock — if that build crashed without updating the timestamp,
    this expires after GOAL_PANEL_TIMEOUT so the goal never wedges forever."""
    if goal.get("panel_status") != "building":
        return False
    stamp = goal.get("panel_build_started_at")
    if not stamp:
        return False
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
        return age < GOAL_PANEL_TIMEOUT
    except ValueError:
        return False


async def _build_async(user: str, goal_id: str, goal: dict) -> None:
    from core import goal_store
    # Lazy import to break the orchestrator_agent ↔ goal_panel import cycle.
    from core.orchestrator_agent import _ask_tool

    async def _noop_status(_msg: str) -> None:
        return None

    result: Dict[str, bool] = {"called": False}
    collected: List[dict] = []
    error_msg = "Couldn't gather your data for this panel. Try Retry."
    try:
        tools = [_ask_tool(s, A2A_AGENTS[s], collected, _noop_status)
                 for s in ORCHESTRATOR_SPECIALISTS]
        tools.append(_panel_tool(user, goal_id, result))
        agent = create_agent(model=get_chat_model(), tools=tools,
                             system_prompt=goal_panel_prompt(goal))
        await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [HumanMessage(
                    "Gather this user's real data for the goal above, then call "
                    "set_goal_panel exactly once with what you found."
                )]},
                config={"recursion_limit": GOAL_PANEL_RECURSION_LIMIT},
            ),
            timeout=GOAL_PANEL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        error_msg = "Panel build timed out. Try Retry."
    except Exception:  # noqa: BLE001 — degrade to "error" below
        pass

    if not result["called"]:
        goal_store.set_panel_status(user, goal_id, "error", error=error_msg)
