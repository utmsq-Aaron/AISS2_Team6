"""Delivery outbox for pre-composed proactive messages (deep-analysis reports).

Some proactive messages are produced OFF the request thread — most importantly a
finished deep-analysis report (``core.deep_analysis``), written from a worker
thread that has no Telethon client and no event loop. It drops the finished
message here; the **Telegram bridge poll loop drains this outbox each tick** and
routes each message through ``core.delivery.deliver_to_user`` (web mirror + a
Telegram push when linked).

    data/proactive/outbox/<slug>/<msg_id>.json
        { msg_id, user, kind, title?, body, trace?, reason_key?,
          created_at }

Contrast with ``core.schedule_store``: those entries are *composed at fire time*
by the poll loop; outbox messages are *already composed* and just need delivery.
A message is deleted once delivered (delivery mirrors to the web chat, so
re-processing would duplicate it). Best-effort throughout.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
_OUTBOX = _ROOT / "data" / "proactive" / "outbox"


def _slug(user: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (user or "").strip().lower()).strip("-") or "anon"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(user: str, body: str, *, title: Optional[str] = None,
            trace: Optional[dict] = None, kind: str = "deep_report",
            reason_key: Optional[str] = None) -> Optional[str]:
    """Queue a pre-composed message for delivery. Returns the msg_id (or None)."""
    if not user or not (body or "").strip():
        return None
    msg_id = uuid.uuid4().hex[:12]
    rec: Dict[str, Any] = {
        "msg_id": msg_id,
        "user": user,
        "kind": kind,
        "title": title,
        "body": body,
        "trace": trace,
        "reason_key": reason_key,
        "created_at": _now(),
    }
    try:
        d = _OUTBOX / _slug(user)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{msg_id}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return msg_id
    except OSError as exc:
        print(f"[proactive_outbox] enqueue skipped for {_slug(user)}: {exc}", flush=True)
        return None


def pending() -> List[Tuple[Path, Dict[str, Any]]]:
    """All undelivered messages across users, as (path, record). Best-effort."""
    out: List[Tuple[Path, Dict[str, Any]]] = []
    if not _OUTBOX.exists():
        return out
    for p in sorted(_OUTBOX.glob("*/*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rec, dict):
                out.append((p, rec))
        except (OSError, ValueError):
            continue
    return out


def remove(path: Path) -> None:
    """Delete a delivered message file (best-effort)."""
    try:
        Path(path).unlink()
    except OSError:
        pass
