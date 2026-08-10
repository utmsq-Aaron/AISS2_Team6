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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api import sync_service as svc

router = APIRouter()

# One Garmin/Strava session at a time — both clients log in per call, and a
# concurrent second run would race the same token files.
_sync_lock = threading.Lock()

# Guardrails on the range a single run may cover. The upper bound is not about
# performance: it is how much can be pushed to Strava by one accidental click.
MAX_DAYS = 365


class PreviewRequest(BaseModel):
    days: int = 30


@router.post("/sync/preview")
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
    # `has_matches` false means the Strava fetch itself failed or returned nothing,
    # so `in_strava` is None everywhere and "missing" would be a lie.
    missing = [a for a in acts if a.get("in_strava") is False] if result.get("has_matches") else []
    return {**result, "missing": missing, "days": days}


class ExportActivity(BaseModel):
    id: int
    name: str | None = None
    date: str | None = None


class ExportRequest(BaseModel):
    activities: List[ExportActivity]


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/sync/export")
async def export(req: ExportRequest):
    """Upload the given Garmin activities to Strava, streaming progress."""
    loop = asyncio.get_running_loop()
    q: "asyncio.Queue[tuple]" = asyncio.Queue()
    acts = [a.model_dump() for a in req.activities]
    total = len(acts)

    def emit(event: str, data: Any) -> None:
        loop.call_soon_threadsafe(q.put_nowait, (event, data))

    def worker() -> None:
        with _sync_lock:
            counts = {"ok": 0, "duplicate": 0, "skipped": 0, "error": 0}
            try:
                garmin = svc.garmin_client()
                token = svc.strava_token()
            except Exception as exc:  # noqa: BLE001
                emit("error", {"message": str(exc)})
                emit("done", {})
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
