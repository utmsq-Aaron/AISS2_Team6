"""Render route-tool results to a static PNG — no browser, no Streamlit.

The Chat tab draws route results as *interactive* folium maps (ui/chat.py
``_render_route_map``). That needs a browser, so it can't be sent over Telegram.
This module produces a flat PNG of the same geometry via the ``staticmap``
package (OSM tiles + Pillow) so the Telegram bridge can ship a route as a photo.

Input is the orchestrator's ``trace["route_data"]`` shape:
``{"tool": <bare route tool>, "data": <tool result dict>}`` — see
``core.agent_trace.route_data``. Returns PNG bytes, or ``None`` when there is
nothing renderable (so callers can simply skip sending an image).
"""

from __future__ import annotations

import io
from typing import Dict, List, Optional, Tuple

# Keep colours in sync with ui/chat.py so web and Telegram look alike.
_LINE = "#FF6400"
_START = "#16A34A"   # green
_END = "#EF4444"     # red
_TRAIL_COLORS = ["#FF6400", "#1E96FF", "#00C864", "#C832C8", "#FFC800"]
_ISO_LINE = "#1E96FF"
_ISO_CENTRE = "#0050AA"

_W, _H = 1000, 720
_PAD = 60  # keep the track off the very edge of the image

_Coord = Tuple[float, float]  # (lon, lat) — staticmap order


def render_route_image(route_data: Optional[Dict]) -> Optional[bytes]:
    """Render ``route_data`` to PNG bytes, or ``None`` if nothing to draw."""
    try:
        from staticmap import CircleMarker, Line, StaticMap
    except ImportError:
        return None

    tool = (route_data or {}).get("tool", "")
    data = (route_data or {}).get("data") or {}
    if not tool or not isinstance(data, dict):
        return None

    smap = StaticMap(
        _W, _H, padding_x=_PAD, padding_y=_PAD,
        url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
        headers={"User-Agent": "FitDash/1.0 (route map)"},
    )
    drew = False

    if tool in ("plan_route", "plan_circular_route"):
        coords = _waypoint_coords(data.get("waypoints"))
        if len(coords) >= 2:
            smap.add_line(Line(coords, _LINE, 5))
            smap.add_marker(CircleMarker(coords[0], _START, 16))
            smap.add_marker(CircleMarker(coords[-1], _END, 16))
            drew = True

    elif tool == "explore_trails":
        for i, trail in enumerate(data.get("trails") or []):
            color = _TRAIL_COLORS[i % len(_TRAIL_COLORS)]
            for seg in (trail.get("segments") or []):
                # segments are stored as [lon, lat] pairs already
                pts = [(p[0], p[1]) for p in seg if len(p) >= 2]
                if len(pts) >= 2:
                    smap.add_line(Line(pts, color, 4))
                    drew = True

    elif tool in ("get_activity_streams", "get_activity_gps_track"):
        pts = data.get("points") or []
        # Downsample to at most 400 points so staticmap stays fast
        if len(pts) > 400:
            step = len(pts) / 400
            pts = [pts[int(i * step)] for i in range(400)] + [pts[-1]]
        coords = [(p["lon"], p["lat"]) for p in pts
                  if p.get("lat") is not None and p.get("lon") is not None]
        if len(coords) >= 2:
            smap.add_line(Line(coords, _LINE, 4))
            smap.add_marker(CircleMarker(coords[0], _START, 16))
            smap.add_marker(CircleMarker(coords[-1], _END, 16))
            drew = True

    elif tool == "get_isochrone":
        geom = data.get("geometry") or {}
        for ring in (geom.get("coordinates") or []):
            pts = [(c[0], c[1]) for c in ring if len(c) >= 2]
            if len(pts) >= 2:
                smap.add_line(Line(pts, _ISO_LINE, 3))
                drew = True
        centre = data.get("centre") or {}
        if _has(centre, "lat", "lon"):
            smap.add_marker(CircleMarker((centre["lon"], centre["lat"]), _ISO_CENTRE, 16))
            drew = True

    if not drew:
        return None

    try:
        image = smap.render()  # auto-fits zoom/centre to the added features
    except Exception:
        return None

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# ── helpers ────────────────────────────────────────────────────────────────────

def _waypoint_coords(waypoints) -> List[_Coord]:
    out: List[_Coord] = []
    for wp in (waypoints or []):
        if isinstance(wp, dict) and "lat" in wp and "lon" in wp:
            out.append((wp["lon"], wp["lat"]))
    return out


def _has(d: Dict, *keys: str) -> bool:
    return all(d.get(k) is not None for k in keys)
