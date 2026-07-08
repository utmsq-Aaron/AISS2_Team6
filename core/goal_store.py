"""Per-user MULTIPLE, freeform training goals — the things the coach steers toward.

A goal is now just TEXT (sport-specific goals are common — "sub-40 10K by December",
"improve my open-water swim pace"), authored by BOTH the user (a single text box,
``source="user"``) and the coach in chat (``add_goal`` tool, ``source="coach"``).
Multiple goals per user are stored at ``data/user_memory/<slug>/goals.json``:

    { "goals": [
        { id, text, sport?, source, status ("active"|"achieved"|"archived"),
          created_at, updated_at,
          panel: Panel | null,               # agent-authored dashboard content
          panel_status ("empty"|"building"|"ready"|"error"),
          panel_updated_at },
        …
    ] }

Each goal's dashboard PANEL is authored by the agent, not hardcoded — a bounded
background job (``core.goal_panel``) gathers the user's real data and calls the
``set_goal_panel`` tool, which lands here via :func:`set_panel` after passing
through :func:`normalize_panel` (so the coach's inline authoring and the background
builder can never diverge in shape). A Panel is:

    { headline, status ("on_track"|"at_risk"|"behind"|"reached"|"unknown"),
      tiles: [{label, value, sub?}] (2-4), progress: {pct, label} | null,
      note (markdown, free-form), chart: {kind, points:[{x,y}], y_label?} | null,
      generated_at }

``goals.json`` is written by THREE processes (FastAPI, the panel-build worker, the
Telegram bridge's drain/staleness loop), so every mutator goes through
``core.jsonstore`` (cross-process flock + atomic write) — the same discipline
``core.chat_store`` uses for the shared Coach chat.

A legacy single ``goal.json`` (the old structured-metric goal) is migrated in place,
once, the first time this user's goals are read: folded into one text goal and
renamed to ``goal.json.migrated``.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import jsonstore
from core.llm import _env

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "user_memory"

_VALID_GOAL_STATUS = {"active", "achieved", "archived"}
_VALID_PANEL_STATUS = {"empty", "building", "ready", "error"}
_VALID_PANEL_HEALTH = {"on_track", "at_risk", "behind", "reached", "unknown"}
_MAX_TILES = 4
_MAX_CHART_POINTS = 60

# Per-user threading lock (defense-in-depth alongside jsonstore.flock — matters on
# non-Unix where flock is a no-op; mirrors core.chat_store's `with _lock, _flock():`).
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    raw = _env("USER_MEMORY_DIR", "")
    return Path(raw) if raw else _DEFAULT_DIR


def _goals_path(user: str) -> Path:
    return _root() / jsonstore.slugify(user) / "goals.json"


def _legacy_goal_path(user: str) -> Path:
    return _root() / jsonstore.slugify(user) / "goal.json"


def _lock_for(user: str) -> threading.Lock:
    slug = jsonstore.slugify(user)
    with _locks_guard:
        lock = _locks.get(slug)
        if lock is None:
            lock = _locks[slug] = threading.Lock()
        return lock


def _find(doc: dict, goal_id: str) -> Optional[Dict[str, Any]]:
    for g in doc.get("goals") or []:
        if g.get("id") == goal_id:
            return g
    return None


def _new_goal(text: str, sport: Optional[str], source: str) -> Dict[str, Any]:
    now = _now()
    return {
        "id": uuid.uuid4().hex[:12],
        "text": text,
        "sport": sport or None,
        "source": source if source in ("user", "coach") else "user",
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "panel": None,
        "panel_status": "empty",
        "panel_updated_at": None,
    }


def _legacy_to_text(g: dict) -> str:
    """Fold the old structured goal (title/metric/target/unit/deadline/why) into text."""
    parts = [g.get("title") or "Training goal"]
    if g.get("metric") and g.get("target") is not None:
        unit = (g.get("unit") or "").strip()
        parts.append(f"target: {g['target']} {unit} ({g['metric']}, "
                     f"{g.get('direction', 'toward')})".replace("  ", " ").strip())
    if g.get("deadline"):
        parts.append(f"by {g['deadline']}")
    if g.get("why"):
        parts.append(f"— {g['why']}")
    return " ".join(p for p in parts if p).strip()


def _load_doc(user: str) -> Dict[str, Any]:
    """Read goals.json, migrating a legacy single goal.json in place on first read.

    Always returns ``{"goals": [...]}``. Best-effort: a failed migration write still
    returns the in-memory synthesized doc so reads work. Caller should hold the lock
    if about to mutate (this function itself may write once, for migration).
    """
    path = _goals_path(user)
    doc = jsonstore.read_json(path)
    if isinstance(doc, dict) and isinstance(doc.get("goals"), list):
        return doc

    doc = {"goals": []}
    legacy_path = _legacy_goal_path(user)
    legacy = jsonstore.read_json(legacy_path)
    if isinstance(legacy, dict) and (legacy.get("title") or legacy.get("why")):
        goal = _new_goal(_legacy_to_text(legacy), sport=None,
                         source="coach" if legacy.get("source") == "coach" else "user")
        goal["created_at"] = legacy.get("created_at") or goal["created_at"]
        goal["updated_at"] = legacy.get("updated_at") or goal["updated_at"]
        if legacy.get("status") in _VALID_GOAL_STATUS:
            goal["status"] = legacy["status"]
        doc["goals"].append(goal)
        try:
            jsonstore.atomic_write(path, doc)
            legacy_path.rename(legacy_path.with_suffix(legacy_path.suffix + ".migrated"))
        except OSError as exc:
            print(f"[goal_store] legacy migration skipped for "
                  f"{jsonstore.slugify(user)}: {exc}", flush=True)
    return doc


# ── Reads ──────────────────────────────────────────────────────────────────────

def list_goals(user: str) -> List[Dict[str, Any]]:
    """All goals (any status), active-first then newest-created-first."""
    if not user:
        return []
    with _lock_for(user), jsonstore.flock(_goals_path(user)):
        doc = _load_doc(user)
    goals = list(doc.get("goals") or [])
    goals.sort(key=lambda g: g.get("created_at") or "", reverse=True)
    goals.sort(key=lambda g: 0 if g.get("status") == "active" else 1)
    return goals


def has_active_goal(user: str) -> bool:
    return any(g.get("status") == "active" for g in list_goals(user))


def get_goal(user: str, goal_id: str) -> Optional[Dict[str, Any]]:
    if not user or not goal_id:
        return None
    with _lock_for(user), jsonstore.flock(_goals_path(user)):
        doc = _load_doc(user)
        return _find(doc, goal_id)


# ── Writes (each: lock → re-read the whole doc → mutate → atomic write) ────────

def add_goal(user: str, text: str, sport: Optional[str] = None,
             source: str = "user") -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not user or not text:
        return None
    path = _goals_path(user)
    with _lock_for(user), jsonstore.flock(path):
        doc = _load_doc(user)
        goal = _new_goal(text, sport, source)
        doc.setdefault("goals", []).append(goal)
        try:
            jsonstore.atomic_write(path, doc)
        except OSError as exc:
            print(f"[goal_store] add_goal write skipped for "
                  f"{jsonstore.slugify(user)}: {exc}", flush=True)
            return None
        return goal


_UPDATE_ALLOWED = {"text", "sport", "status"}


def update_goal(user: str, goal_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Update text/sport/status. No-ops (returns None) if the goal doesn't exist —
    e.g. it was deleted while a background panel build was in flight."""
    if not user or not goal_id:
        return None
    path = _goals_path(user)
    with _lock_for(user), jsonstore.flock(path):
        doc = _load_doc(user)
        goal = _find(doc, goal_id)
        if goal is None:
            return None
        for k, v in fields.items():
            if k not in _UPDATE_ALLOWED or v is None:
                continue
            if k == "status" and v not in _VALID_GOAL_STATUS:
                continue
            if k == "text":
                v = (v or "").strip()
                if not v:
                    continue
            goal[k] = v
        goal["updated_at"] = _now()
        try:
            jsonstore.atomic_write(path, doc)
        except OSError as exc:
            print(f"[goal_store] update_goal write skipped: {exc}", flush=True)
            return None
        return goal


