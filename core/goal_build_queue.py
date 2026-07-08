"""Disk queue handing a goal-panel BUILD from FastAPI to the Telegram bridge.

FastAPI runs under ``uvicorn --reload`` — a code-edit restart kills any daemon
thread it spawned mid-build, wedging a panel in "building" forever. So a
form-created/refreshed goal's build is not spawned in-process; it is dropped here
as a small file, and the bridge (the one always-on process) drains the queue each
scheduler tick and spawns ``core.goal_panel.build_panel`` itself. (A chat-created
goal is different: the orchestrator agent process is durable too — no ``--reload``
— so ``add_goal`` spawns the build directly; see ``core.orchestrator_agent``.)

    data/goal_build/<slug>/<goal_id>.json
        { user, goal_id, enqueued_at }

One file per goal_id — enqueuing the same goal again is a harmless overwrite, not
a duplicate (idempotent). Mirrors ``core.proactive_outbox``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from core import jsonstore

_ROOT = Path(__file__).resolve().parent.parent
_QUEUE = _ROOT / "data" / "goal_build"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(user: str, goal_id: str) -> bool:
    """Queue (or re-queue) a panel build. Idempotent per goal_id. Best-effort."""
    if not user or not goal_id:
        return False
    path = _QUEUE / jsonstore.slugify(user) / f"{goal_id}.json"
    try:
        jsonstore.atomic_write(path, {"user": user, "goal_id": goal_id, "enqueued_at": _now()})
        return True
    except OSError as exc:
        print(f"[goal_build_queue] enqueue skipped for {jsonstore.slugify(user)}: {exc}",
              flush=True)
        return False


def pending() -> List[Tuple[Path, Dict[str, str]]]:
    """All queued builds across users, as (path, record). Best-effort."""
    out: List[Tuple[Path, Dict[str, str]]] = []
    if not _QUEUE.exists():
        return out
    for p in sorted(_QUEUE.glob("*/*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and rec.get("user") and rec.get("goal_id"):
                out.append((p, rec))
        except (OSError, ValueError):
            continue
    return out


def remove(path: Path) -> None:
    """Drop a drained queue entry (best-effort)."""
    try:
        Path(path).unlink()
    except OSError:
        pass
