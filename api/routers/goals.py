"""Multiple, freeform training goals — each with an agent-authored dashboard panel.

A goal is just text (``core.goal_store``), authored here via a single text box
(``source="user"``); the coach writes the same store in chat (``source="coach"``).
Creating or materially editing a goal's text ENQUEUES a background panel build
(``core.goal_build_queue``) — drained by the Telegram bridge, since FastAPI runs
under ``--reload`` and cannot host a durable build thread itself. The frontend
polls ``GET /api/goals`` and watches each goal's ``panel_status`` (empty → building
→ ready | error) to know when a panel is ready.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import current_user
from core import goal_build_queue, goal_store

router = APIRouter(prefix="/goals", tags=["goals"])


class GoalEventBody(BaseModel):
    """One optional structured target event for a goal (issue #25) — all fields
    optional; normalized server-side by ``goal_store._normalize_event``."""
    date: Optional[str] = None
    name: Optional[str] = None
    distance_km: Optional[float] = None
    sport: Optional[str] = None
    elevation_gain_m: Optional[float] = None


class AddGoalBody(BaseModel):
    text: str
    sport: Optional[str] = None
    event: Optional[GoalEventBody] = None


class UpdateGoalBody(BaseModel):
    text: Optional[str] = None
    sport: Optional[str] = None
    status: Optional[str] = None
    event: Optional[GoalEventBody] = None


@router.get("")
def list_goals(user: str = Depends(current_user)) -> dict:
    """All of the user's goals, any status — never 404s (empty list for a new user)."""
    return {"goals": goal_store.list_goals(user)}


@router.post("")
def add_goal(body: AddGoalBody, user: str = Depends(current_user)) -> dict:
    """Create a goal from freeform text; its panel builds in the background."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be empty")
    event = body.event.model_dump() if body.event else None
    goal = goal_store.add_goal(user, text, sport=body.sport, source="user", event=event)
    if not goal:
        raise HTTPException(status_code=500, detail="could not save goal")
    goal_build_queue.enqueue(user, goal["id"])
    goal = goal_store.set_panel_status(user, goal["id"], "building") or goal
    return goal


@router.patch("/{goal_id}")
def update_goal(goal_id: str, body: UpdateGoalBody, user: str = Depends(current_user)) -> dict:
    before = goal_store.get_goal(user, goal_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    goal = goal_store.update_goal(user, goal_id, **fields)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    # A materially different text makes the existing panel stale — rebuild it.
    new_text = fields.get("text")
    if new_text is not None and new_text.strip() != (before.get("text") or "").strip():
        goal_store.set_panel_status(user, goal_id, "building")
        goal_build_queue.enqueue(user, goal_id)
        goal = goal_store.get_goal(user, goal_id) or goal
    return goal


@router.delete("/{goal_id}")
def delete_goal(goal_id: str, user: str = Depends(current_user)) -> dict:
    return {"ok": goal_store.delete_goal(user, goal_id)}


@router.post("/{goal_id}/refresh")
def refresh_goal_panel(goal_id: str, user: str = Depends(current_user)) -> dict:
    """Rebuild this goal's dashboard panel from fresh data (on-demand refresh)."""
    goal = goal_store.get_goal(user, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    goal_store.set_panel_status(user, goal_id, "building")
    goal_build_queue.enqueue(user, goal_id)
    return {"ok": True}
