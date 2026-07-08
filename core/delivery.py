"""Deliver a proactive message to a user across channels.

Every proactive/deep message goes to ONE place the user can always see — the
pinned **Coach** chat in the web UI (``core.coach_mirror``) — and ALSO gets pushed
to the user's **Telegram** DM when their account is linked and a live Telethon
client is available (only the bridge process holds one).

Two entry points so we never deadlock on the event loop:
  * ``deliver_async(...)``  — await from inside the bridge's asyncio loop (the
    normal path: the scheduler poll loop calls this).
  * ``deliver_to_user(...)`` — sync, for worker threads: mirrors to web and, if a
    ``loop`` is handed in, marshals the Telegram send onto it thread-safely.

The web mirror ALWAYS runs first (and never raises), so a Telegram failure or an
unlinked account still surfaces the message in the web Coach chat.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional

from core import coach_mirror, telegram_link


def _notify_sent(on_sent: Optional[Callable], msg: Any) -> None:
    """Hand a sent Telegram Message to the caller (the bridge registers its id in
    its echo-guard so it isn't re-ingested as a new user message). Best-effort."""
    if on_sent is None or msg is None:
        return
    try:
        on_sent(msg)
    except Exception:  # noqa: BLE001
        pass


def mirror_web(email: str, text: str, trace: Optional[dict] = None,
               kind: str = "proactive") -> bool:
    """Append the message to the user's pinned Coach chat (unread bumped)."""
    return coach_mirror.append(email, "assistant", text, trace=trace, bump_unread=True) is not None


async def deliver_async(email: str, text: str, *, trace: Optional[dict] = None,
                        kind: str = "proactive", tg_client: Any = None,
                        on_sent: Optional[Callable] = None) -> Dict[str, bool]:
    """Deliver from inside the bridge event loop. Never raises."""
    delivered = {"web": False, "telegram": False}
    try:
        delivered["web"] = mirror_web(email, text, trace, kind)
    except Exception as exc:  # noqa: BLE001
        print(f"[delivery] web mirror failed for {email}: {exc}", flush=True)

    tid = telegram_link.get_telegram_id(email)
    if tid and tg_client is not None:
        try:
            sent = await tg_client.send_message(tid, text)
            delivered["telegram"] = True
            _notify_sent(on_sent, sent)
        except Exception as exc:  # noqa: BLE001 — push is best-effort
            print(f"[delivery] telegram push failed for {email}: {exc}", flush=True)
    return delivered


def deliver_to_user(email: str, text: str, *, trace: Optional[dict] = None,
                    kind: str = "proactive", tg_client: Any = None,
                    loop: Any = None, on_sent: Optional[Callable] = None) -> Dict[str, bool]:
    """Sync delivery for worker threads. Web mirror always; Telegram push only if a
    linked id, a client, and the bridge ``loop`` are all available (marshaled onto
    that loop). Never raises."""
    delivered = {"web": False, "telegram": False}
    try:
        delivered["web"] = mirror_web(email, text, trace, kind)
    except Exception as exc:  # noqa: BLE001
        print(f"[delivery] web mirror failed for {email}: {exc}", flush=True)

    tid = telegram_link.get_telegram_id(email)
    if tid and tg_client is not None and loop is not None:
        try:
            fut = asyncio.run_coroutine_threadsafe(tg_client.send_message(tid, text), loop)
            sent = fut.result(timeout=30)
            delivered["telegram"] = True
            _notify_sent(on_sent, sent)
        except Exception as exc:  # noqa: BLE001
            print(f"[delivery] telegram push failed for {email}: {exc}", flush=True)
    return delivered
