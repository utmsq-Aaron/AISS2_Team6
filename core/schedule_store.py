"""Durable schedule store for proactive coach wake-ups (cross-chat, dedup'd).

Two small JSON files under ``data/`` (best-effort, one module lock):

  data/schedules.json        pending wake-ups, keyed by email → reason_key → entry
  data/schedule_fired.json   a flat fired-log: token → fired_at  (fire-ONCE guard)

The **poll loop lives in the Telegram bridge** (the only durable process); it calls
``due(now)`` each tick, delivers, then ``mark_fired(...)``. Correctness rests on:

  * **write-time dedup** — an entry is keyed by ``reason_key``; scheduling the same
    reason again *replaces* it (never stacks). So "same reason" can't double-fire.
  * **fire-once** — a fired-log token ``sha1(email|reason_key|fire-minute)`` records
    that a specific fire instant happened, so a second poll tick in the same minute,
    or a bridge restart mid-fire, never re-delivers it.
  * **recurrence** — a ``{"every_days": N}`` entry re-arms itself in ``mark_fired`` at
    the same local wall-clock time (DST-correct), so cadence check-ins keep going.

All instants are stored/compared in UTC; ``Europe/Berlin`` is used only to interpret
bare wall-clock strings and to render ``fire_at_local`` for display.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001 — zoneinfo/tzdata missing → fall back to UTC
    _BERLIN = timezone.utc

_ROOT = Path(__file__).resolve().parent.parent
_SCHED = _ROOT / "data" / "schedules.json"
_FIRED = _ROOT / "data" / "schedule_fired.json"

_lock = threading.Lock()

STALE_MINUTES = 120        # entries older than this are "missed while down" (see bridge policy)
_FIRED_MAX_AGE_DAYS = 14   # prune fired-log tokens older than this


# ── time helpers ──────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def to_utc(iso: str) -> Optional[datetime]:
    """Parse an ISO instant → aware UTC datetime. A bare 'YYYY-MM-DDTHH:MM[:SS]'
    (no offset) is interpreted as Europe/Berlin wall-clock."""
    s = (iso or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_BERLIN)
    return dt.astimezone(timezone.utc)


def to_local_iso(iso: str) -> Optional[str]:
    dt = to_utc(iso)
    return dt.astimezone(_BERLIN).isoformat() if dt else None


def _slot(dt_utc: datetime) -> str:
    return dt_utc.strftime("%Y-%m-%dT%H:%M")


def _token(email: str, reason_key: str, dt_utc: datetime) -> str:
    return hashlib.sha1(f"{email}|{reason_key}|{_slot(dt_utc)}".encode()).hexdigest()


def _next_recurrence(fa_utc: datetime, every_days: int,
                     now: Optional[datetime] = None) -> str:
    """Next FUTURE fire instant, preserving the local wall-clock time (DST-correct).

    Catches up past ``now`` in whole intervals, so a recurring wake-up missed during
    downtime fires once and re-arms *ahead* — not once per poll tick walking forward
    one interval at a time.
    """
    now = now or datetime.now(timezone.utc)
    step = max(1, int(every_days))
    local = fa_utc.astimezone(_BERLIN)
    nxt = local
    for _ in range(4000):  # bound the catch-up loop (≥10y of daily cadence)
        nxt = (nxt + timedelta(days=step)).replace(
            hour=local.hour, minute=local.minute, second=0, microsecond=0)
        if nxt.astimezone(timezone.utc) > now:
            break
    return nxt.astimezone(timezone.utc).isoformat()


# ── raw file I/O (callers hold _lock) ─────────────────────────────────────────

def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[schedule_store] write skipped ({path.name}): {exc}", flush=True)


# ── public API ────────────────────────────────────────────────────────────────

def upsert(email: str, reason_key: str, fire_at_iso: str, note: str,
           kind: str = "wakeup", source: str = "coach",
           recurrence: Optional[dict] = None) -> Optional[dict]:
    """Create/replace the wake-up for (email, reason_key). Returns the entry."""
    if not email or not reason_key:
        return None
    fa = to_utc(fire_at_iso)
    if fa is None:
        return None
    with _lock:
        data = _load(_SCHED)
        entries = data.setdefault(email, {})
        e = entries.get(reason_key) or {"id": uuid.uuid4().hex[:12], "created_at": _now_iso()}
        e.update({
            "reason_key": reason_key,
            "fire_at": fa.isoformat(),
            "note": note or "",
            "kind": kind,
            "source": source,
            "recurrence": recurrence,
            "updated_at": _now_iso(),
        })
        entries[reason_key] = e
        _save(_SCHED, data)
        return dict(e)


def list_for(email: str) -> List[dict]:
    with _lock:
        entries = _load(_SCHED).get(email, {})
    out = []
    for e in entries.values():
        d = dict(e)
        d["fire_at_local"] = to_local_iso(e.get("fire_at", ""))
        out.append(d)
    out.sort(key=lambda x: x.get("fire_at") or "")
    return out


def list_all() -> Dict[str, List[dict]]:
    with _lock:
        data = _load(_SCHED)
    return {email: list(entries.values()) for email, entries in data.items()}


def cancel(email: str, reason_key: str) -> bool:
    with _lock:
        data = _load(_SCHED)
        entries = data.get(email, {})
        if reason_key in entries:
            del entries[reason_key]
            data[email] = entries
            _save(_SCHED, data)
            return True
    return False


def due(now_utc: Optional[datetime] = None) -> List[Tuple[str, dict]]:
    """Every entry whose fire_at ≤ now and not already fired, as (email, entry)."""
    now = now_utc or _now()
    with _lock:
        data = _load(_SCHED)
        fired = _load(_FIRED)
    out: List[Tuple[str, dict]] = []
    for email, entries in data.items():
        for e in entries.values():
            fa = to_utc(e.get("fire_at", ""))
            if fa is None or fa > now:
                continue
            if _token(email, e.get("reason_key", ""), fa) not in fired:
                out.append((email, dict(e)))
    return out


def is_stale(entry: dict, now_utc: Optional[datetime] = None) -> bool:
    """True if the entry's fire_at is more than STALE_MINUTES in the past."""
    now = now_utc or _now()
    fa = to_utc(entry.get("fire_at", ""))
    return fa is not None and (now - fa) > timedelta(minutes=STALE_MINUTES)


