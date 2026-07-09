"""User profile — the name/photo the coach uses, and the onboarding flag.

Backs the first-login wizard (web/src/components/onboarding) and the header's
avatar/name display. All routes are scoped to the authenticated user.
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from api.auth import current_user
from core import user_profile

router = APIRouter(prefix="/profile", tags=["profile"])

_MAX_AVATAR_BYTES = 3 * 1024 * 1024  # 3 MB
_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    onboarding_complete: Optional[bool] = None


def _public(profile: dict) -> dict:
    return {
        "name": profile.get("name") or "",
        "onboarding_complete": bool(profile.get("onboarding_complete")),
        "has_avatar": bool(profile.get("avatar_ext")),
    }


@router.get("")
def get_profile(user: str = Depends(current_user)) -> dict:
    return _public(user_profile.get_profile(user))


@router.put("")
def put_profile(body: ProfileUpdate, user: str = Depends(current_user)) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    profile = user_profile.update_profile(user, **fields)
    if profile is None:
        raise HTTPException(status_code=500, detail="could not save profile")
    return _public(profile)


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...), user: str = Depends(current_user)) -> dict:
    ext = _CONTENT_TYPES.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(status_code=422, detail="unsupported image type (use jpeg/png/webp)")
    data = await file.read(_MAX_AVATAR_BYTES + 1)
    if len(data) > _MAX_AVATAR_BYTES:
        raise HTTPException(status_code=422, detail="image too large (max 3 MB)")
    if not user_profile.set_avatar(user, data, ext):
        raise HTTPException(status_code=500, detail="could not save avatar")
    return _public(user_profile.get_profile(user))


@router.get("/avatar")
def get_avatar(user: str = Depends(current_user)) -> Response:
    path = user_profile.avatar_path(user)
    if path is None:
        raise HTTPException(status_code=404, detail="no avatar set")
    media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") \
        else f"image/{path.suffix.lstrip('.').lower()}"
    return Response(content=path.read_bytes(), media_type=media_type)
