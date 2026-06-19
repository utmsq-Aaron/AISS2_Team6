"""Routes — native FastMCP server (Streamable HTTP).

Route planning and trail discovery via OpenRouteService (ORS) + OpenStreetMap
(Overpass). Self-contained native MCP server — no BaseMCPServer, no dispatch
indirection. The app reaches it as a plain MCP client via core.host.ToolHost.

Run locally:   python -m servers.routes_mcp
Endpoint:      http://127.0.0.1:8102/mcp
Requires:      ORS_API_KEY in .env
Optional:      GOOGLE_GEOCODING_API_KEY in .env — enables the geocode tool (place
               name → coordinates) so routes can be anchored at named places.
"""

import os
import time
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

ORS_BASE = "https://api.openrouteservice.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

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


def _google_key() -> str:
    key = os.getenv("GOOGLE_GEOCODING_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key:
        raise RuntimeError(
            "GOOGLE_GEOCODING_API_KEY not set. Enable the Geocoding API and get a key at "
            "https://developers.google.com/maps/documentation/geocoding/get-api-key")
    return key


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


# ── Geometry helpers (containment / centroid) ─────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase + fold German diacritics, for fuzzy name matching."""
    s = (s or "").lower()
    for a, b in (("ß", "ss"), ("ä", "a"), ("ö", "o"), ("ü", "u")):
        s = s.replace(a, b)
    return s


def _point_in_ring(pt: List[float], ring: List[List[float]]) -> bool:
    """Ray-casting point-in-polygon. pt and ring vertices are [lon, lat]."""
    x, y = pt[0], pt[1]
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
            inside = not inside
    return inside


def _ring_centroid(ring: List[List[float]]) -> List[float]:
    pts = ring[:-1] if ring and ring[0] == ring[-1] else ring
    return [sum(c[0] for c in pts) / len(pts), sum(c[1] for c in pts) / len(pts)]


def _geocode(query: str, region: str = "de") -> Dict[str, Any]:
    """Core geocoder (Google) — shared by the geocode tool and plan_park_loop."""
    q = (query or "").strip()
    if not q:
        return {"error": "empty query"}
    # A comma makes Google parse the text as a STRUCTURED address and prefer a
    # literal street match — e.g. "Schlossgarten, Karlsruhe" resolves to a street
    # "Am Schloßgarten" 20 km away, while "Schlossgarten Karlsruhe" finds the park.
    # Place-name queries geocode far better comma-free; street addresses are
    # unaffected, so normalise commas to spaces.
    q = " ".join(q.replace(",", " ").split())
    try:
        resp = requests.get(GOOGLE_GEOCODE_URL, params={
            "address": q, "region": region or "", "key": _google_key(),
        }, timeout=15)
    except RuntimeError as exc:  # missing key — surface clearly
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — network/transport
        return {"error": f"geocode request failed: {type(exc).__name__}: {exc}"}
    if not resp.ok:
        return {"error": f"Google geocode HTTP {resp.status_code}: {resp.text[:200]}"}
    body = resp.json()
    status = body.get("status")
    if status != "OK" or not body.get("results"):
        return {"error": f"geocode {status}: {body.get('error_message', 'no results')}",
                "query": q}
    top = body["results"][0]
    geom = top.get("geometry", {})
    loc = geom.get("location", {})
    box = geom.get("viewport") or geom.get("bounds") or {}
    ne, sw = box.get("northeast", {}), box.get("southwest", {})
    bbox = ({"min_lat": sw.get("lat"), "min_lon": sw.get("lng"),
             "max_lat": ne.get("lat"), "max_lon": ne.get("lng")} if ne and sw else None)
    return {
        "query": q,
        "lat": loc.get("lat"), "lon": loc.get("lng"),
        "name": top.get("formatted_address"),
        "bbox": bbox,
        "location_type": geom.get("location_type"),
        "types": top.get("types", []),
    }


def _fetch_area_polygon(lat: float, lon: float, name_hint: str = "",
                        radius_m: int = 1000) -> Optional[Dict[str, Any]]:
    """Find the boundary polygon of a park/green area near (lat, lon) via OSM Overpass.

    Picks, among nearby leisure=park/garden/nature_reserve/recreation_ground areas:
    a name match to ``name_hint`` first, then the polygon that contains the point,
    then the largest. Returns {"ring": [[lon,lat],…closed], "name", "osm_id"} or None.
    """
    selectors = "".join(
        f'way["leisure"="{v}"](around:{radius_m},{lat},{lon});'
        f'relation["leisure"="{v}"](around:{radius_m},{lat},{lon});'
        for v in ("park", "garden", "nature_reserve", "recreation_ground"))
    query = f"[out:json][timeout:30];({selectors});out geom tags;"
    elements = None
    for attempt in range(3):  # Overpass is flaky (429/504/transient empties) — retry
        try:
            resp = requests.post(OVERPASS_URL, data={"data": query},
                                 headers={"Accept": "application/json",
                                          "User-Agent": "AISS2-Team6-RoutesMCP/1.0"}, timeout=35)
            if resp.ok:
                els = resp.json().get("elements", [])
                if els:
                    elements = els
                    break
        except Exception:  # noqa: BLE001 — transient; retry then fall back
            pass
        if attempt < 2:
            time.sleep(0.7)
    if not elements:
        return None

    hint = _norm(name_hint)
    best, best_score = None, -1.0
    for el in elements:
        geom = el.get("geometry") or []
        if len(geom) < 4:
            continue
        ring = [[p["lon"], p["lat"]] for p in geom]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        lons = [c[0] for c in ring]
        lats = [c[1] for c in ring]
        area = (max(lons) - min(lons)) * (max(lats) - min(lats))  # bbox area (deg²)
        nm = _norm(el.get("tags", {}).get("name", ""))
        name_match = bool(nm) and (nm in hint or hint in nm)
        contains = _point_in_ring([lon, lat], ring)
        # name match dominates; then containment; then size (tie-break)
        score = (2.0 if name_match else 0.0) + (1.0 if contains else 0.0) + min(area * 50, 0.9)
        if score > best_score:
            best, best_score = (ring, el.get("tags", {}).get("name"), el.get("id")), score
    if not best:
        return None
    return {"ring": best[0], "name": best[1], "osm_id": best[2]}


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def geocode(query: str, region: str = "de") -> Dict[str, Any]:
    """Resolve a place name or address to coordinates (Google Geocoding API).

    Call this FIRST whenever the user names a place — a park, landmark, address,
    neighbourhood, or city — before planning a route. Turn the name into lat/lon,
    then pass those coordinates to plan_route / plan_circular_route / explore_trails.
    Do NOT guess coordinates and do NOT fall back to the home location when the user
    has named a specific place.

    For a loop that must STAY INSIDE a named park/green area, prefer plan_park_loop
    (it geocodes, fetches the boundary, and constrains the route to it).

    Args:
        query: Place name or address, e.g. "Schlossgarten, Karlsruhe" or
            "Hauptbahnhof Karlsruhe". Include the city/country for accuracy.
        region: ccTLD region bias for ambiguous names (default "de" = Germany).

    Returns:
        {query, lat, lon, name (formatted address), bbox {min_lat,min_lon,max_lat,
        max_lon} or None, location_type, types} on success, or {error, query}.
        The bbox is the place's bounding box — useful to keep a loop near/within it.
    """
    return _geocode(query, region)


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
def plan_park_loop(area: str, distance_km: float = 3.0,
                   profile: str = "foot-walking") -> Dict[str, Any]:
    """Plan a loop that stays INSIDE a named park / green area.

    Use this when the user wants a route that stays within a specific park, garden or
    green space (e.g. "a running loop that stays inside Schlossgarten"). It geocodes the
    area, fetches its boundary from OpenStreetMap, and constrains an OpenRouteService
    round-trip to that boundary by avoiding everything outside it. The area may be small,
    so the actual loop can be SHORTER than requested — the result reports the real
    distance and what fraction of the path lies inside the boundary (containment_pct).

    Args:
        area: Park/area name, e.g. "Schlossgarten Karlsruhe" (name + city, no comma).
        distance_km: Target loop distance in km (default 3); capped by the area's size.
        profile: ORS profile or Strava sport type (default foot-walking).

    Returns:
        Same shape as plan_circular_route (profile, distance_km, duration_min, elevation,
        start_lat/lon, waypoints) PLUS: area (resolved name), area_osm_id, contained
        (bool), containment_pct (0–100 or None), note (honest caveat). On failure: {error}.
    """
    g = _geocode(area)
    if g.get("error"):
        return {"error": f"could not locate '{area}': {g['error']}"}
    clat, clon = g["lat"], g["lon"]
    prof = _profile(profile)
    poly = _fetch_area_polygon(clat, clon, g.get("name") or area)

    def _round_trip(anchor_lat, anchor_lon, length_m, avoid):
        body = {"coordinates": [[anchor_lon, anchor_lat]],
                "options": {"round_trip": {"length": float(length_m), "points": 5, "seed": 1}},
                "elevation": True, "instructions": False, "units": "km"}
        if avoid:
            body["options"]["avoid_polygons"] = avoid
        return requests.post(f"{ORS_BASE}/v2/directions/{prof}/geojson",
                             headers=_headers(), json=body, timeout=25)

    feat, raw, pct, contained, note = None, [], None, False, None

    if poly:
        ring = poly["ring"]
        lons = [c[0] for c in ring]
        lats = [c[1] for c in ring]
        m = 0.02  # ~2 km outer margin; routing must stay in the hole (the park)
        outer = [[min(lons) - m, min(lats) - m], [max(lons) + m, min(lats) - m],
                 [max(lons) + m, max(lats) + m], [min(lons) - m, max(lats) + m],
                 [min(lons) - m, min(lats) - m]]
        avoid = {"type": "Polygon", "coordinates": [outer, ring]}
        anchor = [clon, clat] if _point_in_ring([clon, clat], ring) else _ring_centroid(ring)
        # the boundary caps the loop length; retry shorter until ORS can route it
        for factor in (1.0, 0.66, 0.4, 0.25):
            resp = _round_trip(anchor[1], anchor[0], float(distance_km) * 1000 * factor, avoid)
            if resp.ok:
                feat = resp.json()["features"][0]
                raw = feat.get("geometry", {}).get("coordinates", [])
                if raw:
                    break
        if raw:
            inside = sum(_point_in_ring(c[:2], ring) for c in raw)
            pct = round(100 * inside / len(raw), 1)
            contained = pct >= 90
            note = (f"Route constrained to {poly.get('name') or area}; "
                    f"{pct:.0f}% of the path lies inside the park boundary.")
        else:
            note = (f"Found the boundary of {poly.get('name') or area} but ORS could not build "
                    f"a loop inside it (the area may be too small for this distance).")

    # Fallback: no boundary found, or constrained routing failed → anchored, uncontained loop
    if not raw:
        resp = _round_trip(clat, clon, float(distance_km) * 1000, None)
        if not resp.ok:
            return {"error": f"ORS round-trip {resp.status_code}: {resp.text[:200]}",
                    "area": g.get("name") or area}
        feat = resp.json()["features"][0]
        raw = feat.get("geometry", {}).get("coordinates", [])
        if not raw:
            return {"error": "ORS returned an empty route", "area": g.get("name") or area}
        if note is None:
            note = (f"No mappable boundary was found for '{area}', so the loop is anchored at "
                    f"the area but is NOT constrained to stay inside it.")

    summary = feat.get("properties", {}).get("summary", {})
    return {
        "profile": prof,
        "target_distance_km": float(distance_km),
        "distance_km": round(summary.get("distance", 0), 2),
        "duration_min": round(summary.get("duration", 0) / 60, 1),
        "elevation": _elevation_stats([c[2] for c in raw if len(c) > 2]),
        "start_lat": raw[0][1], "start_lon": raw[0][0],
        "area": (poly.get("name") if poly else None) or g.get("name") or area,
        "area_osm_id": poly.get("osm_id") if poly else None,
        "contained": contained,
        "containment_pct": pct,
        "note": note,
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
