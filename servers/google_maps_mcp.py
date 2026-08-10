"""Google Maps — native FastMCP server (Streamable HTTP).

Self-contained MCP server for place search, place details, geocoding and
directions, backed by the CURRENT Google Maps Platform APIs — Places API (New),
Geocoding API v4 and the Routes API. These all work with a billing-free Maps
Demo Key (unlike the legacy APIs the archived ``@modelcontextprotocol/
server-google-maps`` npm proxy called, which this server replaces — same tool
names, so the route agent's prompt keeps working unchanged).

Run locally:   python -m servers.google_maps_mcp
Endpoint:      http://127.0.0.1:8108/mcp   (override host/port via env)

Requires:
    GOOGLE_MAPS_API_KEY   Google Maps Platform API key (a Demo Key suffices).
                          Enable: Places API (New), Geocoding API, Routes API.

Demo-key caveat: user-generated content (reviews, photos) is not served; the
field masks below therefore retry without rating fields when Google rejects them.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

HOST = os.getenv("GOOGLE_MAPS_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("GOOGLE_MAPS_MCP_PORT", "8108"))
TIMEOUT = 15

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
GEOCODE_ADDRESS_URL = "https://geocode.googleapis.com/v4/geocode/address/{address}"
GEOCODE_LOCATION_URL = "https://geocode.googleapis.com/v4/geocode/location/{lat},{lng}"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

# travel-mode aliases → Routes API enum
_MODES = {
    "walking": "WALK", "walk": "WALK", "foot": "WALK",
    "driving": "DRIVE", "drive": "DRIVE", "car": "DRIVE",
    "bicycling": "BICYCLE", "cycling": "BICYCLE", "bike": "BICYCLE", "bicycle": "BICYCLE",
    "transit": "TRANSIT", "public": "TRANSIT",
}

# Rating/user-count are aggregated user-generated content — a billing-free Demo
# Key does not serve UGC, so requests including them can be rejected. Ask for
# the full mask first, fall back to the basic one on refusal.
_SEARCH_MASK_FULL = ("places.id,places.displayName,places.formattedAddress,"
                     "places.location,places.types,places.rating,places.userRatingCount,"
                     "places.currentOpeningHours.openNow")
_SEARCH_MASK_BASIC = ("places.id,places.displayName,places.formattedAddress,"
                      "places.location,places.types,places.currentOpeningHours.openNow")
_DETAILS_MASK_FULL = ("id,displayName,formattedAddress,location,types,"
                      "internationalPhoneNumber,websiteUri,regularOpeningHours,"
                      "rating,userRatingCount,priceLevel")
_DETAILS_MASK_BASIC = ("id,displayName,formattedAddress,location,types,"
                       "internationalPhoneNumber,websiteUri,regularOpeningHours")


def _check_prereqs() -> None:
    """Fail fast with an actionable error; ToolHost skips unreachable servers."""
    if not os.getenv("GOOGLE_MAPS_API_KEY"):
        sys.exit("[google_maps] missing required env: GOOGLE_MAPS_API_KEY (see .env.example).")


def _key() -> str:
    return os.getenv("GOOGLE_MAPS_API_KEY", "")


def _headers(field_mask: Optional[str] = None) -> Dict[str, str]:
    h = {"X-Goog-Api-Key": _key(), "Content-Type": "application/json"}
    if field_mask:
        h["X-Goog-FieldMask"] = field_mask
    return h


def _err(resp: requests.Response, what: str) -> Dict[str, Any]:
    try:
        msg = resp.json().get("error", {}).get("message", resp.text[:200])
    except Exception:  # noqa: BLE001
        msg = resp.text[:200]
    return {"error": f"{what} failed (HTTP {resp.status_code}): {msg}"}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 12742000 * math.asin(math.sqrt(a))


def _geocode_point(text: str, region: str = "de") -> Optional[Dict[str, float]]:
    """Resolve a name/address to {latitude, longitude}, or None.

    Geocoding v4 first (exact for addresses); if it has no result — typical for
    fuzzy POI names like "KIT Campus Süd" without a city — fall back to a Places
    text search, which handles such names far better.
    """
    url = GEOCODE_ADDRESS_URL.format(address=quote(text, safe=""))
    try:
        resp = requests.get(url, params={"regionCode": region.upper()},
                            headers=_headers(), timeout=TIMEOUT)
        if resp.ok:
            results = resp.json().get("results", [])
            if results:
                loc = results[0].get("location", {})
                lat, lng = loc.get("latitude"), loc.get("longitude")
                if lat is not None and lng is not None:
                    return {"latitude": lat, "longitude": lng}
        resp = requests.post(PLACES_SEARCH_URL, json={"textQuery": text, "pageSize": 1},
                             headers=_headers("places.location"), timeout=TIMEOUT)
        if resp.ok:
            places = resp.json().get("places", [])
            if places:
                loc = places[0].get("location", {})
                lat, lng = loc.get("latitude"), loc.get("longitude")
                if lat is not None and lng is not None:
                    return {"latitude": lat, "longitude": lng}
    except Exception:  # noqa: BLE001 — network/transport
        pass
    return None


def _place_out(p: Dict[str, Any]) -> Dict[str, Any]:
    loc = p.get("location", {})
    out: Dict[str, Any] = {
        "place_id": p.get("id"),
        "name": (p.get("displayName") or {}).get("text"),
        "address": p.get("formattedAddress"),
        "lat": loc.get("latitude"), "lon": loc.get("longitude"),
        "types": p.get("types", []),
    }
    if "rating" in p:
        out["rating"] = p["rating"]
        out["rating_count"] = p.get("userRatingCount")
    open_now = (p.get("currentOpeningHours") or {}).get("openNow")
    if open_now is not None:
        out["open_now"] = open_now
    return out


mcp = FastMCP(
    "google_maps",
    instructions=("Find businesses and points of interest, get place details, convert "
                  "addresses to coordinates (and back), and compute directions/ETA "
                  "between two named places."),
    host=HOST,
    port=PORT,
    stateless_http=True,
)


# ── Tools (names kept identical to the replaced upstream server) ──────────────

@mcp.tool()
def maps_search_places(query: str, max_results: int = 5,
                       near_lat: Optional[float] = None,
                       near_lon: Optional[float] = None,
                       radius_m: int = 1000) -> Dict[str, Any]:
    """Search businesses and points of interest by free-text query (Places API).

    Call this when the user asks to FIND a type of place — "bike shop near the
    castle", "café with wifi in Karlsruhe", "swimming pool nearby". Include the
    city or area in the query for accurate results. For a plain place/area NAME
    that just needs coordinates for route planning, prefer routes__geocode.

    To find places ALONG A PLANNED ROUTE, pass a waypoint from the route's track
    as near_lat/near_lon (e.g. the midpoint) — results are then biased to that
    spot instead of the city centre. Repeat with different waypoints to cover a
    long route.

    Args:
        query: Free-text search, e.g. "Fahrradladen Karlsruhe Oststadt".
        max_results: How many places to return (1-20, default 5).
        near_lat, near_lon: Optional coordinate to search around (both required
            together) — use route waypoints for "on the route" searches.
        radius_m: Bias radius around near_lat/near_lon in metres (50–50000,
            default 1000).

    Returns:
        {places: [{place_id, name, address, lat, lon, types, rating?, open_now?}]}
        — pass a place_id to maps_place_details for opening hours/phone/website.
    """
    q = (query or "").strip()
    if not q:
        return {"error": "empty query"}
    body: Dict[str, Any] = {"textQuery": q, "pageSize": max(1, min(int(max_results or 5), 20))}
    if (near_lat is None) != (near_lon is None):
        return {"error": "near_lat and near_lon must be given together"}
    if near_lat is not None and near_lon is not None:
        body["locationBias"] = {"circle": {
            "center": {"latitude": float(near_lat), "longitude": float(near_lon)},
            "radius": float(max(50, min(int(radius_m or 1000), 50000))),
        }}
    resp = None
    for mask in (_SEARCH_MASK_FULL, _SEARCH_MASK_BASIC):
        resp = requests.post(PLACES_SEARCH_URL, json=body, headers=_headers(mask), timeout=TIMEOUT)
        if resp.ok:
            places = resp.json().get("places", [])
            return {"query": q, "places": [_place_out(p) for p in places]}
        if resp.status_code not in (400, 403):  # only mask problems warrant a retry
            break
    return _err(resp, "Place search")


@mcp.tool()
def maps_search_along_route(query: str, anchors: List[Dict[str, float]],
                            max_detour_m: int = 400, max_results: int = 5) -> Dict[str, Any]:
    """Find places that lie ON a planned route (within max_detour_m of its track).

    Call this — NOT maps_search_places — whenever the user wants a stop ALONG a
    planned route ("a café along the route"). Pass the route result's
    ``poi_anchors`` list (from routes__plan_route / plan_circular_route /
    plan_park_loop) VERBATIM as ``anchors``; never invent coordinates. Only
    places within ``max_detour_m`` of the track are returned — an empty result
    means there is no such place directly on the route: say so honestly instead
    of substituting a place elsewhere in town.

    Args:
        query: What to find, e.g. "café", "bakery", "drinking fountain".
        anchors: The route's poi_anchors — [{"km": …, "lat": …, "lon": …}, …].
        max_detour_m: Max distance off the track in metres (50–2000, default 400).
        max_results: Max places to return (default 5).

    Returns:
        {places: [{place_id, name, address, lat, lon, types, rating?, open_now?,
                   near_km, detour_m}]} sorted by detour_m. ``near_km`` is the km
        mark on the route where the place is closest; quote it in the answer.
    """
    q = (query or "").strip()
    if not q:
        return {"error": "empty query"}
    pts = [(float(a["km"]), float(a["lat"]), float(a["lon"]))
           for a in (anchors or [])
           if isinstance(a, dict) and all(k in a for k in ("km", "lat", "lon"))]
    if not pts:
        return {"error": "anchors required — pass the route result's poi_anchors list verbatim"}
    detour = max(50, min(int(max_detour_m or 400), 2000))

    found: Dict[str, Dict[str, Any]] = {}
    resp = None
    for _, alat, alon in pts[:8]:
        body = {"textQuery": q, "pageSize": 10, "locationBias": {"circle": {
            "center": {"latitude": alat, "longitude": alon}, "radius": float(detour)}}}
        for mask in (_SEARCH_MASK_FULL, _SEARCH_MASK_BASIC):
            resp = requests.post(PLACES_SEARCH_URL, json=body, headers=_headers(mask), timeout=TIMEOUT)
            if resp.ok:
                for p in resp.json().get("places", []):
                    out = _place_out(p)
                    if out.get("place_id") and out.get("lat") is not None:
                        found.setdefault(out["place_id"], out)
                break
            if resp.status_code not in (400, 403):
                break
    if not found and resp is not None and not resp.ok:
        return _err(resp, "Along-route search")

    # Hard proximity filter — the guarantee this tool exists for.
    results = []
    for out in found.values():
        best = min(pts, key=lambda t: _haversine_m(out["lat"], out["lon"], t[1], t[2]))
        d = _haversine_m(out["lat"], out["lon"], best[1], best[2])
        if d <= detour:
            out["near_km"] = best[0]
            out["detour_m"] = round(d)
            results.append(out)
    results.sort(key=lambda r: r["detour_m"])
    return {"query": q, "max_detour_m": detour,
            "places": results[:max(1, min(int(max_results or 5), 20))]}


@mcp.tool()
def maps_place_details(place_id: str) -> Dict[str, Any]:
    """Details for ONE place found via maps_search_places: opening hours, phone, website.

    Call this after a search when the user wants specifics of a place — is it open,
    how to contact it, its website. Needs the place_id from a previous search result.

    Args:
        place_id: The Places API id, e.g. "ChIJN1t_tDeuEmsRUsoyG83frY4".
    """
    pid = (place_id or "").strip()
    if not pid:
        return {"error": "empty place_id"}
    url = PLACES_DETAILS_URL.format(place_id=quote(pid, safe=""))
    resp = None
    for mask in (_DETAILS_MASK_FULL, _DETAILS_MASK_BASIC):
        resp = requests.get(url, headers=_headers(mask), timeout=TIMEOUT)
        if resp.ok:
            p = resp.json()
            out = _place_out(p)
            out.update({
                "phone": p.get("internationalPhoneNumber"),
                "website": p.get("websiteUri"),
                "opening_hours": (p.get("regularOpeningHours") or {}).get("weekdayDescriptions"),
                "price_level": p.get("priceLevel"),
            })
            return out
        if resp.status_code not in (400, 403):
            break
    return _err(resp, "Place details")


@mcp.tool()
def maps_geocode(address: str, region: str = "de") -> Dict[str, Any]:
    """Convert an address or place name to coordinates (Geocoding API).

    Call this to turn a street address into lat/lon. For named places/areas that
    feed route planning, routes__geocode is the default; this is the Google-side
    equivalent when that server is unavailable.

    Args:
        address: Address or place name, e.g. "Kaiserstraße 12 Karlsruhe".
        region: Two-letter region bias for ambiguous names (default "de").
    """
    a = (address or "").strip()
    if not a:
        return {"error": "empty address"}
    url = GEOCODE_ADDRESS_URL.format(address=quote(a, safe=""))
    params = {"regionCode": (region or "").upper()} if region else {}
    resp = requests.get(url, params=params, headers=_headers(), timeout=TIMEOUT)
    if not resp.ok:
        return _err(resp, "Geocoding")
    results = resp.json().get("results", [])
    if not results:
        return {"error": "no results", "address": a}
    top = results[0]
    loc = top.get("location", {})
    return {
        "address": a,
        "lat": loc.get("latitude"), "lon": loc.get("longitude"),
        "formatted_address": top.get("formattedAddress"),
        "place_id": top.get("placeId"),
        "granularity": top.get("granularity"),
        "types": top.get("types", []),
    }


@mcp.tool()
def maps_reverse_geocode(latitude: float, longitude: float) -> Dict[str, Any]:
    """Convert coordinates to the nearest address (reverse geocoding).

    Call this when the user has a GPS point (e.g. from an activity) and wants to
    know WHERE that is — street, neighbourhood, city.

    Args:
        latitude, longitude: The coordinate to resolve.
    """
    url = GEOCODE_LOCATION_URL.format(lat=latitude, lng=longitude)
    resp = requests.get(url, headers=_headers(), timeout=TIMEOUT)
    if not resp.ok:
        return _err(resp, "Reverse geocoding")
    results = resp.json().get("results", [])
    if not results:
        return {"error": "no results", "lat": latitude, "lon": longitude}
    top = results[0]
    return {
        "lat": latitude, "lon": longitude,
        "formatted_address": top.get("formattedAddress"),
        "place_id": top.get("placeId"),
        "granularity": top.get("granularity"),
        "types": top.get("types", []),
    }


@mcp.tool()
def maps_directions(origin: str, destination: str, mode: str = "walking") -> Dict[str, Any]:
    """Directions and ETA between two named places/addresses (Routes API).

    Call this when the user asks HOW to get from A to B or how long it takes —
    including by car or public transit. For planning a sports route (running/
    cycling track with elevation profile), prefer the routes__* tools instead.

    Args:
        origin: Start address or place name, e.g. "Karlsruhe Hbf".
        destination: End address or place name, e.g. "Schloss Karlsruhe".
        mode: walking | driving | bicycling | transit (default walking).
    """
    o, d = (origin or "").strip(), (destination or "").strip()
    if not o or not d:
        return {"error": "origin and destination are required"}
    travel_mode = _MODES.get((mode or "walking").lower().strip(), "WALK")
    # The Routes API rejects fuzzy place names as address waypoints ("KIT Campus
    # Süd" → Address not found) even though Geocoding resolves them fine — so
    # resolve both endpoints to coordinates first and route between latLngs.
    o_pt, d_pt = _geocode_point(o), _geocode_point(d)
    if o_pt is None:
        return {"error": f"could not locate origin '{o}' — try a more specific name/address"}
    if d_pt is None:
        return {"error": f"could not locate destination '{d}' — try a more specific name/address"}
    body: Dict[str, Any] = {
        "origin": {"location": {"latLng": o_pt}},
        "destination": {"location": {"latLng": d_pt}},
        "travelMode": travel_mode,
        "computeAlternativeRoutes": False,
    }
    if travel_mode == "DRIVE":
        body["routingPreference"] = "TRAFFIC_AWARE"
    mask = ("routes.duration,routes.distanceMeters,"
            "routes.legs.steps.navigationInstruction.instructions,"
            "routes.legs.steps.distanceMeters")
    resp = requests.post(ROUTES_URL, json=body, headers=_headers(mask), timeout=TIMEOUT)
    if not resp.ok:
        return _err(resp, "Directions request")
    routes = resp.json().get("routes", [])
    if not routes:
        return {"error": "no route found", "origin": o, "destination": d}
    route = routes[0]
    seconds = int(str(route.get("duration", "0s")).rstrip("s") or 0)
    steps: List[Dict[str, Any]] = []
    for leg in route.get("legs", []):
        for s in leg.get("steps", []):
            instr = (s.get("navigationInstruction") or {}).get("instructions")
            if instr:
                steps.append({"instruction": instr,
                              "distance_m": s.get("distanceMeters")})
    return {
        "origin": o, "destination": d, "mode": travel_mode,
        "distance_km": round(route.get("distanceMeters", 0) / 1000, 2),
        "duration_min": round(seconds / 60, 1),
        "steps": steps,
    }


if __name__ == "__main__":
    _check_prereqs()
    mcp.run(transport="streamable-http")
