"""Per-user profile — the name the coach calls you, an optional photo, and the
first-login onboarding flag.

Stored at ``data/user_memory/<slug>/profile.json`` (beside ``soul.md``/``goals.json``,
already gitignored via ``data/user_memory/``), written by FastAPI and read by the
coach's context assembly (``core.user_memory.context_block``, which runs in every
process that calls ``orchestrator.run`` — web, Telegram, and composed wake-ups). An
optional avatar image lives alongside it as ``avatar.<ext>``.

Everything is best-effort JSON via ``core.jsonstore`` (cross-process flock + atomic
write) — this file has exactly one writer (FastAPI) today, but the lock costs
nothing and keeps the pattern consistent with the other multi-writer stores.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from core import jsonstore
from core.llm import _env

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "user_memory"

_DEFAULTS: Dict[str, Any] = {
    "name": "",
    "onboarding_complete": False,
    "avatar_ext": None,
}
_UPDATE_ALLOWED = {"name", "onboarding_complete"}
_AVATAR_EXTS = {"jpg", "jpeg", "png", "webp"}


def _root() -> Path:
    raw = _env("USER_MEMORY_DIR", "")
    return Path(raw) if raw else _DEFAULT_DIR


def _profile_path(user: str) -> Path:
    return _root() / jsonstore.slugify(user) / "profile.json"


def get_profile(user: str) -> Dict[str, Any]:
    """The user's profile, defaults filled in. Never raises."""
    with jsonstore.flock(_profile_path(user)):
        doc = jsonstore.read_json(_profile_path(user))
    profile = dict(_DEFAULTS)
    if isinstance(doc, dict):
        profile.update({k: v for k, v in doc.items() if k in profile})
    return profile


def update_profile(user: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Merge whitelisted fields (name, onboarding_complete) into the profile."""
    if not user:
        return None
    path = _profile_path(user)
    with jsonstore.flock(path):
        doc = jsonstore.read_json(path)
        profile = dict(_DEFAULTS)
        if isinstance(doc, dict):
            profile.update({k: v for k, v in doc.items() if k in profile})
        for k, v in fields.items():
            if k not in _UPDATE_ALLOWED or v is None:
                continue
            if k == "name":
                v = str(v).strip()[:100]
            profile[k] = v
        try:
            jsonstore.atomic_write(path, profile)
        except OSError:
            return None
        return profile


def display_name(user: str) -> str:
    """The user's chosen name, or "" if none set. Best-effort — never raises."""
    try:
        return (get_profile(user).get("name") or "").strip()
    except Exception:  # noqa: BLE001 — profile is best-effort
        return ""


def avatar_path(user: str) -> Optional[Path]:
    """The path to the user's avatar file, if one exists on disk."""
    profile = get_profile(user)
    ext = profile.get("avatar_ext")
    if not ext:
        return None
    p = _root() / jsonstore.slugify(user) / f"avatar.{ext}"
    return p if p.exists() else None


def set_avatar(user: str, data: bytes, ext: str) -> bool:
    """Write the avatar file and record its extension. Cleans up a prior avatar
    with a different extension. Returns True on success."""
    ext = (ext or "").lower().lstrip(".")
    if ext not in _AVATAR_EXTS or not user:
        return False
    user_dir = _root() / jsonstore.slugify(user)
    path = _profile_path(user)
    with jsonstore.flock(path):
        doc = jsonstore.read_json(path)
        profile = dict(_DEFAULTS)
        if isinstance(doc, dict):
            profile.update({k: v for k, v in doc.items() if k in profile})
        old_ext = profile.get("avatar_ext")
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
            (user_dir / f"avatar.{ext}").write_bytes(data)
            if old_ext and old_ext != ext:
                old_path = user_dir / f"avatar.{old_ext}"
                if old_path.exists():
                    old_path.unlink()
            profile["avatar_ext"] = ext
            jsonstore.atomic_write(path, profile)
            return True
        except OSError:
            return False
