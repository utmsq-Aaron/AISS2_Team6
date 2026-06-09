"""Routes — native FastMCP server (Streamable HTTP).

Route planning and trail discovery via OpenRouteService (ORS) + OpenStreetMap
(Overpass). Self-contained native MCP server — no BaseMCPServer, no dispatch
indirection. The app reaches it as a plain MCP client via core.host.ToolHost.

Run locally:   python -m servers.routes_mcp
Endpoint:      http://127.0.0.1:8102/mcp
Requires:      ORS_API_KEY in .env
"""

import os
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

ORS_BASE = "https://api.openrouteservice.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

HOST = os.getenv("ROUTES_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("ROUTES_MCP_PORT", "8102"))

mcp = FastMCP(
    "routes",
    instructions="Plan point-to-point and loop routes, elevation profiles, isochrones, and discover trails.",
    host=HOST,
    port=PORT,
    stateless_http=True,
)

VALID_PROFILES = {"cycling-regular", "cycling-mountain", "foot-hiking", "foot-walking"}

STRAVA_TO_PROFILE: Dict[str, str] = {
    "ride": "cycling-regular", "virtualride": "cycling-regular",
    "mountainbikeride": "cycling-mountain", "gravel_ride": "cycling-regular",
    "run": "foot-walking", "virtualrun": "foot-walking", "running": "foot-walking",
    "hike": "foot-hiking", "walk": "foot-walking",
    "alpineski": "foot-hiking", "nordicski": "foot-hiking",
}


def _api_key() -> str:
    key = os.getenv("ORS_API_KEY", "")
    if not key:
        raise RuntimeError("ORS_API_KEY not set. Get a free key at https://openrouteservice.org/dev")
    return key


def _headers() -> Dict[str, str]:
    return {"Authorization": _api_key(), "Content-Type": "application/json"}


def _profile(profile: Optional[str]) -> str:
    if not profile:
        return "foot-hiking"
    p = profile.lower().strip()
    if p in VALID_PROFILES:
        return p
    if p in STRAVA_TO_PROFILE:
        return STRAVA_TO_PROFILE[p]
    for v in VALID_PROFILES:
        if p in v or v in p:
            return v
    return "foot-hiking"


def _elevation_stats(elev: List[float]) -> Dict[str, Any]:
    if not elev:
        return {"gain_m": None, "loss_m": None, "min_m": None, "max_m": None}
    gain = loss = 0.0
    for i in range(1, len(elev)):
        diff = elev[i] - elev[i - 1]
        gain += diff if diff > 0 else 0
        loss += -diff if diff < 0 else 0
    return {"gain_m": round(gain, 1), "loss_m": round(loss, 1),
            "min_m": round(min(elev), 1), "max_m": round(max(elev), 1)}


def _simplify(raw: List[List[float]], target: int) -> List[Dict[str, Any]]:
    step = max(1, len(raw) // max(1, target))
    return [{"lat": round(c[1], 6), "lon": round(c[0], 6),
             "ele_m": round(c[2], 1) if len(c) > 2 else None} for c in raw[::step]]


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def plan_route(
    start_lat: float, start_lon: float, end_lat: float, end_lon: float,
    profile: Optional[str] = None,
    waypoints: Optional[List[List[float]]] = None,
    simplify_points: int = 100,
) -> Dict[str, Any]:
    """Plan a point-to-point route (A→B) with distance, duration, elevation and waypoints.

    Call this when the user asks to plan a specific route between two places. Accepts an
    ORS profile (cycling-regular, cycling-mountain, foot-hiking, foot-walking) or a Strava
    sport type (Ride, Run, Hike, Walk). Coordinates are latitude/longitude.

    Args:
        start_lat, start_lon: Start coordinate.
        end_lat, end_lon: End coordinate.
        profile: ORS profile or Strava sport type.
        waypoints: Optional intermediate points [[lat, lon], ...].
        simplify_points: Max waypoints to return (default 100, max 500).
    """
    prof = _profile(profile)
    coords = [[start_lon, start_lat]]
    for wp in (waypoints or []):
        coords.append([wp[1], wp[0]])
    coords.append([end_lon, end_lat])

    resp = requests.post(f"{ORS_BASE}/v2/directions/{prof}/geojson", headers=_headers(), json={
        "coordinates": coords, "elevation": True, "instructions": True,
        "units": "km", "geometry_simplify": False,
    }, timeout=20)
    if not resp.ok:
        return {"error": f"ORS directions {resp.status_code}: {resp.text[:200]}"}

    feat = resp.json()["features"][0]
    props = feat.get("properties", {})
    summary = props.get("summary", {})
    raw = feat.get("geometry", {}).get("coordinates", [])
    steps = [{"instruction": s.get("instruction", ""), "distance_km": round(s.get("distance", 0), 2)}
             for seg in props.get("segments", []) for s in (seg.get("steps") or []) if s.get("instruction")]
    return {
        "profile": prof,
        "distance_km": round(summary.get("distance", 0), 2),
        "duration_min": round(summary.get("duration", 0) / 60, 1),
        "elevation": _elevation_stats([c[2] for c in raw if len(c) > 2]),
        "waypoints_count": len(raw),
        "waypoints": _simplify(raw, min(int(simplify_points or 100), 500)),
        "instructions": steps[:50],
    }


@mcp.tool()
def plan_circular_route(lat: float, lon: float, distance_km: float,
                        profile: str = "foot-hiking") -> Dict[str, Any]:
    """Plan a circular (loop) route that starts and ends at the same point.

    Call this for "plan a 30 km bike loop from my home" or "find a 10 km running loop".

    Args:
        lat, lon: Start/end coordinate.
        distance_km: Target loop distance in km.
        profile: ORS profile or Strava sport type (default foot-hiking).
    """
    prof = _profile(profile)
    resp = requests.post(f"{ORS_BASE}/v2/directions/{prof}/geojson", headers=_headers(), json={
        "coordinates": [[lon, lat]],
        "options": {"round_trip": {"length": float(distance_km) * 1000, "points": 3, "seed": 1}},
        "elevation": True, "instructions": False, "units": "km",
    }, timeout=20)
    if not resp.ok:
        return {"error": f"ORS circular {resp.status_code}: {resp.text[:200]}"}

    feat = resp.json()["features"][0]
    summary = feat.get("properties", {}).get("summary", {})
    raw = feat.get("geometry", {}).get("coordinates", [])
    return {
        "profile": prof,
        "target_distance_km": float(distance_km),
        "actual_distance_km": round(summary.get("distance", 0), 2),
        "duration_min": round(summary.get("duration", 0) / 60, 1),
        "elevation": _elevation_stats([c[2] for c in raw if len(c) > 2]),
        "start_lat": lat, "start_lon": lon,
        "waypoints": _simplify(raw, 100),
    }


@mcp.tool()
def get_elevation_profile(coordinates: List[List[float]],
                          format_out: str = "geojson") -> Dict[str, Any]:
    """Get the elevation profile (gain/loss/min/max + per-point altitude) for coordinates.

    Call this to analyse the hilliness of a GPS track (e.g. from a Strava activity) or to
    enrich [lat, lon] points with altitude.

    Args:
        coordinates: List of [lat, lon] pairs.
        format_out: 'geojson' (default) or 'encodedpolyline'.
    """
    if not coordinates:
        return {"error": "No coordinates provided"}
    resp = requests.post(f"{ORS_BASE}/elevation/line", headers=_headers(), json={
        "format_in": "geojson", "format_out": format_out,
        "geometry": {"coordinates": [[c[1], c[0]] for c in coordinates], "type": "LineString"},
    }, timeout=20)
    if not resp.ok:
        return {"error": f"ORS elevation {resp.status_code}: {resp.text[:200]}"}
    raw = resp.json().get("geometry", {}).get("coordinates", [])
    return {
        "points": len(raw),
        "elevation": _elevation_stats([c[2] for c in raw if len(c) > 2]),
        "profile": _simplify(raw, len(raw)),
    }


@mcp.tool()
def explore_trails(lat: float, lon: float, radius_km: float = 15.0,
                   sport_type: str = "hiking", limit: int = 5, offset: int = 0) -> Dict[str, Any]:
    """Find named hiking / cycling / running trails near a location (OpenStreetMap).

    Call this for "what trails are near X?" or "show cycling routes where I hiked".

    Args:
        lat, lon: Centre coordinate.
        radius_km: Search radius in km (default 15, max 50).
        sport_type: hiking, cycling, running, or mtb.
        limit: Trails per page (default 5, max 20).
        offset: Skip the first N results (pagination).
    """
    radius_km = min(float(radius_km), 50)
    page_size = min(int(limit or 5), 20)
    offset = max(int(offset or 0), 0)
    fetch_limit = offset + page_size
    osm_route = {"hiking": "hiking", "hike": "hiking", "cycling": "bicycle", "bike": "bicycle",
                 "ride": "bicycle", "mtb": "mtb", "running": "running", "run": "running",
                 "foot": "hiking", "walk": "foot"}.get((sport_type or "hiking").lower(), "hiking")

    query = (f'[out:json][timeout:30];(relation["type"="route"]["route"="{osm_route}"]'
             f'(around:{radius_km * 1000},{lat},{lon}););out geom {fetch_limit};')
    resp = requests.post(OVERPASS_URL, data={"data": query},
                         headers={"Accept": "application/json", "User-Agent": "AISS2-Team6-RoutesMCP/1.0"},
                         timeout=35)
    if not resp.ok:
        return {"error": f"Overpass {resp.status_code}: {resp.text[:200]}"}

    all_el = resp.json().get("elements", [])
    page = all_el[offset:offset + page_size]
    trails = []
    for el in page:
        tags = el.get("tags", {})
        segments = []
        for m in el.get("members", []):
            if m.get("type") == "way":
                geom = m.get("geometry", [])
                if len(geom) >= 2:
                    step = max(1, len(geom) // 60)
                    segments.append([[pt["lon"], pt["lat"]] for pt in geom[::step]])
        b = el.get("bounds", {})
        trails.append({
            "osm_id": el.get("id"),
            "name": tags.get("name") or tags.get("name:en") or tags.get("ref", "Unnamed trail"),
            "route_type": tags.get("route"), "distance": tags.get("distance") or tags.get("length"),
            "network": tags.get("network"),
            "difficulty": tags.get("sac_scale") or tags.get("mtb:scale") or tags.get("difficulty"),
            "surface": tags.get("surface"),
            "description": tags.get("description") or tags.get("description:en"),
            "website": tags.get("website") or tags.get("url"),
            "segments": segments,
            "bounds": {"min_lat": b.get("minlat"), "min_lon": b.get("minlon"),
                       "max_lat": b.get("maxlat"), "max_lon": b.get("maxlon")} if b else None,
        })
    return {
        "search_centre": {"lat": lat, "lon": lon}, "radius_km": radius_km, "sport_type": osm_route,
        "offset": offset, "page_size": page_size, "has_more": len(all_el) == fetch_limit,
        "trails": trails,
    }


@mcp.tool()
def get_isochrone(lat: float, lon: float, range_value: float,
                  range_type: str = "time", profile: str = "cycling-regular") -> Dict[str, Any]:
    """Compute how far you can travel from a point within a time or distance budget.

    Call this for "what can I reach in 1 hour by bike?" or "area I can cover on a 20 km run".
    Returns a GeoJSON polygon (reachable area) + summary.

    Args:
        lat, lon: Start coordinate.
        range_value: For time: seconds (3600 = 1 h). For distance: metres (20000 = 20 km).
        range_type: 'time' (default) or 'distance'.
        profile: ORS profile or Strava sport type (default cycling-regular).
    """
    prof = _profile(profile)
    resp = requests.post(f"{ORS_BASE}/v2/isochrones/{prof}", headers=_headers(), json={
        "locations": [[lon, lat]], "range_type": range_type, "range": [float(range_value)],
        "units": "km", "attributes": ["area", "reachfactor"],
    }, timeout=20)
    if not resp.ok:
        return {"error": f"ORS isochrones {resp.status_code}: {resp.text[:200]}"}
    feats = resp.json().get("features", [])
    if not feats:
        return {"error": "No isochrone data returned"}
    feat = feats[0]
    props = feat.get("properties", {})
    geom = feat.get("geometry", {})
    poly = geom.get("coordinates", [[]])[0]
    lats, lons = [c[1] for c in poly], [c[0] for c in poly]
    return {
        "profile": prof, "range_type": range_type, "range_value": float(range_value),
        "range_label": (f"{int(range_value / 60)} min" if range_type == "time"
                        else f"{range_value / 1000:.1f} km"),
        "area_km2": round(props.get("area", 0), 2), "reach_factor": props.get("reachfactor"),
        "centre": {"lat": lat, "lon": lon},
        "bounding_box": {"min_lat": round(min(lats), 5), "max_lat": round(max(lats), 5),
                         "min_lon": round(min(lons), 5), "max_lon": round(max(lons), 5)},
        "polygon_points": len(poly), "geometry": geom,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
