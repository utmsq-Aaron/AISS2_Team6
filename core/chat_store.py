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

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.llm import _env

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "chats"

_TITLE_MAX = 60
_lock = threading.Lock()


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
    # chat_id must be a bare hex token — reject anything that could escape the dir.
    if not re.fullmatch(r"[a-f0-9]{6,40}", chat_id or ""):
        return None
    return _user_dir(user) / f"{chat_id}.json"


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, chat: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")


def _summary(chat: dict) -> dict:
    return {
        "id": chat.get("id"),
        "title": chat.get("title") or "New chat",
        "created_at": chat.get("created_at"),
        "updated_at": chat.get("updated_at"),
        "message_count": len(chat.get("messages") or []),
    }


def list_chats(user: str) -> List[dict]:
    """Chat summaries for a user, newest-updated first."""
    d = _user_dir(user)
    if not d.exists():
        return []
    chats = [c for c in (_read(p) for p in d.glob("*.json")) if c]
    chats.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
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
                   trace: Optional[dict] = None) -> Optional[dict]:
    """Append one message; auto-title from the first user message. Returns summary."""
    path = _chat_path(user, chat_id)
    if path is None:
        return None
    with _lock:
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
        chat["updated_at"] = _now()
        _write(path, chat)
        return _summary(chat)


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
