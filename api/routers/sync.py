"""Garmin → Strava sync — fetch/compare + SSE export.

Mirrors ui/sync.py: a two-stage flow (fetch a date range with Strava-duplicate
flags, then export selected activities). The export streams per-activity progress
over SSE, like the chat endpoint.
"""

import asyncio
import json
import threading
from typing import Any, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api import sync_service as svc

router = APIRouter()

# Serialize Garmin/Strava sessions (one login at a time).
_sync_lock = threading.Lock()


class FetchRequest(BaseModel):
    start: str  # YYYY-MM-DD
    end: str    # YYYY-MM-DD


@router.post("/sync/fetch")
def fetch(req: FetchRequest):
    try:
        with _sync_lock:
            return svc.fetch_activities(req.start, req.end)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/sync/route")
def route(activity_id: int = Query(...)):
    return {"coords": svc.route_coords(activity_id)}


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
                except Exception as exc:  # noqa: BLE001
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
