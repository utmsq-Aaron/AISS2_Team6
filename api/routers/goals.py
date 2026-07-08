"""Structured training goal — the anchor of the goal-oriented dashboard.

The user's single active goal (``core.goal_store``). Authored here via the Settings
form (source="form"); the coach writes the same file in chat (source="coach"). The
progress endpoint derives the current value from live Strava/Garmin data through the
shared ToolHost and degrades to ``{"status": "unknown"}`` when that data is missing.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import current_user
from api.deps import get_host, orchestrator_lock
from core import goal_store

router = APIRouter(prefix="/goals", tags=["goals"])


class GoalBody(BaseModel):
    title: Optional[str] = None
    metric: Optional[str] = None
    target: Optional[float] = None
    unit: Optional[str] = None
    direction: Optional[str] = None
    deadline: Optional[str] = None
    baseline: Optional[float] = None
    why: Optional[str] = None
    status: Optional[str] = None


@router.get("")
def get_goal(user: str = Depends(current_user)) -> dict:
    """The user's active goal, or {} when none is set."""
    return goal_store.read(user) or {}


@router.put("")
def put_goal(body: GoalBody, user: str = Depends(current_user)) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return goal_store.upsert(user, source="form", **fields) or {}


@router.delete("")
def delete_goal(user: str = Depends(current_user)) -> dict:
    return {"ok": goal_store.delete(user)}


@router.get("/progress")
def goal_progress(user: str = Depends(current_user)) -> dict:
    """Measured progress toward the goal (best-effort; drives the dashboard ring)."""
    goal = goal_store.read(user)
    if not goal:
        return {"status": "no_goal"}
    with orchestrator_lock:  # ToolHost isn't thread-safe; serialize like /api/chat
        return goal_store.goal_progress(user, goal, host=get_host())
