"""Deep-analysis job records — status tracking for long background analyses.

When the orchestrator triages a request as DEEP, ``start_deep_analysis`` records a
job here and spawns ``core.deep_analysis.run_deep_job`` off-thread. The record lets
the user (and tests) see a job progress accepted → running → done | failed. The
finished report itself is delivered via ``core.proactive_outbox`` → the bridge, not
stored here. Best-effort JSON, mirroring ``core.chat_store``.

    data/deep_jobs/<slug>/<job_id>.json
        { job_id, user, topic, rationale, status, error, created_at, updated_at }
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from core.llm import _env

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "deep_jobs"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(user: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (user or "").strip().lower()).strip("-") or "anon"


def _root() -> Path:
    raw = _env("DEEP_JOBS_DIR", "")
    return Path(raw) if raw else _DEFAULT_DIR


def _path(user: str, job_id: str) -> Optional[Path]:
    if not re.fullmatch(r"[a-f0-9]{6,40}", job_id or ""):
        return None
    return _root() / _slug(user) / f"{job_id}.json"


def create_job(user: str, topic: str, rationale: str = "") -> Optional[str]:
    job_id = uuid.uuid4().hex[:12]
    rec = {
        "job_id": job_id, "user": user, "topic": topic, "rationale": rationale,
        "status": "accepted", "error": None,
        "created_at": _now(), "updated_at": _now(),
    }
    path = _path(user, job_id)
    if path is None:
        return None
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return job_id


def update_status(user: str, job_id: str, status: str, error: Optional[str] = None) -> None:
    path = _path(user, job_id)
    if path is None:
        return
    with _lock:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        rec["status"] = status
        rec["error"] = error
        rec["updated_at"] = _now()
        try:
            path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def get_job(user: str, job_id: str) -> Optional[dict]:
    path = _path(user, job_id)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_jobs(user: str) -> List[dict]:
    d = _root() / _slug(user)
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out
