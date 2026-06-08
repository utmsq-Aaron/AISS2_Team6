#!/usr/bin/env python3
"""
Routes MCP Server — JSON-RPC interface for route planning and trail discovery.

Provides 5 tools powered by OpenRouteService (ORS) and the OpenStreetMap
Overpass API. No paid plan required: the ORS free tier (2 000 req/day) is
more than sufficient for personal use.

Supported profiles
------------------
cycling-regular     Road / gravel cycling
cycling-mountain    Mountain biking
foot-hiking         Hiking / trekking
foot-walking        Casual walking
running             Running

Prerequisites: set ORS_API_KEY in .env
"""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from servers._base_server import BaseMCPServer

load_dotenv()

ORS_BASE = "https://api.openrouteservice.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

VALID_PROFILES = {
    "cycling-regular",
    "cycling-mountain",
    "foot-hiking",
    "foot-walking",
}

# Sport-type strings Strava uses → nearest ORS profile
# Note: ORS has no "running" profile — foot-walking is the closest
STRAVA_TO_PROFILE: Dict[str, str] = {
    "ride":             "cycling-regular",
    "virtualride":      "cycling-regular",
    "mountainbikeride": "cycling-mountain",
    "gravel_ride":      "cycling-regular",
    "run":              "foot-walking",
    "virtualrun":       "foot-walking",
    "running":          "foot-walking",
    "hike":             "foot-hiking",
    "walk":             "foot-walking",
    "alpineski":        "foot-hiking",
    "nordicski":        "foot-hiking",
}


def _api_key() -> str:
    key = os.getenv("ORS_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ORS_API_KEY not set. Add it to your .env file.\n"
            "Get a free key at https://openrouteservice.org/dev/#/signup"
        )
    return key


def _headers() -> Dict[str, str]:
    return {
        "Authorization": _api_key(),
        "Content-Type": "application/json",
    }


def _normalise_profile(profile: Optional[str]) -> str:
    if not profile:
        return "foot-hiking"
    p = profile.lower().strip()
    # Direct match
    if p in VALID_PROFILES:
        return p
    # Strava type alias
    if p in STRAVA_TO_PROFILE:
        return STRAVA_TO_PROFILE[p]
    # Partial match (e.g. "cycling" → "cycling-regular", "hiking" → "foot-hiking")
    for v in VALID_PROFILES:
        if p in v or v in p:
            return v
    return "foot-hiking"


def _elevation_stats(elevations: List[float]) -> Dict[str, Any]:
    if not elevations:
        return {"gain_m": None, "loss_m": None, "min_m": None, "max_m": None}
    gain = loss = 0.0
    for i in range(1, len(elevations)):
        diff = elevations[i] - elevations[i - 1]
        if diff > 0:
            gain += diff
        else:
            loss += abs(diff)
    return {
        "gain_m":  round(gain, 1),
        "loss_m":  round(loss, 1),
        "min_m":   round(min(elevations), 1),
        "max_m":   round(max(elevations), 1),
    }


# ── MCP Server ────────────────────────────────────────────────────────────────

