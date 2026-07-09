"""The red feedback button — a tester reports a problem; the server snapshots
diagnostics alongside it. See api/feedback_service.py for what's captured.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import feedback_service
from api.auth import current_user, require_admin

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackBody(BaseModel):
    text: str
    context: Optional[Dict[str, Any]] = None


@router.post("")
def submit_feedback(body: FeedbackBody, user: str = Depends(current_user)) -> dict:
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be empty")
    bundle_id = feedback_service.create_bundle(user, text, body.context)
    if bundle_id is None:
        raise HTTPException(status_code=500, detail="could not save feedback")
    feedback_service.notify_admin(bundle_id, user, text)
    return {"ok": True, "bundle_id": bundle_id}


@router.get("")
def list_feedback(_admin: str = Depends(require_admin)) -> dict:
    return {"bundles": feedback_service.list_bundles()}


@router.get("/{bundle_id}")
def get_feedback(bundle_id: str, _admin: str = Depends(require_admin)) -> dict:
    bundle = feedback_service.get_bundle(bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="bundle not found")
    return bundle
