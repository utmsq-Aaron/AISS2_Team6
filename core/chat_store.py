"""Per-user persistent chat sessions on disk (survive server restarts).

Each user gets a directory of chat files:

    data/chats/<user-slug>/<chat-id>.json
        { "id", "title", "created_at", "updated_at",
          "messages": [ {"role", "content", "ts", "trace"?}, … ] }

One file per chat keeps writes cheap and avoids rewriting a giant blob on every
turn. Everything is best-effort and JSON; the API layer (api/routers/chats.py)
exposes CRUD, and the chat SSE endpoint appends each completed turn here so the UI
can reload the full history after a restart.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fcntl  # Unix (macOS/Linux) — cross-process advisory file lock
except ImportError:  # pragma: no cover — non-Unix falls back to threading only
    fcntl = None  # type: ignore

from core.llm import _env

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "chats"

_TITLE_MAX = 60
_lock = threading.Lock()

# A single reserved, non-hex chat id per user: the pinned "Coach" chat that mirrors
# the Telegram DM and receives proactive/deep-analysis deliveries. Exempt from the
# hex-only id gate below (it's a fixed literal, so still traversal-safe).
COACH_CHAT_ID = "coach"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(user: str) -> str:
    """Filesystem-safe per-user dir (also guards against path traversal)."""
    return re.sub(r"[^a-z0-9_-]+", "-", (user or "").strip().lower()).strip("-") or "anon"


def _root() -> Path:
    raw = _env("CHATS_DIR", "")
    return Path(raw) if raw else _DEFAULT_DIR


def _user_dir(user: str) -> Path:
    return _root() / _slug(user)


def _chat_path(user: str, chat_id: str) -> Optional[Path]:
    # chat_id must be a bare hex token (or the reserved COACH_CHAT_ID literal) —
    # reject anything else that could escape the dir.
    if chat_id != COACH_CHAT_ID and not re.fullmatch(r"[a-f0-9]{6,40}", chat_id or ""):
        return None
    return _user_dir(user) / f"{chat_id}.json"


@contextlib.contextmanager
def _flock(path: Path):
    """Best-effort cross-process exclusive lock on one chat file.

    The Coach chat (``coach.json``) is written by TWO processes — FastAPI and the
    Telegram bridge — so the module ``threading.Lock`` (per-process) isn't enough;
    without this a concurrent append + mark_read would clobber each other
    (last-writer-wins). On non-Unix (no ``fcntl``) this is a no-op.
    """
    if fcntl is None:
        yield
        return
    lock_path = path.with_suffix(path.suffix + ".lock")
    fh = None
    try:  # acquire — any failure degrades to the threading lock alone
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        if fh is not None:
            fh.close()
            fh = None
    try:  # run the guarded body exactly once; release in finally
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, chat: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so a concurrent reader (list_chats/get_chat don't take the lock)
    # never sees a half-written file — a torn read would parse as None and trigger
    # the append_message recreate branch, silently dropping the chat's history.
    tmp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)  # os.replace — atomic on POSIX
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _summary(chat: dict) -> dict:
    s = {
        "id": chat.get("id"),
        "title": chat.get("title") or "New chat",
        "created_at": chat.get("created_at"),
        "updated_at": chat.get("updated_at"),
        "message_count": len(chat.get("messages") or []),
    }
    # Optional metadata for special chats (the pinned Coach chat). Absent on
    # normal chats so the payload stays unchanged for them.
    if chat.get("pinned"):
        s["pinned"] = True
    if chat.get("special"):
        s["special"] = chat["special"]
    if chat.get("unread"):
        s["unread"] = int(chat["unread"])
    return s


def summarize(chat: dict) -> dict:
    """Public summary of a chat record (id/title/counts + special-chat flags)."""
    return _summary(chat)


def list_chats(user: str) -> List[dict]:
    """Chat summaries for a user: pinned chats first, then newest-updated first."""
    d = _user_dir(user)
    if not d.exists():
        return []
    chats = [c for c in (_read(p) for p in d.glob("*.json")) if c]
    # Stable sort applied twice: newest-updated first, then pinned floats to top.
    chats.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    chats.sort(key=lambda c: 0 if c.get("pinned") else 1)
    return [_summary(c) for c in chats]


def create_chat(user: str, title: str = "") -> dict:
    """Create an empty chat and return its full record."""
    with _lock:
        chat = {
            "id": uuid.uuid4().hex[:12],
            "title": (title or "").strip()[:_TITLE_MAX],
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [],
        }
        path = _chat_path(user, chat["id"])
        if path is not None:
            _write(path, chat)
    return chat


def get_chat(user: str, chat_id: str) -> Optional[dict]:
    path = _chat_path(user, chat_id)
    return _read(path) if path else None


def history_messages(user: str, chat_id: str) -> List[Dict[str, str]]:
    """Prior turns as [{role, content}] for feeding the agent (no traces)."""
    chat = get_chat(user, chat_id)
    if not chat:
        return []
    return [{"role": m["role"], "content": m.get("content") or ""}
            for m in chat.get("messages", []) if m.get("role") in ("user", "assistant")]


def append_message(user: str, chat_id: str, role: str, content: str,
                   trace: Optional[dict] = None, bump_unread: bool = False) -> Optional[dict]:
    """Append one message; auto-title from the first user message. Returns summary.

    ``bump_unread`` increments the chat's unread counter (used by the Coach chat
    when a proactive/deep message the user hasn't seen in the web UI is delivered).
    """
    path = _chat_path(user, chat_id)
    if path is None:
        return None
    with _lock, _flock(path):
        chat = _read(path)
        if chat is None:  # gone / never created → recreate under this id
            chat = {"id": chat_id, "title": "", "created_at": _now(),
                    "updated_at": _now(), "messages": []}
        msg: Dict[str, Any] = {"role": role, "content": content, "ts": _now()}
        if trace is not None:
            msg["trace"] = trace
        chat.setdefault("messages", []).append(msg)
        if not chat.get("title") and role == "user" and content.strip():
            chat["title"] = content.strip()[:_TITLE_MAX]
        if bump_unread:
            chat["unread"] = int(chat.get("unread") or 0) + 1
        chat["updated_at"] = _now()
        _write(path, chat)
        return _summary(chat)


def ensure_special_chat(user: str, chat_id: str, title: str,
                        special: Optional[str] = None) -> Optional[dict]:
    """Create a pinned special chat (e.g. the Coach chat) if it doesn't exist yet.

    Idempotent: returns the existing summary when already present, preserving its
    messages/unread. The pinned/special/title metadata makes it sort first
    (``list_chats``) and lets the UI mark it.
    """
    path = _chat_path(user, chat_id)
    if path is None:
        return None
    with _lock, _flock(path):
        chat = _read(path)
        if chat is None:
            chat = {
                "id": chat_id,
                "title": (title or "").strip()[:_TITLE_MAX] or chat_id,
                "created_at": _now(),
                "updated_at": _now(),
                "messages": [],
                "pinned": True,
                "special": special or chat_id,
            }
            _write(path, chat)
        return _summary(chat)


def mark_read(user: str, chat_id: str) -> bool:
    """Clear a chat's unread counter. Returns True if the chat exists."""
    path = _chat_path(user, chat_id)
    if path is None:
        return False
    with _lock, _flock(path):
        chat = _read(path)
        if chat is None:
            return False
        if chat.get("unread"):
            chat["unread"] = 0
            _write(path, chat)
        return True


def rename_chat(user: str, chat_id: str, title: str) -> bool:
    path = _chat_path(user, chat_id)
    if path is None:
        return False
    with _lock:
        chat = _read(path)
        if chat is None:
            return False
        chat["title"] = (title or "").strip()[:_TITLE_MAX] or chat.get("title") or "New chat"
        chat["updated_at"] = _now()
        _write(path, chat)
        return True


def delete_chat(user: str, chat_id: str) -> bool:
    path = _chat_path(user, chat_id)
    if path is None or not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False