def mark_fired(email: str, entry: dict, now_utc: Optional[datetime] = None) -> None:
    """Record that this entry fired (once), then re-arm (recurring) or delete it."""
    now = now_utc or _now()
    fa = to_utc(entry.get("fire_at", "")) or now
    reason_key = entry.get("reason_key", "")
    with _lock:
        fired = _load(_FIRED)
        fired[_token(email, reason_key, fa)] = now.isoformat()
        _prune_fired(fired, now)
        _save(_FIRED, fired)

        data = _load(_SCHED)
        entries = data.get(email, {})
        rec = entry.get("recurrence")
        if rec and rec.get("every_days") and reason_key in entries:
            entries[reason_key]["fire_at"] = _next_recurrence(fa, rec["every_days"], now)
            entries[reason_key]["updated_at"] = now.isoformat()
            data[email] = entries
            _save(_SCHED, data)
        elif reason_key in entries:
            del entries[reason_key]
            data[email] = entries
            _save(_SCHED, data)


def _prune_fired(fired: dict, now: datetime) -> None:
    cutoff = now - timedelta(days=_FIRED_MAX_AGE_DAYS)
    stale = []
    for tok, ts in fired.items():
        try:
            if datetime.fromisoformat(ts) < cutoff:
                stale.append(tok)
        except (ValueError, TypeError):
            stale.append(tok)
    for tok in stale:
        fired.pop(tok, None)
