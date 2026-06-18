"""Turn a route-tool result into *openable* artifacts — no Streamlit, no network.

The static PNG (core/route_render.py) lets a Telegram user *see* a route; this
module lets them *open* it:

  - ``google_maps_url(route_data)`` — a tappable Google Maps directions link that
    opens the Maps app (or Apple Maps / browser). Google re-routes between the
    points and caps intermediate waypoints, so it is an *approximation* of the
    planned path, not the exact polyline.
  - ``route_gpx(route_data)`` — the *exact* planned track as GPX bytes, openable in
    OsmAnd, Komoot, Organic Maps, Garmin Connect, Strava, … (Google Maps can't
    import GPX — that's why the link above exists).

Input is the orchestrator's ``trace["route_data"]`` shape ``{"tool", "data"}`` —
see ``core.agent_trace.route_data``. Both functions return ``None`` when there
is nothing applicable, so callers can simply skip them.
"""

from __future__ import annotations

import xml.sax.saxutils as _su
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

_LatLon = Tuple[float, float]
_SINGLE_ROUTE_TOOLS = ("plan_route", "plan_circular_route")
_GMAPS_MAX_WAYPOINTS = 8  # the consumer dir URL practically supports ~9 stops


# ── Google Maps directions link ─────────────────────────────────────────────────

def google_maps_url(route_data: Optional[Dict]) -> Optional[str]:
    """Tappable Google Maps directions URL for a single planned route, else None."""
    pts = _ordered_latlon(route_data)
    if len(pts) < 2:
        return None
    origin, dest = pts[0], pts[-1]
    middle = _downsample(pts[1:-1], _GMAPS_MAX_WAYPOINTS) if len(pts) > 2 else []
    params = {
        "api": "1",
        "travelmode": _travel_mode(route_data),
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{dest[0]},{dest[1]}",
    }
    if middle:
        params["waypoints"] = "|".join(f"{lat},{lon}" for lat, lon in middle)
    # keep ',' and '|' literal so the URL stays clean and tappable
    return "https://www.google.com/maps/dir/?" + urlencode(params, safe="|,")


# ── GPX export ───────────────────────────────────────────────────────────────────

def route_gpx(route_data: Optional[Dict], name: str = "FitDash route") -> Optional[bytes]:
    """Exact route as GPX 1.1 track bytes (point-to-point, circular, or trails)."""
    tool = (route_data or {}).get("tool", "")
    data = (route_data or {}).get("data") or {}
    segments: List[List[Tuple[float, float, Optional[float]]]] = []

    if tool in _SINGLE_ROUTE_TOOLS:
        seg = [(wp["lat"], wp["lon"], wp.get("ele_m"))
               for wp in (data.get("waypoints") or [])
               if isinstance(wp, dict) and wp.get("lat") is not None and wp.get("lon") is not None]
        if len(seg) >= 2:
            segments.append(seg)
    elif tool == "explore_trails":
        for trail in (data.get("trails") or []):
            for raw in (trail.get("segments") or []):
                seg = [(p[1], p[0], None) for p in raw if len(p) >= 2]  # stored [lon, lat]
                if len(seg) >= 2:
                    segments.append(seg)

    if not segments:
        return None

    out: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="FitDash" xmlns="http://www.topografix.com/GPX/1/1">',
        f"<trk><name>{_su.escape(name)}</name>",
    ]
    for seg in segments:
        out.append("<trkseg>")
        for lat, lon, ele in seg:
            if ele is not None:
                out.append(f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele></trkpt>')
            else:
                out.append(f'<trkpt lat="{lat}" lon="{lon}"></trkpt>')
        out.append("</trkseg>")
    out.append("</trk></gpx>")
    return "\n".join(out).encode("utf-8")


# ── helpers ──────────────────────────────────────────────────────────────────────

def _ordered_latlon(route_data: Optional[Dict]) -> List[_LatLon]:
    """Ordered (lat, lon) for single-route tools; [] for trails/isochrone/empty."""
    tool = (route_data or {}).get("tool", "")
    data = (route_data or {}).get("data") or {}
    if tool in _SINGLE_ROUTE_TOOLS:
        return [(wp["lat"], wp["lon"]) for wp in (data.get("waypoints") or [])
                if isinstance(wp, dict) and wp.get("lat") is not None and wp.get("lon") is not None]
    return []


def _travel_mode(route_data: Optional[Dict]) -> str:
    prof = str(((route_data or {}).get("data") or {}).get("profile", "")).lower()
    if prof.startswith("cycl") or "bike" in prof or "ride" in prof:
        return "bicycling"
    if prof.startswith("driv") or "car" in prof:
        return "driving"
    return "walking"


def _downsample(pts: List[_LatLon], n: int) -> List[_LatLon]:
    """Evenly pick at most n points, keeping order (first/last preserved)."""
    if n <= 0 or len(pts) <= n:
        return pts
    if n == 1:
        return [pts[0]]
    step = (len(pts) - 1) / (n - 1)
    return [pts[round(i * step)] for i in range(n)]