def delete_goal(user: str, goal_id: str) -> bool:
    if not user or not goal_id:
        return False
    path = _goals_path(user)
    with _lock_for(user), jsonstore.flock(path):
        doc = _load_doc(user)
        before = len(doc.get("goals") or [])
        doc["goals"] = [g for g in (doc.get("goals") or []) if g.get("id") != goal_id]
        if len(doc["goals"]) == before:
            return False
        try:
            jsonstore.atomic_write(path, doc)
            return True
        except OSError:
            return False


def set_panel_status(user: str, goal_id: str, status: str) -> Optional[Dict[str, Any]]:
    """No-ops on a missing goal (deleted mid-build) or an invalid status."""
    if status not in _VALID_PANEL_STATUS or not user or not goal_id:
        return None
    path = _goals_path(user)
    with _lock_for(user), jsonstore.flock(path):
        doc = _load_doc(user)
        goal = _find(doc, goal_id)
        if goal is None:
            return None
        goal["panel_status"] = status
        goal["updated_at"] = _now()
        try:
            jsonstore.atomic_write(path, doc)
        except OSError as exc:
            print(f"[goal_store] set_panel_status write skipped: {exc}", flush=True)
            return None
        return goal


def set_panel(user: str, goal_id: str, panel: Any) -> Optional[Dict[str, Any]]:
    """Normalize + store an agent-authored panel. No-ops on a missing goal (deleted
    mid-build) — never resurrects a deleted goal."""
    if not user or not goal_id:
        return None
    path = _goals_path(user)
    with _lock_for(user), jsonstore.flock(path):
        doc = _load_doc(user)
        goal = _find(doc, goal_id)
        if goal is None:
            return None
        normalized = normalize_panel(panel)
        goal["panel"] = normalized
        goal["panel_status"] = "ready"
        goal["panel_updated_at"] = normalized["generated_at"]
        goal["updated_at"] = _now()
        try:
            jsonstore.atomic_write(path, doc)
        except OSError as exc:
            print(f"[goal_store] set_panel write skipped: {exc}", flush=True)
            return None
        return goal


