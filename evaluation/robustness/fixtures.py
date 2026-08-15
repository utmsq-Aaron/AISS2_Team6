"""Argument fixtures for the technical-robustness sweep — **default-deny**.

A tool is exercised by ``run_robustness.py`` **only** if it has an explicit entry
in :data:`FIXTURES`. Everything else the live inventory reports is recorded as
*skipped*, with a reason from :data:`SKIP` (or the auto-reason ``"no fixture"``
for a tool that appeared after this file was written).

Three layers keep the sweep read-only:

1. *Default-deny* — no fixture, no call. Ever.
2. :func:`write_path_reason` — a name-based veto over every mutating verb
   (create/add/set/update/delete/record/send/launch/schedule/book/export/write/
   post/mark/complete/rescaffold/import), over the whole ``telegram`` server, and
   over the whole ``flythrough`` server (its one tool starts a headless render).
   The runner consults it for every call, so a fixture alone is not sufficient.
3. An import-time self-check that raises if 1 and 2 ever disagree — i.e. if a
   fixture is added for a tool the veto forbids.

Argument values match each ``@mcp.tool()`` signature in ``servers/*_mcp.py``
(cross-checked against the ``inputSchema`` the running servers advertise) and are
deliberately small: Karlsruhe coordinates (the stack's home city — see
``servers/weather_mcp.py``), short day/week windows, low result limits.

Like the rest of ``evaluation/``, this module *imports* ``core`` and is never
imported by it.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from core.config import SEP

# ── Constants used across the fixtures ────────────────────────────────────────

# Karlsruhe city centre — the home location the weather server defaults to.
KARLSRUHE_LAT = 49.0069
KARLSRUHE_LON = 8.4037
# Karlsruhe palace, ~1.5 km north — a short, always-routable A→B leg.
PALACE_LAT = 49.0134
PALACE_LON = 8.4044

# The oversized-input probe: 10k characters in a single string argument.
OVERSIZED = "x" * 10_000


def _days_ago(n: int) -> str:
    """``YYYY-MM-DD``, n days before today (the format every server expects)."""
    return (_dt.date.today() - _dt.timedelta(days=n)).isoformat()


def _rfc3339(days_from_now: int) -> str:
    """UTC RFC3339 timestamp, n days from now (Google Calendar's format)."""
    ts = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=days_from_now)
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ── The write-path veto (independent of FIXTURES) ─────────────────────────────

# A bare tool name is vetoed when any underscore-separated segment is one of
# these verbs. Segment matching (not substring) so a legitimate read tool is
# never vetoed by an accidental infix.
WRITE_VERBS = frozenset({
    "create", "add", "set", "update", "delete", "record", "send", "launch",
    "schedule", "book", "export", "write", "post", "mark", "complete",
    "rescaffold", "import",
})

# Whole servers the automated suite never touches.
BANNED_SERVERS: Dict[str, str] = {
    "telegram": "messaging side effects — never called by the automated suite",
    "flythrough": "starts a headless render — never called by the automated suite",
}


def write_path_reason(full_name: str) -> Optional[str]:
    """Reason this ``server__tool`` must never be called, or ``None`` if it may.

    The runner calls this for *every* candidate, so it vetoes even a tool that
    (wrongly) carries a fixture.
    """
    server, _, bare = full_name.partition(SEP)
    banned = BANNED_SERVERS.get(server)
    if banned:
        return banned
    hits = sorted(WRITE_VERBS.intersection(bare.lower().split("_")))
    if hits:
        return f"write path ('{hits[0]}'), excluded from automated sweep"
    return None


# ── Fixtures: full tool name → {"args": …, "malformed": [ …, … ]} ─────────────
#
# "args"      — one set of valid arguments (the sweep repeats this call).
# "malformed" — hostile variants: missing required arg, wrong type,
#               out-of-domain value, oversized string, unknown extra arg.

