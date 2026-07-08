"""Per-user structured training goal — the thing the coach steers toward.

One active goal per user at ``data/user_memory/<slug>/goal.json`` (beside the
soul), authored by BOTH the Settings form (source="form") and the coach in chat
(source="coach"; ``upsert_goal`` tool). It is:

  * injected into every turn as a directive (``core.user_memory.goal_block``), and
  * the anchor of the goal-oriented dashboard, which shows measurable progress
    toward it (``goal_progress`` derives the current value from live Strava/Garmin
    data via the shared ToolHost).

Everything is best-effort JSON with a per-user lock, mirroring ``core.user_memory``.

Goal schema (single object; only ``title`` is required to create one):
    { id, title, why?, metric, target, unit, direction,
      deadline (ISO date | null), baseline?, status, source,
      created_at, updated_at }
where ``metric`` ∈ {weekly_distance_km, total_distance_km, 5k_time, bodyweight_kg}
drives progress computation (5k_time stored as seconds; displayed mm:ss by the UI).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.llm import _env

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "user_memory"

METRICS = ("weekly_distance_km", "total_distance_km", "5k_time", "bodyweight_kg")

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(user: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (user or "").strip().lower()).strip("-") or "anon"


def _root() -> Path:
    raw = _env("USER_MEMORY_DIR", "")
    return Path(raw) if raw else _DEFAULT_DIR


def _goal_path(user: str) -> Path:
    return _root() / _slug(user) / "goal.json"


def _lock_for(user: str) -> threading.Lock:
    slug = _slug(user)
    with _locks_guard:
        lock = _locks.get(slug)
        if lock is None:
            lock = _locks[slug] = threading.Lock()
        return lock


def read(user: str) -> Optional[Dict[str, Any]]:
    """The user's active goal, or None. Best-effort (never raises)."""
    try:
        p = _goal_path(user)
        if not p.exists():
            return None
        goal = json.loads(p.read_text(encoding="utf-8"))
        return goal if isinstance(goal, dict) and goal.get("status", "active") != "deleted" else None
    except (OSError, ValueError):
        return None


_ALLOWED = {"title", "why", "metric", "target", "unit", "direction",
            "deadline", "baseline", "status"}


def upsert(user: str, source: str = "form", **fields: Any) -> Optional[Dict[str, Any]]:
    """Create or update the goal, merging only non-None allowed fields. Returns it."""
    if not user:
        return None
    lock = _lock_for(user)
    with lock:
        goal = read(user) or {
            "id": uuid.uuid4().hex[:12],
            "status": "active",
            "created_at": _now(),
        }
        for k, v in fields.items():
            if k in _ALLOWED and v is not None:
                goal[k] = v
        goal.setdefault("status", "active")
        goal["source"] = source
        goal["updated_at"] = _now()
        try:
            p = _goal_path(user)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(goal, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"[goal_store] write skipped for {_slug(user)}: {exc}", flush=True)
            return None
        return goal


def delete(user: str) -> bool:
    try:
        p = _goal_path(user)
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False


# ── Progress computation ──────────────────────────────────────────────────────

def _call_json(host, tool: str, args: Optional[dict] = None) -> Optional[dict]:
    """Call an MCP tool via the ToolHost, return the parsed dict or None on error."""
    try:
        raw = host.call_tool(tool, args or {})
        data = json.loads(raw)
        if isinstance(data, dict) and "error" in data:
            return None
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — progress is best-effort
        return None


def _current_value(user: str, metric: str, host) -> Optional[float]:
    if metric == "weekly_distance_km":
        d = _call_json(host, "strava__get_training_trends", {"weeks": 4})
        v = (d or {}).get("summary", {}).get("avg_distance_per_active_week_km")
        return float(v) if v is not None else None
    if metric == "total_distance_km":
        d = _call_json(host, "strava__get_activity_stats")
        v = (d or {}).get("total_distance_km")
        return float(v) if v is not None else None
    if metric == "bodyweight_kg":
        d = _call_json(host, "garmin__get_garmin_body_composition")
        latest = (d or {}).get("latest") or {}
        v = latest.get("weight_kg")
        return float(v) if v is not None else None
    if metric == "5k_time":
        # Garmin's race predictor gives a current 5k estimate ("mm:ss"/"h:mm:ss");
        # a genuine current-fitness signal (strava PBs aren't per-distance/time-sorted).
        d = _call_json(host, "garmin__get_garmin_training_metrics")
        pred = ((d or {}).get("race_predictions") or {}).get("5k")
        return _hms_to_seconds(pred)
    return None


