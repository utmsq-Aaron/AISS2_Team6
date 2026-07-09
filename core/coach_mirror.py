"""The pinned "Coach" chat — the web-visible surface for the coach.

One special chat per user (id ``chat_store.COACH_CHAT_ID``) that:
  * mirrors the user's Telegram DM (the bridge appends every inbound + outbound
    turn here, so the conversation shows up in the web chat list), and
  * receives proactive check-ins and finished deep-analysis reports
    (``core.delivery`` appends them here).

It is always pinned to the top of the chat list and specially marked in the UI.
When the user has no Telegram link, this same chat still exists web-only as the
place proactive messages land. Everything is best-effort — a mirror failure must
never break a Telegram reply or a scheduled delivery.
"""

from __future__ import annotations

from typing import Optional

from core import chat_store

COACH_CHAT_ID = chat_store.COACH_CHAT_ID
_TITLE = "Coach"


def get_or_create(user: str) -> Optional[dict]:
    """Ensure the user's Coach chat exists (pinned + marked). Returns its summary."""
    return chat_store.ensure_special_chat(user, COACH_CHAT_ID, _TITLE, special="coach")


def append(user: str, role: str, content: str,
           trace: Optional[dict] = None, bump_unread: Optional[bool] = None) -> Optional[dict]:
    """Append one turn to the user's Coach chat. Best-effort (never raises).

    ``bump_unread`` defaults to True for assistant/coach messages (the user hasn't
    seen them in the web UI yet) and False for the user's own mirrored messages.
    """
    if not user:
        return None
    if bump_unread is None:
        bump_unread = role == "assistant"
    try:
        get_or_create(user)
        return chat_store.append_message(user, COACH_CHAT_ID, role, content,
                                         trace=trace, bump_unread=bump_unread)
    except Exception as exc:  # noqa: BLE001 — mirroring must never break delivery
        print(f"[coach_mirror] append skipped for {user}: {exc}", flush=True)
        return None


def mark_read(user: str) -> bool:
    """Clear the Coach chat's unread badge."""
    try:
        return chat_store.mark_read(user, COACH_CHAT_ID)
    except Exception:  # noqa: BLE001
        return False
