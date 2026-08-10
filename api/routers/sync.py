"""Garmin → Strava sync — preview what's missing, then upload it.

Two stages on purpose. An upload to Strava is outward-facing and awkward to undo,
so the button in Settings never uploads on the first click: /sync/preview reports
which Garmin activities have no Strava counterpart, the user sees the list, and
only then does /sync/export run. The export streams per-activity progress over
SSE like the chat endpoint — a run can span several minutes (Strava processes each
upload asynchronously and we poll for the result), which is far too long to leave
a plain POST hanging.
"""

import asyncio
import json
import threading
from datetime import date, timedelta
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api import sync_service as svc
from api.auth import require_admin

router = APIRouter()

# Admin-only, like every other privileged Settings action. The credentials live in
# the instance's single-user .tokens/ files, so these endpoints act on the OWNER's
# Garmin and Strava accounts no matter who is logged in — a non-admin user must not
# be able to push the owner's activities to the owner's public Strava feed.
_ADMIN = [Depends(require_admin)]

# One Garmin/Strava session at a time — both clients log in per call, and a
# concurrent second run would race the same token files.
_sync_lock = threading.Lock()

# Guardrails on the range a single run may cover. The upper bound is not about
# performance: it is how much can be pushed to Strava by one accidental click.
MAX_DAYS = 365


class PreviewRequest(BaseModel):
    days: int = 30


@router.post("/sync/preview", dependencies=_ADMIN)
def preview(req: PreviewRequest):
    """Garmin activities in the last `days` days, each flagged against Strava."""
    days = max(1, min(req.days, MAX_DAYS))
    end = date.today()
    start = end - timedelta(days=days)
    try:
        with _sync_lock:
            result = svc.fetch_activities(start.isoformat(), end.isoformat())
    except Exception as exc:  # noqa: BLE001 — surfaced to the user as-is
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    acts = result.get("activities", [])
    # When Strava could not be read, `in_strava` is None everywhere and calling
    # anything "missing" would be a guess — an empty-but-readable Strava, by
    # contrast, legitimately makes every Garmin activity missing.
    missing = [a for a in acts if a.get("in_strava") is False] if result.get("strava_readable") else []
    return {**result, "missing": missing, "days": days}


class ExportActivity(BaseModel):
    id: int
    name: str | None = None
    date: str | None = None


class ExportRequest(BaseModel):
    activities: List[ExportActivity]


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/sync/export", dependencies=_ADMIN)
async def export(req: ExportRequest):
    """Upload the given Garmin activities to Strava, streaming progress."""
    loop = asyncio.get_running_loop()
    q: "asyncio.Queue[tuple]" = asyncio.Queue()
    acts = [a.model_dump() for a in req.activities]
    total = len(acts)

    def emit(event: str, data: Any) -> None:
        loop.call_soon_threadsafe(q.put_nowait, (event, data))

    def worker() -> None:
        # `done` must be emitted on EVERY path, including an unexpected raise:
        # the generator below blocks on the queue until it sees one, so a missed
        # `done` leaves the browser showing "Uploading…" forever.
        try:
            # Never block on the lock. A second run (double-click, or a previous
            # batch still polling after the browser went away) would otherwise
            # emit nothing at all for minutes.
            if not _sync_lock.acquire(blocking=False):
                emit("error", {"message": "A sync is already running — wait for it to finish."})
                return
            try:
                counts = {"ok": 0, "duplicate": 0, "skipped": 0, "error": 0}
                try:
                    garmin = svc.garmin_client()
                    token = svc.strava_token()
                except Exception as exc:  # noqa: BLE001
                    emit("error", {"message": str(exc)})
                    return
                for i, act in enumerate(acts):
                    name = act.get("name") or f"Activity {act.get('id')}"
                    emit("progress", {"index": i, "total": total, "name": name})
                    try:
                        result = svc.export_one(garmin, token, act)
                    except Exception as exc:  # noqa: BLE001 — one bad activity must not
                        result = {"status": "error", "name": name, "message": str(exc)}
                    counts[result["status"]] = counts.get(result["status"], 0) + 1
                    emit("result", {**result, "index": i, "total": total})
                emit("summary", counts)
            finally:
                _sync_lock.release()
        except Exception as exc:  # noqa: BLE001
            emit("error", {"message": f"Sync aborted: {exc}"})
        finally:
            emit("done", {})

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        while True:
            event, data = await q.get()
            yield _sse(event, data)
            if event == "done":
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
