"""Proactive wake-ups — the user's view of what the coach has scheduled.

The durable poll loop that FIRES these lives in the Telegram bridge (the only
always-on process); this router just lets the user see and cancel pending
wake-ups. Entries are created by the coach (schedule_followup) and the calendar
auto-scheduler; see core.schedule_store.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import current_user
from core import schedule_store

router = APIRouter(prefix="/schedules", tags=["schedules"])


class CancelBody(BaseModel):
    reason_key: str


@router.get("")
def list_schedules(user: str = Depends(current_user)) -> dict:
    """Pending wake-ups (each with a Europe/Berlin ``fire_at_local``)."""
    return {"schedules": schedule_store.list_for(user)}


@router.post("/cancel")
def cancel_schedule(body: CancelBody, user: str = Depends(current_user)) -> dict:
    return {"ok": schedule_store.cancel(user, body.reason_key)}
