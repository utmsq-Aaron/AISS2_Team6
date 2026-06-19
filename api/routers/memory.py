"""Per-user memory endpoints — view/edit the soul, inspect recalled conversations.

All routes are scoped to the authenticated user (api/auth.current_user): you can
only ever see or edit your own memory. Chat itself reads/writes memory automatically
(core/orchestrator.run → core/user_memory); these endpoints just make it visible.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import current_user
from core.user_memory import get_user_memory

router = APIRouter(prefix="/memory", tags=["memory"])


class SoulUpdate(BaseModel):
    content: str


@router.get("/soul")
def get_soul(user: str = Depends(current_user)) -> dict[str, str]:
    mem = get_user_memory(user)
    mem.ensure_soul()
    return {"user": user, "content": mem.read_soul()}


@router.put("/soul")
def put_soul(req: SoulUpdate, user: str = Depends(current_user)) -> dict[str, Any]:
    mem = get_user_memory(user)
    mem.write_soul(req.content)
    return {"user": user, "ok": True}


@router.get("/search")
def search(q: str, k: int = 5, user: str = Depends(current_user)) -> dict[str, Any]:
    """Recall the user's most relevant past conversation turns for query ``q``."""
    hits = get_user_memory(user).recall(q, k=k)
    return {"user": user, "query": q, "results": hits}


@router.post("/soul/refresh")
def refresh_soul(user: str = Depends(current_user)) -> dict[str, Any]:
    """Force an immediate LLM soul refresh from recent conversation (ignores the
    every-N-turns throttle). Returns whether the soul actually changed."""
    mem = get_user_memory(user)
    changed = mem.refresh_soul()
    return {"user": user, "updated": changed, "content": mem.read_soul()}