FIXTURES: Dict[str, Dict[str, Any]] = {

    # ── weather (all four tools; free public API) ──────────────────────────────
    "weather__get_current_weather": {
        "args": {},
        "malformed": [
            {"unexpected_arg": "boom"},                 # unknown arg on a no-arg tool
            {"days": 3},                                # another tool's argument
        ],
    },
    "weather__get_weather_forecast": {
        "args": {"days": 3},
        "malformed": [
            {"days": "seven"},                          # wrong type (str for int)
            {"days": -5},                               # out of domain
            {"days": 3, "date": "2999-99-99"},          # unparseable date
        ],
    },
    "weather__get_pollen_levels": {"args": {}},
    "weather__get_uv_index": {"args": {}},

    # ── routes (all seven tools; ORS + Overpass + Google Geocoding) ────────────
    "routes__geocode": {
        "args": {"query": "Schlossgarten, Karlsruhe", "region": "de"},
        "malformed": [
            {},                                         # missing required 'query'
            {"query": 12345, "region": "de"},           # wrong type (int for str)
            {"query": OVERSIZED, "region": "de"},       # 10k-char string
        ],
    },
    "routes__plan_route": {
        "args": {
            "start_lat": KARLSRUHE_LAT, "start_lon": KARLSRUHE_LON,
            "end_lat": PALACE_LAT, "end_lon": PALACE_LON,
            "profile": "foot-walking", "simplify_points": 50,
        },
    },
    "routes__plan_circular_route": {
        "args": {"lat": KARLSRUHE_LAT, "lon": KARLSRUHE_LON,
                 "distance_km": 5.0, "profile": "foot-walking"},
        "malformed": [
            {"lat": KARLSRUHE_LAT, "lon": KARLSRUHE_LON},          # missing distance_km
            {"lat": 999.0, "lon": KARLSRUHE_LON, "distance_km": 5.0},   # out of domain
            {"lat": "north", "lon": KARLSRUHE_LON, "distance_km": 5.0},  # wrong type
        ],
    },
    "routes__plan_park_loop": {
        "args": {"area": "Schlossgarten Karlsruhe", "distance_km": 2.0,
                 "profile": "foot-walking"},
    },
    "routes__get_elevation_profile": {
        "args": {
            "coordinates": [[KARLSRUHE_LAT, KARLSRUHE_LON],
                            [49.0100, 8.4050],
                            [PALACE_LAT, PALACE_LON]],
            "format_out": "geojson",
        },
        "malformed": [
            {"coordinates": []},                        # empty (documented error path)
            {"coordinates": "49.0069,8.4037"},          # wrong type (str for array)
            {"coordinates": [[KARLSRUHE_LAT]]},         # malformed pair (no lon)
        ],
    },
    "routes__explore_trails": {
        "args": {"lat": KARLSRUHE_LAT, "lon": KARLSRUHE_LON,
                 "radius_km": 5.0, "sport_type": "hiking", "limit": 3},
        "malformed": [
            {"lat": KARLSRUHE_LAT},                     # missing required 'lon'
            {"lat": 999.0, "lon": 999.0, "limit": 1},   # out-of-domain coordinate
            {"lat": KARLSRUHE_LAT, "lon": KARLSRUHE_LON, "radius_km": "far", "limit": 1},
        ],
    },
    "routes__get_isochrone": {
        "args": {"lat": KARLSRUHE_LAT, "lon": KARLSRUHE_LON, "range_value": 600,
                 "range_type": "time", "profile": "cycling-regular"},
        "malformed": [
            {"lat": KARLSRUHE_LAT, "lon": KARLSRUHE_LON},          # missing range_value
            {"lat": KARLSRUHE_LAT, "lon": KARLSRUHE_LON, "range_value": -600},
            {"lat": KARLSRUHE_LAT, "lon": KARLSRUHE_LON,
             "range_value": 600, "range_type": "parsecs"},         # bad enum
        ],
    },

    # ── strava (read-only tools; the athlete's own history) ────────────────────
    "strava__get_activities": {
        "args": {"limit": 5},
        "malformed": [
            {"limit": "many"},                          # wrong type
            {"limit": -10},                             # out of domain
            {"limit": 3, "sport_type": OVERSIZED},      # 10k-char filter
        ],
    },
    "strava__search_activities": {"args": {"keyword": "run", "limit": 5}},
    "strava__get_activity_stats": {"args": {}},
    "strava__get_athlete_profile": {"args": {}},
    "strava__get_gear_info": {"args": {}},
    "strava__get_training_trends": {"args": {"weeks": 4}},
    "strava__get_personal_bests": {"args": {"sport_type": "Run"}},
    "strava__get_yearly_breakdown": {"args": {}},
    "strava__analyze_performance_trends": {"args": {"sport_type": "Run", "limit": 10}},
    "strava__get_training_load": {
        "args": {"weeks": 4},
        "malformed": [
            {"weeks": "sixteen"},                       # wrong type
            {"weeks": 0},                               # degenerate window
            {"weeks": -12},                             # out of domain
        ],
    },

    # ── garmin (read-only health/wellness tools) ──────────────────────────────
    "garmin__get_garmin_activities": {"args": {"limit": 5}},
    "garmin__get_garmin_daily_health": {"args": {"date": _days_ago(1)}},
    "garmin__get_garmin_heart_rate_timeline": {"args": {"date": _days_ago(1)}},
    "garmin__get_garmin_sleep": {
        "args": {"date": _days_ago(1)},
        "malformed": [
            {"date": "yesterday"},                      # unparseable date
            {"date": 20250101},                         # wrong type (int for str)
            {"date": OVERSIZED},                        # 10k-char string
        ],
    },
    "garmin__get_garmin_body_battery": {
        "args": {"start_date": _days_ago(3), "end_date": _days_ago(1)},
    },
    "garmin__get_garmin_hrv_status": {"args": {"date": _days_ago(1)}},
    "garmin__get_garmin_training_metrics": {"args": {"date": _days_ago(1)}},
    "garmin__get_garmin_wellness_trends": {
        "args": {"days": 7},
        "malformed": [
            {"days": "many"},                           # wrong type
            {"days": -5},                               # out of domain
            {"start_date": "not-a-date", "end_date": _days_ago(1)},
        ],
    },
    "garmin__get_garmin_steps_timeline": {"args": {"date": _days_ago(1)}},
    "garmin__get_garmin_stress_timeline": {"args": {"date": _days_ago(1)}},
    "garmin__get_garmin_body_composition": {
        "args": {"start_date": _days_ago(7), "end_date": _days_ago(1)},
    },

    # ── calendar (read-only pair only — no event is ever written) ─────────────
    "calendar__list_calendars": {"args": {"max_results": 10}},
    "calendar__list_events": {
        "args": {"time_min": _rfc3339(0), "time_max": _rfc3339(7),
                 "calendar_id": "primary", "max_results": 10},
        "malformed": [
            {"time_min": "not-a-timestamp", "max_results": 5},   # unparseable window
            {"max_results": -3},                                 # out of domain
            {"query": OVERSIZED, "max_results": 2},              # 10k-char search term
        ],
    },

    # ── athlete (the two read-only tools of the structured athlete store) ─────
    "athlete__get_athlete_overview": {
        "args": {},
        "malformed": [
            {"unexpected_arg": "boom"},                 # unknown arg on a no-arg tool
            {"days": 7},                                # another tool's argument
        ],
    },
    "athlete__get_plan": {"args": {}},
}