def _hms_to_seconds(s: Any) -> Optional[float]:
    """Parse 'mm:ss' or 'h:mm:ss' → seconds; None on anything else."""
    if not isinstance(s, str) or ":" not in s:
        return None
    try:
        parts = [int(p) for p in s.strip().split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    if len(parts) == 3:
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    return None


def _pct(current: float, target: float, baseline: Optional[float], direction: str) -> Optional[float]:
    try:
        if baseline is not None and target != baseline:
            p = (current - baseline) / (target - baseline) * 100
        elif direction == "decrease":
            p = (target / current) * 100 if current else None
        else:  # increase / maintain
            p = (current / target) * 100 if target else None
        return None if p is None else max(0.0, round(p, 1))
    except (TypeError, ZeroDivisionError):
        return None


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a datetime to aware UTC (a naive value is assumed UTC)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _time_pct(created_at: Optional[str], deadline: Optional[str]) -> Optional[float]:
    """Fraction of the goal window elapsed (0..100), or None if undatable.

    All three instants are coerced to aware UTC first: created_at is stored tz-aware
    but a date-only deadline parses naive, and mixing the two raised a swallowed
    TypeError that silently killed deadline-aware pacing.
    """
    if not deadline:
        return None
    try:
        start = _as_utc(datetime.fromisoformat(created_at)) if created_at else None
        raw_end = datetime.fromisoformat(deadline) if len(deadline) > 10 else \
            datetime.fromisoformat(deadline + "T23:59:59")
        end = _as_utc(raw_end)
        now = datetime.now(timezone.utc)
        if start is None or end <= start:
            return None
        return max(0.0, min(100.0, (now - start).total_seconds()
                            / (end - start).total_seconds() * 100))
    except (ValueError, TypeError):
        return None


def goal_progress(user: str, goal: Optional[dict] = None, host=None) -> Dict[str, Any]:
    """Progress toward the goal from live data. Degrades to status='unknown'."""
    goal = goal or read(user)
    if not goal:
        return {"status": "no_goal"}
    if host is None:
        from core.host import default_host
        host = default_host

    metric = goal.get("metric")
    target = goal.get("target")
    unit = goal.get("unit")
    direction = goal.get("direction") or ("decrease" if metric in ("5k_time", "bodyweight_kg") else "increase")
    baseline = goal.get("baseline")

    if metric not in METRICS or target is None:
        return {"status": "unknown", "target": target, "unit": unit}

    current = _current_value(user, metric, host)
    if current is None:
        return {"status": "unknown", "target": target, "unit": unit}

    try:
        target = float(target)
    except (TypeError, ValueError):
        return {"status": "unknown", "target": goal.get("target"), "unit": unit}

    pct = _pct(current, target, baseline, direction)
    delta_needed = round(target - current, 2)
    reached = (current >= target) if direction != "decrease" else (current <= target)

    # Classify against elapsed time when we can; otherwise a coarse pct threshold.
    tpct = _time_pct(goal.get("created_at"), goal.get("deadline"))
    if reached:
        status = "reached"
        on_track = True
    elif pct is None:
        status = "unknown"
        on_track = False
    elif tpct is not None:
        on_track = pct >= tpct - 10
        status = "on_track" if on_track else ("at_risk" if pct >= tpct - 25 else "behind")
    else:
        on_track = pct >= 50
        status = "on_track" if on_track else "behind"

    return {
        "status": status,
        "current": round(current, 2),
        "target": round(target, 2),
        "unit": unit,
        "pct": pct,
        "on_track": on_track,
        "delta_needed": delta_needed,
        "direction": direction,
        "computed_at": _now(),
    }