# ── Panel normalization (the shared shape contract) ────────────────────────────

def normalize_panel(raw: Any) -> Dict[str, Any]:
    """Coerce arbitrary agent output into a well-formed Panel. Never raises — this
    is what keeps the coach's inline authoring and the background builder from
    diverging in shape, and what protects the frontend from malformed content."""
    raw = raw if isinstance(raw, dict) else {}

    headline = str(raw.get("headline") or "").strip()[:200] or "Goal update"
    status = raw.get("status")
    status = status if status in _VALID_PANEL_HEALTH else "unknown"

    tiles: List[Dict[str, str]] = []
    for t in (raw.get("tiles") or [])[:_MAX_TILES]:
        if not isinstance(t, dict):
            continue
        label = str(t.get("label") or "").strip()[:40]
        value = str(t.get("value") or "").strip()[:40]
        if not label or not value:
            continue
        tile = {"label": label, "value": value}
        if t.get("sub"):
            tile["sub"] = str(t["sub"]).strip()[:60]
        tiles.append(tile)

    progress = None
    p = raw.get("progress")
    if isinstance(p, dict) and p.get("pct") is not None:
        try:
            pct = max(0.0, min(100.0, float(p["pct"])))
            progress = {"pct": round(pct, 1), "label": str(p.get("label") or "").strip()[:60]}
        except (TypeError, ValueError):
            progress = None

    chart = None
    c = raw.get("chart")
    if isinstance(c, dict):
        kind = c.get("kind") if c.get("kind") in ("line", "bar") else "line"
        points: List[Dict[str, Any]] = []
        for pt in (c.get("points") or [])[:_MAX_CHART_POINTS]:
            if isinstance(pt, dict) and "x" in pt and "y" in pt:
                try:
                    points.append({"x": pt["x"], "y": float(pt["y"])})
                except (TypeError, ValueError):
                    continue
        if points:
            chart = {"kind": kind, "points": points}
            if c.get("y_label"):
                chart["y_label"] = str(c["y_label"]).strip()[:40]

    note = str(raw.get("note") or "").strip()[:4000]

    return {
        "headline": headline,
        "status": status,
        "tiles": tiles,
        "progress": progress,
        "note": note,
        "chart": chart,
        "generated_at": _now(),
    }