# ── Skip list: discovered but deliberately not exercised ──────────────────────
# Every live tool without a fixture. Anything discovered at runtime that is in
# neither dict gets the auto-reason "no fixture" from the runner.

SKIP: Dict[str, str] = {
    # strava — write path + tools needing an id from a previous live call
    "strava__delete_activity": "write path ('delete'), excluded from automated sweep",
    "strava__get_activity_detail":
        "signature unclear for a static fixture — needs a live activity_id/name",
    "strava__get_activity_streams":
        "signature unclear for a static fixture — needs a live activity_id/name",
    "strava__compare_activity_to_baseline":
        "signature unclear for a static fixture — needs a live activity_id/name",

    # garmin — tools needing an id from a previous live call
    "garmin__get_garmin_activity_detail":
        "signature unclear for a static fixture — needs a live Garmin activity_id",
    "garmin__get_activity_gps_track":
        "signature unclear for a static fixture — needs a live Garmin activity_id",

    # calendar — read tool needing a live id, plus the three write paths
    "calendar__get_event":
        "signature unclear for a static fixture — needs a live event_id",
    "calendar__create_event": "write path ('create'), excluded from automated sweep",
    "calendar__update_event": "write path ('update'), excluded from automated sweep",
    "calendar__delete_event": "write path ('delete'), excluded from automated sweep",

    # athlete — everything that mutates or stores athlete state
    "athlete__set_race_goal": "write path ('set'), excluded from automated sweep",
    "athlete__add_milestone": "write path ('add'), excluded from automated sweep",
    "athlete__update_milestone_status": "write path ('update'), excluded from automated sweep",
    "athlete__delete_race_goal": "write path ('delete'), excluded from automated sweep",
    "athlete__add_timeline_event": "write path ('add'), excluded from automated sweep",
    "athlete__delete_timeline_event": "write path ('delete'), excluded from automated sweep",
    "athlete__set_athlete_profile": "write path ('set'), excluded from automated sweep",
    "athlete__compute_zones": "write path — computes AND stores the athlete's zones",
    "athlete__scaffold_plan": "write path — plan scaffolding, not a read-only tool",
    "athlete__save_plan": "write path — persists a training plan",
    "athlete__record_week_actual": "write path ('record'), excluded from automated sweep",
    "athlete__rescaffold_plan": "write path ('rescaffold'), excluded from automated sweep",

    # flythrough — the whole server
    "flythrough__prepare_flythrough": "starts a headless render",

    # google_maps — registered in core.config but not part of this sweep. Every
    # tool costs a Google Places/Routes quota unit and duplicates routes__geocode
    # coverage; listed here so it shows up as a deliberate skip when it is up.
    "google_maps__maps_search_places": "external quota — covered by routes__geocode",
    "google_maps__maps_search_along_route": "external quota — covered by routes__geocode",
    "google_maps__maps_place_details":
        "signature unclear for a static fixture — needs a live place_id",
    "google_maps__maps_geocode": "external quota — covered by routes__geocode",
    "google_maps__maps_reverse_geocode": "external quota — covered by routes__geocode",
    "google_maps__maps_directions": "external quota — covered by routes__plan_route",
}


# ── Helpers used by the runner ────────────────────────────────────────────────

def skip_reason(full_name: str) -> str:
    """Why ``full_name`` is not exercised (veto first, then SKIP, then default)."""
    return write_path_reason(full_name) or SKIP.get(full_name) or "no fixture"


def malformed_variants(full_name: str) -> List[Dict[str, Any]]:
    """The malformed argument sets for a tool (possibly empty)."""
    return list((FIXTURES.get(full_name) or {}).get("malformed") or [])


# ── Import-time safety self-check ─────────────────────────────────────────────
# Layer 1 (a fixture exists) must never contradict layer 2 (the write-path veto).

_VETOED_FIXTURES = sorted(n for n in FIXTURES if write_path_reason(n))
if _VETOED_FIXTURES:  # pragma: no cover — a guard, not a code path
    raise RuntimeError(
        "Refusing to load robustness fixtures: these tools are on the write-path "
        f"veto list yet carry a fixture: {_VETOED_FIXTURES}"
    )
