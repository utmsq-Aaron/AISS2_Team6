"""Structured athlete state for the Coach tab — a thin adapter over athlete_mcp.

Unlike the freeform goals (``core.goal_store``, direct file access), the athlete
store lives behind its own MCP server: this router fronts it through a per-request
ToolHost whose ``X-FitDash-User`` connection header carries the signed-in account
(identity via header, never a tool argument — the calendar server's pattern).
The React Coach tab reads ``GET /overview``; plan GENERATION delegates to the
coach agent (:9006) over A2A in a background thread — the tab polls the overview
until ``plan`` appears (or ``/plan/status`` reports the error).
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import current_user
from core.a2a_client import call_agent
from core.config import A2A_AGENTS, MCP_SERVERS
from core.host import ToolHost

router = APIRouter(prefix="/athlete", tags=["athlete"])

# user → "running" | "error: …" (in-memory; --reload wipes it, the UI just re-polls)
_gen_status: Dict[str, str] = {}
_gen_lock = threading.Lock()


def _host(user: str) -> ToolHost:
    """A per-request ToolHost scoped to the athlete server, acting as ``user``."""
    return ToolHost(servers={"athlete": MCP_SERVERS["athlete"]},
                    headers={"athlete": {"X-FitDash-User": user}})


def _call(user: str, tool: str, args: Optional[Dict[str, Any]] = None) -> Any:
    result = _host(user).call_tool(f"athlete__{tool}", args or {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            pass
    if isinstance(result, dict) and result.get("error"):
        # "no plan yet"-style soft errors are the caller's business; transport-level
        # failures (server down) surface as 502 via the generic branch below.
        return result
    return result


class RaceGoalBody(BaseModel):
    race_name: str
    race_date: str                       # ISO YYYY-MM-DD
    distance_km: float
    target_time: str = ""
    weekly_sessions: int = 4
    preferred_days: str = ""
    priority: str = "A"                  # "A" (season goal, drives the plan) | "B" | "C" (milestones)


class TimelineEventBody(BaseModel):
    event_type: str                      # injury | illness | race | note
    title: str
    start_date: str
    end_date: str = ""
    severity: str = ""
    blocked_sports: str = ""


@router.get("/overview")
def overview(user: str = Depends(current_user)) -> dict:
    """Everything the Coach tab renders, in one call (never 404s for a new user)."""
    out = _call(user, "get_athlete_overview")
    if not isinstance(out, dict):
        raise HTTPException(status_code=502, detail="athlete server unavailable")
    with _gen_lock:
        out["plan_generation"] = _gen_status.get(user)
    return out


@router.post("/goal")
def set_goal(body: RaceGoalBody, user: str = Depends(current_user)) -> dict:
    out = _call(user, "set_race_goal", body.model_dump())
    if isinstance(out, dict) and out.get("error"):
        raise HTTPException(status_code=422, detail=out["error"])
    return out


@router.delete("/goal/{race_id}")
def delete_goal(race_id: str, user: str = Depends(current_user)) -> dict:
    out = _call(user, "delete_race_goal", {"race_id": race_id})
    if isinstance(out, dict) and out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


class ProfileBody(BaseModel):
    age: int = 0                         # years — drives the literature HFmax default


@router.post("/profile")
def set_profile(body: ProfileBody, user: str = Depends(current_user)) -> dict:
    out = _call(user, "set_athlete_profile", body.model_dump())
    if isinstance(out, dict) and out.get("error"):
        raise HTTPException(status_code=422, detail=out["error"])
    return out


@router.post("/timeline")
def add_event(body: TimelineEventBody, user: str = Depends(current_user)) -> dict:
    out = _call(user, "add_timeline_event", body.model_dump())
    if isinstance(out, dict) and out.get("error"):
        raise HTTPException(status_code=422, detail=out["error"])
    return out


@router.delete("/timeline/{event_id}")
def delete_event(event_id: str, user: str = Depends(current_user)) -> dict:
    out = _call(user, "delete_timeline_event", {"event_id": event_id})
    if isinstance(out, dict) and out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


_GENERATE_INSTRUCTION = (
    "Build the complete training plan for my stored race goal now. Follow your "
    "workflow strictly: read the overview; if zones are missing, compute them from "
    "my AGE via athlete__compute_zones (age-based HFmax is the DEFAULT — do not use "
    "Garmin's observed max HR unless I explicitly logged a real all-out effort) plus "
    "resting HR from garmin. Read my current weekly volume from the last Strava weeks. "
    "If the recent weeks are EMPTY (training break), use the weekly volume of the last "
    "ACTIVE training weeks from Strava history instead, restart at no more than 60 % of "
    "it and note the break in the first weeks' workout rationales — never invent a "
    "volume that is nowhere in the data. Then call athlete__scaffold_plan with it; fill "
    "every week with concrete workouts (my zones, day preferences, one sentence of "
    "rationale, a literature source where sensible); save with athlete__save_plan and "
    "fix any violations until saving succeeds. Finish with a short summary of the plan."
)


def _run_generation(user: str) -> None:
    try:
        answer, _ = asyncio.run(call_agent(
            A2A_AGENTS["coach"], _GENERATE_INSTRUCTION,
            metadata={"user": user, "delegation_depth": 1},
            timeout=600.0,
        ))
        plan = _call(user, "get_plan")
        with _gen_lock:
            if isinstance(plan, dict) and not plan.get("error"):
                _gen_status.pop(user, None)          # success — overview now has the plan
            else:
                _gen_status[user] = f"error: coach finished without a stored plan ({(answer or '')[:200]})"
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the thread
        with _gen_lock:
            _gen_status[user] = f"error: {type(exc).__name__}: {exc}"


@router.post("/plan/generate")
def generate_plan(user: str = Depends(current_user)) -> dict:
    """Kick off plan generation via the coach agent; poll /overview for the result."""
    ov = _call(user, "get_athlete_overview")
    if not isinstance(ov, dict) or not (ov.get("profile") or {}).get("race"):
        raise HTTPException(status_code=422, detail="Kein Wettkampfziel gesetzt — erst POST /athlete/goal.")
    with _gen_lock:
        if _gen_status.get(user) == "running":
            return {"ok": True, "status": "running"}
        _gen_status[user] = "running"
    threading.Thread(target=_run_generation, args=(user,), daemon=True).start()
    return {"ok": True, "status": "running"}


@router.get("/plan/status")
def plan_status(user: str = Depends(current_user)) -> dict:
    with _gen_lock:
        return {"status": _gen_status.get(user)}
