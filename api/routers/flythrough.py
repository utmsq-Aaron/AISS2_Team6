"""Flythrough — serve the self-contained 3D GPS flythrough HTML page.

`GET /flythrough/{activity_id}` loads the activity's GPS stream via the Strava
MCP tool, prepares the track, and returns the full MapLibre + WebCodecs HTML page
(the same engine the Streamlit app renders, via `core.flythrough_html`). The React
app fetches this **authenticated** and renders it in an `<iframe srcdoc>`; the
in-page Export button encodes an MP4 client-side, so there is no server-side
(Playwright) render to run here.
"""

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from api.deps import get_host
from core.flythrough_html import build_flythrough_html, prepare_track

router = APIRouter(prefix="/flythrough", tags=["flythrough"])

_MODES = {"satellite_3d", "dark"}
_ORIENTATIONS = {"landscape", "portrait"}
_RESOLUTIONS = {"HD", "2K", "4K"}


@router.get("/{activity_id}", response_class=HTMLResponse)
def flythrough_page(
    activity_id: int,
    mode: str = Query("satellite_3d"),
    orientation: str = Query("landscape"),
    resolution: str = Query("2K"),
    duration: int = Query(0, ge=0, le=120),
) -> HTMLResponse:
    """Return the standalone flythrough HTML for one Strava activity."""
    mode = mode if mode in _MODES else "satellite_3d"
    orientation = orientation if orientation in _ORIENTATIONS else "landscape"
    resolution = resolution if resolution in _RESOLUTIONS else "2K"

    raw = get_host().call_tool("strava__get_activity_streams", {"activity_id": activity_id})
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=502, detail="Could not read activity streams.")
    if isinstance(data, dict) and data.get("error"):
        raise HTTPException(status_code=404, detail=str(data["error"]))

    points = (data or {}).get("points") or []
    track = [
        [p["lon"], p["lat"], p.get("ele") or 0.0, p.get("time_s")]
        for p in points
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    if len(track) < 2:
        raise HTTPException(status_code=404, detail="No GPS route data for this activity.")

    track = prepare_track(track)
    name = ((data.get("activity") or {}).get("name")) or f"Activity {activity_id}"
    html = build_flythrough_html(
        track,
        name,
        mode=mode,
        auto_export=False,
        duration_sec=max(0, duration),
        orientation=orientation,
        resolution=resolution,
    )
    return HTMLResponse(content=html)