class RoutesMCPServer(BaseMCPServer):
    """Route planning and trail discovery via OpenRouteService + OpenStreetMap."""

    def list_tools(self) -> list:
        return [
            {
                "name": "plan_route",
                "description": (
                    "Plan a route from A to B using OpenRouteService. Returns distance, "
                    "estimated duration, elevation gain/loss, a simplified list of waypoints "
                    "(lat/lon/elevation), and turn-by-turn instructions. "
                    "Accepts a Strava sport type (Run, Ride, Hike …) or an ORS profile directly. "
                    "Use this when the user asks to plan a specific point-to-point route."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "start_lat":  {"type": "number", "description": "Start latitude"},
                        "start_lon":  {"type": "number", "description": "Start longitude"},
                        "end_lat":    {"type": "number", "description": "End latitude"},
                        "end_lon":    {"type": "number", "description": "End longitude"},
                        "profile":    {
                            "type": "string",
                            "description": (
                                "ORS profile or Strava sport type. "
                                "ORS: cycling-regular, cycling-mountain, foot-hiking, foot-walking, running. "
                                "Strava aliases also accepted: Ride, Run, Hike, Walk, MountainBikeRide."
                            ),
                        },
                        "waypoints": {
                            "type": "array",
                            "description": "Optional intermediate waypoints [[lat, lon], ...]",
                            "items": {"type": "array", "items": {"type": "number"}},
                        },
                        "simplify_points": {
                            "type": "integer",
                            "description": "Max number of waypoints to return (default 100, max 500). Lower = faster response.",
                        },
                    },
                    "required": ["start_lat", "start_lon", "end_lat", "end_lon"],
                },
            },
            {
                "name": "plan_circular_route",
                "description": (
                    "Plan a circular (loop) route that starts and ends at the same point "
                    "with a target distance. Ideal for 'plan a 30 km bike loop from my home' "
                    "or 'find a 10 km running loop near where I usually train'. "
                    "Uses the ORS Isochrones API to find a reachable area, then builds a loop."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "lat":      {"type": "number", "description": "Start/end latitude"},
                        "lon":      {"type": "number", "description": "Start/end longitude"},
                        "distance_km": {"type": "number", "description": "Target loop distance in km"},
                        "profile":  {
                            "type": "string",
                            "description": "ORS profile or Strava sport type (default: foot-hiking)",
                        },
                    },
                    "required": ["lat", "lon", "distance_km"],
                },
            },
            {
                "name": "get_elevation_profile",
                "description": (
                    "Get the elevation profile for a list of coordinates (e.g. from a Strava GPS stream). "
                    "Returns elevation at each point plus total gain, loss, min and max. "
                    "Use this to analyse the hilliness of a past Strava activity or to enrich "
                    "a set of coordinates with altitude data."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "coordinates": {
                            "type": "array",
                            "description": "List of [lat, lon] pairs",
                            "items": {"type": "array", "items": {"type": "number"}},
                        },
                        "format_out": {
                            "type": "string",
                            "description": "Output format: 'encodedpolyline' or 'geojson' (default: geojson)",
                        },
                    },
                    "required": ["coordinates"],
                },
            },
            {
                "name": "explore_trails",
                "description": (
                    "Find hiking, cycling, or running trails near a given location using "
                    "OpenStreetMap data (Overpass API). Returns named trails with distance, "
                    "route type, and bounding box. "
                    "Use this when the user asks 'what trails are near X?' or 'show me cycling "
                    "routes in the area where I hiked last week'."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "lat":        {"type": "number", "description": "Centre latitude"},
                        "lon":        {"type": "number", "description": "Centre longitude"},
                        "radius_km":  {"type": "number", "description": "Search radius in km (default 15, max 50)"},
                        "sport_type": {
                            "type": "string",
                            "description": "One of: hiking, cycling, running, mtb (default: hiking)",
                        },
                        "limit":      {"type": "integer", "description": "Max trails to return (default 10)"},
                    },
                    "required": ["lat", "lon"],
                },
            },
            {
                "name": "get_isochrone",
                "description": (
                    "Calculate how far you can travel from a point within a given time or distance. "
                    "Returns a GeoJSON polygon (reachable area) and a summary. "
                    "Use this for questions like 'what can I reach in 1 hour by bike from my home?' "
                    "or 'show the area I can cover on a 20 km run'."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "lat":         {"type": "number", "description": "Start latitude"},
                        "lon":         {"type": "number", "description": "Start longitude"},
                        "range_type":  {
                            "type": "string",
                            "description": "'time' (seconds) or 'distance' (metres). Default: time.",
                        },
                        "range_value": {
                            "type": "number",
                            "description": "Range value. For time: seconds (e.g. 3600 = 1 hour). For distance: metres (e.g. 20000 = 20 km).",
                        },
                        "profile": {
                            "type": "string",
                            "description": "ORS profile or Strava sport type (default: cycling-regular)",
                        },
                    },
                    "required": ["lat", "lon", "range_value"],
                },
            },
        ]

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        rid = request.get("id", 1)
        try:
            method = request.get("method")
            params = request.get("params", {})

            if method == "initialize":
                return {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"protocolVersion": "simple-mcp-1.0", "capabilities": {"tools": True}},
                }
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": rid, "result": {"tools": self.tools}}
            if method == "tools/call":
                result = await self._dispatch(params.get("name"), params.get("arguments", {}))
                return {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": result}]},
                }
            raise ValueError(f"Unknown method: {method}")

        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -1, "message": str(e)}}

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        print(f"[routes] {tool_name}({json.dumps(args)})", file=sys.stderr)
        handlers = {
            "plan_route":            self._plan_route,
            "plan_circular_route":   self._plan_circular_route,
            "get_elevation_profile": self._get_elevation_profile,
            "explore_trails":        self._explore_trails,
            "get_isochrone":         self._get_isochrone,
        }
        return await handlers[tool_name](args)

    # ── Tool implementations ──────────────────────────────────────────────────

    async def _plan_route(self, args: Dict) -> str:
        profile = _normalise_profile(args.get("profile"))
        simplify = min(int(args.get("simplify_points", 100)), 500)

        # Build coordinate list: [start, *waypoints, end]  — ORS uses [lon, lat]
        coords = [[args["start_lon"], args["start_lat"]]]
        for wp in (args.get("waypoints") or []):
            coords.append([wp[1], wp[0]])  # waypoints given as [lat, lon]
        coords.append([args["end_lon"], args["end_lat"]])

        body = {
            "coordinates": coords,
            "elevation": True,
            "instructions": True,
            "units": "km",
            "geometry_simplify": False,
        }

        resp = requests.post(
            f"{ORS_BASE}/v2/directions/{profile}/geojson",
            headers=_headers(),
            json=body,
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f"ORS directions error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        # GeoJSON response: features[0] contains the route
        feature = data["features"][0]
        props = feature.get("properties", {})
        summary = props.get("summary", {})
        segments = props.get("segments", [])

        # GeoJSON geometry: coordinates are [lon, lat, elevation]
        raw_coords = feature.get("geometry", {}).get("coordinates", [])

        # Simplify: keep every N-th point
        step = max(1, len(raw_coords) // simplify)
        waypoints = [
            {"lat": round(c[1], 6), "lon": round(c[0], 6), "ele_m": round(c[2], 1) if len(c) > 2 else None}
            for c in raw_coords[::step]
        ]

        # Elevation stats from geometry
        elevations = [c[2] for c in raw_coords if len(c) > 2]
        elev_stats = _elevation_stats(elevations)

        # Turn-by-turn steps from first segment
        steps = []
        for seg in segments:
            for step_data in (seg.get("steps") or []):
                instruction = step_data.get("instruction", "")
                dist = step_data.get("distance", 0)
                if instruction:
                    steps.append({"instruction": instruction, "distance_km": round(dist, 2)})

        return json.dumps({
            "profile":          profile,
            "distance_km":      round(summary.get("distance", 0), 2),
            "duration_min":     round(summary.get("duration", 0) / 60, 1),
            "elevation":        elev_stats,
            "waypoints_count":  len(raw_coords),
            "waypoints":        waypoints,
            "instructions":     steps[:50],  # cap at 50 steps
        }, indent=2)

    async def _plan_circular_route(self, args: Dict) -> str:
        profile = _normalise_profile(args.get("profile", "foot-hiking"))
        distance_km = float(args["distance_km"])
        lat, lon = float(args["lat"]), float(args["lon"])

        # ORS circular route via the /directions endpoint with options.round_trip
        body = {
            "coordinates": [[lon, lat]],
            "options": {
                "round_trip": {
                    "length":      distance_km * 1000,  # metres
                    "points":      3,
                    "seed":        1,
                }
            },
            "elevation": True,
            "instructions": False,
            "units": "km",
        }

        resp = requests.post(
            f"{ORS_BASE}/v2/directions/{profile}/geojson",
            headers=_headers(),
            json=body,
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f"ORS circular route error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        feature = data["features"][0]
        props = feature.get("properties", {})
        summary = props.get("summary", {})
        raw_coords = feature.get("geometry", {}).get("coordinates", [])

        # Simplify to ~100 points
        step = max(1, len(raw_coords) // 100)
        waypoints = [
            {"lat": round(c[1], 6), "lon": round(c[0], 6), "ele_m": round(c[2], 1) if len(c) > 2 else None}
            for c in raw_coords[::step]
        ]

        elevations = [c[2] for c in raw_coords if len(c) > 2]
        elev_stats = _elevation_stats(elevations)

        return json.dumps({
            "profile":         profile,
            "target_distance_km": distance_km,
            "actual_distance_km": round(summary.get("distance", 0), 2),
            "duration_min":    round(summary.get("duration", 0) / 60, 1),
            "elevation":       elev_stats,
            "start_lat":       lat,
            "start_lon":       lon,
            "waypoints":       waypoints,
        }, indent=2)

    async def _get_elevation_profile(self, args: Dict) -> str:
        coords = args["coordinates"]  # [[lat, lon], ...]
        if not coords:
            return json.dumps({"error": "No coordinates provided"})

        # ORS elevation expects [lon, lat]
        ors_coords = [[c[1], c[0]] for c in coords]

        body = {
            "format_in":  "geojson",
            "format_out": args.get("format_out", "geojson"),
            "geometry": {
                "coordinates": ors_coords,
                "type": "LineString",
            },
        }

        resp = requests.post(
            f"{ORS_BASE}/elevation/line",
            headers=_headers(),
            json=body,
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f"ORS elevation error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        result_coords = data.get("geometry", {}).get("coordinates", [])

        # Build enriched list: [lat, lon, elevation_m]
        enriched = [
            {"lat": round(c[1], 6), "lon": round(c[0], 6), "ele_m": round(c[2], 1) if len(c) > 2 else None}
            for c in result_coords
        ]
        elevations = [c[2] for c in result_coords if len(c) > 2]
        elev_stats = _elevation_stats(elevations)

        return json.dumps({
            "points":    len(enriched),
            "elevation": elev_stats,
            "profile":   enriched,
        }, indent=2)

    async def _explore_trails(self, args: Dict) -> str:
        lat = float(args["lat"])
        lon = float(args["lon"])
        radius_km = min(float(args.get("radius_km", 15)), 50)
        radius_m = radius_km * 1000
        # page_size: how many trails to return per call (default 5, max 20)
        page_size = min(int(args.get("limit", 5)), 20)
        # offset: skip the first N results to support "load more" pagination
        offset = max(int(args.get("offset", 0)), 0)
        # total to fetch from Overpass = offset + page_size (always from position 0)
        fetch_limit = offset + page_size

        sport = (args.get("sport_type") or "hiking").lower()
        osm_route_map = {
            "hiking":  "hiking",
            "hike":    "hiking",
            "cycling": "bicycle",
            "bike":    "bicycle",
            "ride":    "bicycle",
            "mtb":     "mtb",
            "running": "running",
            "run":     "running",
            "foot":    "hiking",
            "walk":    "foot",
        }
        osm_route = osm_route_map.get(sport, "hiking")

        # out geom <N>; fetches exactly N elements with full GPS geometry
        query = f"""
[out:json][timeout:30];
(
  relation["type"="route"]["route"="{osm_route}"](around:{radius_m},{lat},{lon});
);
out geom {fetch_limit};
"""

        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers={
                "Accept": "application/json",
                "User-Agent": "AISS2-Team6-RoutesMCP/1.0",
            },
            timeout=35,
        )
        if not resp.ok:
            raise RuntimeError(f"Overpass API error {resp.status_code}: {resp.text[:200]}")

        all_elements = resp.json().get("elements", [])
        # total_found is an approximation — we only fetched fetch_limit from Overpass
        total_approx = len(all_elements) + (1 if len(all_elements) == fetch_limit else 0)
        page_elements = all_elements[offset:offset + page_size]

        trails = []
        for el in page_elements:
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("name:en") or tags.get("ref", "Unnamed trail")
            distance_raw = tags.get("distance") or tags.get("length")
            bounds = el.get("bounds", {})

            # Extract GPS segments from way members (out geom; populates member geometry)
            segments: List[List[List[float]]] = []
            for member in el.get("members", []):
                if member.get("type") == "way":
                    geom = member.get("geometry", [])
                    if len(geom) >= 2:
                        # Subsample long segments to keep response lean (max 60 pts each)
                        step = max(1, len(geom) // 60)
                        seg = [[pt["lon"], pt["lat"]] for pt in geom[::step]]
                        segments.append(seg)

            trails.append({
                "osm_id":     el.get("id"),
                "name":       name,
                "route_type": tags.get("route"),
                "distance":   distance_raw,
                "network":    tags.get("network"),
                "difficulty": tags.get("sac_scale") or tags.get("mtb:scale") or tags.get("difficulty"),
                "surface":    tags.get("surface"),
                "description": tags.get("description") or tags.get("description:en"),
                "website":    tags.get("website") or tags.get("url"),
                "segments":   segments,  # [[lon, lat], ...] per way-segment
                "bounds": {
                    "min_lat": bounds.get("minlat"),
                    "min_lon": bounds.get("minlon"),
                    "max_lat": bounds.get("maxlat"),
                    "max_lon": bounds.get("maxlon"),
                } if bounds else None,
            })

        return json.dumps({
            "search_centre": {"lat": lat, "lon": lon},
            "radius_km":     radius_km,
            "sport_type":    osm_route,
            "total_found":   total_approx,
            "offset":        offset,
            "page_size":     page_size,
            "has_more":      len(all_elements) == fetch_limit,
            "trails":        trails,
        }, indent=2)

    async def _get_isochrone(self, args: Dict) -> str:
        profile = _normalise_profile(args.get("profile", "cycling-regular"))
        lat, lon = float(args["lat"]), float(args["lon"])
        range_type = args.get("range_type", "time")
        range_value = float(args["range_value"])

        body = {
            "locations":   [[lon, lat]],
            "range_type":  range_type,
            "range":       [range_value],
            "units":       "km",
            "attributes":  ["area", "reachfactor"],
        }

        resp = requests.post(
            f"{ORS_BASE}/v2/isochrones/{profile}",
            headers=_headers(),
            json=body,
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f"ORS isochrones error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        features = data.get("features", [])
        if not features:
            return json.dumps({"error": "No isochrone data returned"})

        feature = features[0]
        props = feature.get("properties", {})
        geometry = feature.get("geometry", {})

        # Summarise bounding box from polygon coordinates
        poly_coords = geometry.get("coordinates", [[]])[0]
        lats = [c[1] for c in poly_coords]
        lons = [c[0] for c in poly_coords]

        return json.dumps({
            "profile":      profile,
            "range_type":   range_type,
            "range_value":  range_value,
            "range_label":  f"{int(range_value/60)} min" if range_type == "time" else f"{range_value/1000:.1f} km",
            "area_km2":     round(props.get("area", 0), 2),
            "reach_factor": props.get("reachfactor"),
            "centre":       {"lat": lat, "lon": lon},
            "bounding_box": {
                "min_lat": round(min(lats), 5),
                "max_lat": round(max(lats), 5),
                "min_lon": round(min(lons), 5),
                "max_lon": round(max(lons), 5),
            },
            "polygon_points": len(poly_coords),
            "geometry":     geometry,  # full GeoJSON for map rendering
        }, indent=2)


# ── Subprocess entry point ────────────────────────────────────────────────────

async def _main() -> None:
    print("Routes MCP Server started.", file=sys.stderr)
    server = RoutesMCPServer()
    while True:
        try:
            line = input()
            if not line.strip():
                continue
            response = await server.handle_request(json.loads(line))
            print(json.dumps(response))
            sys.stdout.flush()
        except EOFError:
            break
        except Exception as e:
            print(f"Server error: {e}", file=sys.stderr)
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -1, "message": str(e)}}))
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(_main())
