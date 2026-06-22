"""Persistent Telegram-user → account link.

Maps a Telegram numeric user id to the FitDash account (email) it logged in as,
in ``data/telegram_links.json``. Once a Telegram user completes the email+OTP
login (handled by ``telegram_bridge.py``), the link is saved here so they never
have to log in again across restarts — until they ``/logout`` (which unlinks).

The link is identity only: the agent then runs as that email, so the Telegram
user gets the same Strava/Garmin connections and the same per-user agent memory
as the web account.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_FILE = _ROOT / "data" / "telegram_links.json"
_lock = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def get_email(telegram_id: int) -> Optional[str]:
    """The account linked to this Telegram user, or None if not logged in."""
    rec = _load().get(str(telegram_id))
    return rec.get("email") if isinstance(rec, dict) else None


def is_linked(telegram_id: int) -> bool:
    return get_email(telegram_id) is not None


def link(telegram_id: int, email: str) -> None:
    with _lock:
        d = _load()
        d[str(telegram_id)] = {
            "email": email,
            "linked_at": datetime.now(timezone.utc).isoformat(),
        }
        _save(d)


def unlink(telegram_id: int) -> bool:
    """Remove the link; True if there was one."""
    with _lock:
        d = _load()
        existed = d.pop(str(telegram_id), None) is not None
        if existed:
            _save(d)
        return existed
