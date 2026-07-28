"""`core/route_export.py` turns a planned route into artifacts a phone can open.

Pure stdlib, no network — so it is cheap to check properly. What matters here is
that the GPX is *valid XML with the exact planned points* (a corrupt file fails
silently inside Komoot or Garmin Connect, long after we could notice) and that
the Maps link keeps start and finish while thinning only the middle.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import route_export as rx  # noqa: E402 — needs the sys.path line above

_GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def _route(n: int = 3, profile: str = "foot-walking") -> dict:
    """A `trace["route_data"]` payload as the orchestrator assembles it.

    Note the tool name is the BARE one — core.agent_trace.route_data() strips the
    ``server__`` prefix before storing it, and route_export matches on that.
    """
    return {
        "tool": "plan_route",
        "data": {
            "profile": profile,
            "waypoints": [{"lat": round(49.0 + i / 1000, 3), "lon": round(8.4 + i / 1000, 3),
                           "ele_m": 100 + i} for i in range(n)],
        },
    }


# ── Google Maps link ──────────────────────────────────────────────────────────

def test_maps_url_keeps_start_and_finish():
    url = rx.google_maps_url(_route(3))
    q = parse_qs(urlparse(url).query)

    assert q["origin"] == ["49.0,8.4"]
    assert q["destination"] == ["49.002,8.402"]
    assert q["waypoints"] == ["49.001,8.401"]


def test_maps_url_thins_long_routes_to_the_waypoint_cap():
    """Google caps intermediate stops, so a 200-point track must be downsampled —
    but the endpoints have to survive it, or the link goes somewhere else."""
    url = rx.google_maps_url(_route(200))
    q = parse_qs(urlparse(url).query)

    assert len(q["waypoints"][0].split("|")) <= rx._GMAPS_MAX_WAYPOINTS
    assert q["origin"] == ["49.0,8.4"]
    assert q["destination"] == ["49.199,8.599"]


def test_maps_url_picks_the_travel_mode_from_the_profile():
    assert "travelmode=bicycling" in rx.google_maps_url(_route(profile="cycling-regular"))
    assert "travelmode=walking" in rx.google_maps_url(_route(profile="foot-walking"))


def test_maps_url_is_none_when_there_is_no_route():
    assert rx.google_maps_url(None) is None
    assert rx.google_maps_url(_route(1)) is None                      # a single point is not a route
    assert rx.google_maps_url({"tool": "explore_trails", "data": {}}) is None


# ── GPX ───────────────────────────────────────────────────────────────────────

def test_gpx_is_valid_and_carries_every_planned_point():
    gpx = rx.route_gpx(_route(5))
    root = ET.fromstring(gpx.decode("utf-8"))          # raises if the XML is malformed

    pts = root.findall(".//gpx:trkpt", _GPX_NS)
    assert len(pts) == 5
    assert pts[0].attrib == {"lat": "49.0", "lon": "8.4"}
    # Elevation is carried through when the router supplied it.
    assert pts[0].find("gpx:ele", _GPX_NS).text == "100"
    assert root.get("version") == "1.1"


def test_gpx_track_name_is_escaped():
    """The name reaches an XML attribute; an apostrophe or & must not break the file."""
    gpx = rx.route_gpx(_route(2), name="Aaron & Co <Trainingslauf>")
    root = ET.fromstring(gpx.decode("utf-8"))
    assert root.find(".//gpx:name", _GPX_NS).text == "Aaron & Co <Trainingslauf>"


def test_gpx_exports_trails_as_separate_segments():
    """explore_trails returns several tracks; each must stay its own segment
    rather than being joined into one implausible line."""
    data = {"tool": "explore_trails",
            "data": {"trails": [{"segments": [[[8.4, 49.0], [8.41, 49.01]]]},
                                {"segments": [[[8.5, 49.1], [8.51, 49.11]]]}]}}
    root = ET.fromstring(rx.route_gpx(data).decode("utf-8"))

    segs = root.findall(".//gpx:trkseg", _GPX_NS)
    assert len(segs) == 2
    # Trails arrive as [lon, lat] and must come out the other way round.
    assert segs[0].findall("gpx:trkpt", _GPX_NS)[0].attrib == {"lat": "49.0", "lon": "8.4"}


def test_gpx_is_none_when_there_is_nothing_to_export():
    assert rx.route_gpx(None) is None
    assert rx.route_gpx(_route(1)) is None
    assert rx.route_gpx({"tool": "explore_trails", "data": {"trails": []}}) is None
