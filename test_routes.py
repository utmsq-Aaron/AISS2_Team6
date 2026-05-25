#!/usr/bin/env python3
"""
Schnelltest für den Routes MCP-Server.

Führe aus dem Projektordner aus:
    python test_routes.py

Benötigt ORS_API_KEY in .env
"""

import asyncio
import json
import sys
from dotenv import load_dotenv

load_dotenv()

# Karlsruhe KIT-Campus als Testkoordinate
START_LAT, START_LON = 49.0130, 8.4093   # KIT Campus Süd
END_LAT,   END_LON   = 49.0069, 8.4037   # Karlsruhe Hauptbahnhof

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def info(msg): print(f"  {YELLOW}→{RESET} {msg}")


async def run_tests():
    from servers.routes import RoutesMCPServer
    server = RoutesMCPServer()

    total = passed = 0

    # ── 1. plan_route ────────────────────────────────────────────────────────
    total += 1
    print(f"\n{BOLD}1. plan_route (cycling-regular){RESET}")
    try:
        result = json.loads(await server._dispatch("plan_route", {
            "start_lat": START_LAT, "start_lon": START_LON,
            "end_lat":   END_LAT,   "end_lon":   END_LON,
            "profile":   "cycling-regular",
        }))
        assert "distance_km" in result, "Kein distance_km"
        ok(f"Distanz: {result['distance_km']} km  |  Dauer: {result['duration_min']} min")
        ok(f"Höhengewinn: {result['elevation']['gain_m']} m  |  Waypoints: {result['waypoints_count']}")
        ok(f"Turn-by-turn Schritte: {len(result.get('instructions', []))}")
        passed += 1
    except Exception as e:
        fail(f"Fehler: {e}")

    # ── 2. plan_route mit Strava-Alias ───────────────────────────────────────
    total += 1
    print(f"\n{BOLD}2. plan_route mit Strava-Alias 'Ride'{RESET}")
    try:
        result = json.loads(await server._dispatch("plan_route", {
            "start_lat": START_LAT, "start_lon": START_LON,
            "end_lat":   END_LAT,   "end_lon":   END_LON,
            "profile":   "Ride",   # Strava-Typ
        }))
        assert result["profile"] == "cycling-regular"
        ok(f"'Ride' → '{result['profile']}'  ✓  (Distanz: {result['distance_km']} km)")
        passed += 1
    except Exception as e:
        fail(f"Fehler: {e}")

    # ── 3. plan_route Wandern ────────────────────────────────────────────────
    total += 1
    print(f"\n{BOLD}3. plan_route (foot-hiking){RESET}")
    try:
        result = json.loads(await server._dispatch("plan_route", {
            "start_lat": START_LAT, "start_lon": START_LON,
            "end_lat":   END_LAT,   "end_lon":   END_LON,
            "profile":   "foot-hiking",
        }))
        ok(f"Distanz: {result['distance_km']} km  |  Dauer: {result['duration_min']} min")
        passed += 1
    except Exception as e:
        fail(f"Fehler: {e}")

    # ── 4. plan_circular_route ───────────────────────────────────────────────
    total += 1
    print(f"\n{BOLD}4. plan_circular_route (10 km Lauf-Loop){RESET}")
    try:
        result = json.loads(await server._dispatch("plan_circular_route", {
            "lat": START_LAT, "lon": START_LON,
            "distance_km": 10,
            "profile": "running",
        }))
        assert "actual_distance_km" in result
        ok(f"Ziel: {result['target_distance_km']} km  →  Tatsächlich: {result['actual_distance_km']} km")
        ok(f"Dauer: {result['duration_min']} min  |  Höhengewinn: {result['elevation']['gain_m']} m")
        passed += 1
    except Exception as e:
        fail(f"Fehler: {e}")

    # ── 5. get_elevation_profile ─────────────────────────────────────────────
    total += 1
    print(f"\n{BOLD}5. get_elevation_profile (simulierter GPS-Track){RESET}")
    try:
        # Simuliert einen Strava GPS-Stream (5 Punkte auf der Teststrecke)
        coords = [
            [START_LAT + i * 0.002, START_LON + i * 0.001]
            for i in range(5)
        ]
        result = json.loads(await server._dispatch("get_elevation_profile", {
            "coordinates": coords,
        }))
        assert result["points"] == len(coords)
        elev = result["elevation"]
        ok(f"Punkte: {result['points']}  |  Höhenbereich: {elev['min_m']}–{elev['max_m']} m")
        ok(f"Gewinn: {elev['gain_m']} m  |  Verlust: {elev['loss_m']} m")
        passed += 1
    except Exception as e:
        fail(f"Fehler: {e}")

    # ── 6. explore_trails ────────────────────────────────────────────────────
    total += 1
    print(f"\n{BOLD}6. explore_trails (Wanderwege, 20 km Umkreis){RESET}")
    try:
        result = json.loads(await server._dispatch("explore_trails", {
            "lat": START_LAT, "lon": START_LON,
            "radius_km": 20,
            "sport_type": "hiking",
            
            "limit": 5,
        }))
        trails = result.get("trails", [])
        ok(f"Gefunden: {result['total_found']} Trails  |  Angezeigt: {len(trails)}")
        for t in trails[:3]:
            info(f"{t['name']}  ({t.get('distance') or '?'} km, Typ: {t.get('route_type')})")
        passed += 1
    except Exception as e:
        fail(f"Fehler: {e}")

    # ── 7. get_isochrone ─────────────────────────────────────────────────────
    total += 1
    print(f"\n{BOLD}7. get_isochrone (30 min Fahrrad){RESET}")
    try:
        result = json.loads(await server._dispatch("get_isochrone", {
            "lat": START_LAT, "lon": START_LON,
            "range_type": "time",
            "range_value": 1800,  # 30 Minuten
            "profile": "cycling-regular",
        }))
        ok(f"Erreichbare Fläche: {result['area_km2']} km²")
        bb = result["bounding_box"]
        ok(f"Bounding Box: {bb['min_lat']:.3f},{bb['min_lon']:.3f} → {bb['max_lat']:.3f},{bb['max_lon']:.3f}")
        passed += 1
    except Exception as e:
        fail(f"Fehler: {e}")

    # ── Ergebnis ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    color = GREEN if passed == total else (YELLOW if passed > 0 else RED)
    print(f"{color}{BOLD}{passed}/{total} Tests bestanden{RESET}\n")
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
