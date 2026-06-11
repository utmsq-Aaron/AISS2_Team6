"""Strava — native FastMCP server (Streamable HTTP).

Self-contained MCP server for Strava activity data, backed by the Strava v3 REST API
with OAuth2. No BaseMCPServer, no dispatch indirection — the tools call the API directly.
The app reaches it as a plain MCP client via ``core.host.ToolHost``.

Run locally:   python -m servers.strava_mcp
Endpoint:      http://127.0.0.1:8103/mcp   (override host/port via env)
"""

import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Allow running as a standalone process from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from auth.strava_oauth import OAuth2Manager  # noqa: E402

HOST = os.getenv("STRAVA_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("STRAVA_MCP_PORT", "8103"))

mcp = FastMCP(
    "strava",
    instructions=(
        "Strava activity data: list activities, aggregate stats, training trends, "
        "personal bests, yearly breakdown, gear info, GPS streams, activity detail, "
        "performance trend analysis over time, activity vs. baseline comparison, "
        "and training load (ATL/CTL/TSB)."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
)


# ── Strava API client ─────────────────────────────────────────────────────────

_HTTP_TIMEOUT    = 30
_MAX_RETRIES     = 3
_ACTIVITIES_SINCE = datetime(2010, 1, 1)
_PER_PAGE        = 200


class StravaAPI:
    """Thread-safe async wrapper around the Strava v3 REST API with OAuth2 token management."""

    BASE = "https://www.strava.com/api/v3"

    def __init__(self) -> None:
        self._oauth: Optional[OAuth2Manager] = None
        self._token: Optional[str] = None
        self._lock = threading.Lock()

    def _init_oauth(self) -> None:
        if self._oauth is None:
            cid  = os.getenv("CLIENT_ID")
            csec = os.getenv("CLIENT_SECRET")
            if not cid or not csec:
                raise RuntimeError(
                    "CLIENT_ID and CLIENT_SECRET must be set in .env. "
                    "Copy .env.example to .env and add your Strava API credentials."
                )
            self._oauth = OAuth2Manager(cid, csec)

    async def _ensure_token(self) -> None:
        """Refresh/obtain access token, serialised to prevent thundering-herd."""
        with self._lock:
            self._init_oauth()
            self._token = self._oauth.get_valid_access_token()

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, url: str, **kwargs) -> requests.Response:
        """HTTP GET with auth, timeout, and retry on 429/5xx."""
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("timeout", _HTTP_TIMEOUT)
        last_exc: Optional[Exception] = None
        resp: Optional[requests.Response] = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.get(url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(2 ** attempt + random.uniform(0, 0.5))
                continue
            if resp.status_code == 429:
                # Return the 429 immediately — the Retry-After is usually 15+ min,
                # so retrying in the same request loop is pointless and only blocks.
                return resp
            if resp.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            return resp
        if last_exc:
            raise RuntimeError(f"Strava HTTP request failed after {_MAX_RETRIES} attempts: {last_exc}")
        return resp

    async def get_activities(
        self,
        limit: int = 200,
        sport_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict]:
        await self._ensure_token()
        collected: List[Dict] = []
        page = 1
        after_ts = (
            int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
            if start_date else int(_ACTIVITIES_SINCE.timestamp())
        )
        before_ts = (
            int((datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).timestamp())
            if end_date else int(datetime.now().timestamp())
        )
        max_fetch = max(limit, _PER_PAGE)

        while len(collected) < max_fetch:
            resp = self._get(
                f"{self.BASE}/activities",
                params={
                    "per_page": min(_PER_PAGE, max_fetch - len(collected)),
                    "page":     page,
                    "after":    after_ts,
                    "before":   before_ts,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Strava API {resp.status_code}: {resp.text}")
            batch = resp.json()
            if not batch:
                break
            if sport_type:
                batch = [a for a in batch if a.get("type", "").lower() == sport_type.lower()]
            collected.extend(batch)
            page += 1
            if len(batch) < _PER_PAGE:
                break

        collected.sort(key=lambda x: x.get("start_date", ""), reverse=True)
        return collected[:limit]

    # ── Cached wrapper — disk cache persists across restarts, no auto-expiry.
    # Only refreshed when the user explicitly clicks "Refresh data" (which deletes
    # the file) or adds a new activity (handled by the delete/import flows).
    _FILE_CACHE = Path(".cache/strava_activities.json")

    async def get_activities_cached(
        self,
        limit: int = 200,
        sport_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict]:
        is_unfiltered = (sport_type is None and start_date is None and end_date is None)

        # Disk cache: for unfiltered queries return as-is; for filtered queries
        # apply filters client-side so all filtered variants share the same file.
        file_data = self._load_file_cache()
        if file_data is not None:
            if is_unfiltered:
                return file_data[:limit]
            return self._filter_activities(file_data, sport_type, start_date, end_date)[:limit]

        data = await self.get_activities(
            limit=limit, sport_type=sport_type,
            start_date=start_date, end_date=end_date,
        )

        # Enrich missing polylines before caching. Strava's list endpoint omits
        # summary_polyline for many activities; the detail endpoint always has it.
        # Skip sports that never have outdoor GPS to avoid wasting API calls.
        _NO_GPS_TYPES = {"Swim", "WeightTraining", "Yoga", "Crossfit", "Elliptical",
                         "StairStepper", "RockClimbing", "VirtualRide", "VirtualRun"}
        if is_unfiltered:
            no_poly = [
                a for a in data
                if not (a.get("map") or {}).get("summary_polyline", "")
                and a.get("type") not in _NO_GPS_TYPES
            ]
            for a in no_poly:
                poly = await self.get_activity_polyline(a["id"])
                if poly:
                    if not a.get("map"):
                        a["map"] = {}
                    a["map"]["summary_polyline"] = poly
            self._save_file_cache(data)

        return data

    @staticmethod
    def _filter_activities(
        activities: List[Dict],
        sport_type: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> List[Dict]:
        result = activities
        if sport_type:
            st = sport_type.lower()
            result = [
                a for a in result
                if (a.get("type") or "").lower() == st
                or (a.get("sport_type") or "").lower() == st
            ]
        if start_date:
            result = [a for a in result if (a.get("start_date") or "")[:10] >= start_date]
        if end_date:
            result = [a for a in result if (a.get("start_date") or "")[:10] <= end_date]
        return result

    def _load_file_cache(self) -> Optional[List[Dict]]:
        """Return cached activities from disk, or None if no cache file exists."""
        try:
            if not self._FILE_CACHE.exists():
                return None
            return json.loads(self._FILE_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_file_cache(self, data: List[Dict]) -> None:
        """Write activity list to disk so it survives server restarts."""
        try:
            self._FILE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            self._FILE_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    async def get_athlete(self) -> Dict:
        await self._ensure_token()
        resp = self._get(f"{self.BASE}/athlete")
        resp.raise_for_status()
        return resp.json()

    async def get_athlete_stats(self, athlete_id: int) -> Dict:
        await self._ensure_token()
        resp = self._get(f"{self.BASE}/athletes/{athlete_id}/stats")
        resp.raise_for_status()
        return resp.json()

    def _evict_activity(self, activity_id: int) -> None:
        """Remove a deleted/inaccessible activity from the disk cache."""
        aid = int(activity_id)
        try:
            if self._FILE_CACHE.exists():
                raw = json.loads(self._FILE_CACHE.read_text(encoding="utf-8"))
                trimmed_file = [a for a in raw if a.get("id") != aid]
                if len(trimmed_file) < len(raw):
                    self._FILE_CACHE.write_text(
                        json.dumps(trimmed_file, ensure_ascii=False), encoding="utf-8"
                    )
        except Exception:
            pass

    async def get_activity_by_id(self, activity_id: int) -> Dict:
        await self._ensure_token()
        resp = self._get(f"{self.BASE}/activities/{activity_id}")
        if resp.status_code == 404:
            self._evict_activity(activity_id)
            raise RuntimeError(f"Activity {activity_id} not found (404) — removed from cache")
        if not resp.ok:
            raise RuntimeError(f"Activity {activity_id} not found ({resp.status_code})")
        return resp.json()

    async def get_activity_polyline(self, activity_id: int) -> str:
        """Return map.polyline from activity detail (Strava list endpoint omits it)."""
        try:
            detail = await self.get_activity_by_id(activity_id)
            return (detail.get("map") or {}).get("polyline", "")
        except Exception:
            return ""

    async def get_activity_streams(self, activity_id: int) -> Dict:
        await self._ensure_token()
        resp = self._get(
            f"{self.BASE}/activities/{activity_id}/streams",
            params={
                "keys":        "latlng,altitude,time,distance,heartrate,cadence,velocity_smooth,watts",
                "key_by_type": "true",
            },
        )
        if resp.status_code == 404:
            self._evict_activity(activity_id)
            raise RuntimeError(f"Streams {activity_id}: 404 — activity removed from cache")
        if not resp.ok:
            raise RuntimeError(f"Streams {activity_id}: {resp.status_code}")
        return resp.json()

    async def get_gear(self, gear_id: str) -> Optional[Dict]:
        await self._ensure_token()
        resp = self._get(f"{self.BASE}/gear/{gear_id}")
        return resp.json() if resp.ok else None

    def _delete(self, url: str) -> requests.Response:
        return requests.delete(url, headers=self._headers(), timeout=_HTTP_TIMEOUT)


_api = StravaAPI()


def _pace(speed_kmh: float) -> Optional[float]:
    if not speed_kmh:
        return None
    return round(60.0 / speed_kmh, 2)


def _pace_str(speed_kmh: float) -> Optional[str]:
    """Return pace as M:SS string (e.g. '5:41') from speed in km/h."""
    if not speed_kmh:
        return None
    total_sec = 3600 / speed_kmh
    mins = int(total_sec // 60)
    secs = int(total_sec % 60)
    return f"{mins}:{secs:02d}"


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_activities(
    limit: int = 50,
    sport_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """List the user's Strava-recorded activities (most recent first), optionally
    filtered by sport type and date range. This is the primary activity source —
    it includes activities originally recorded on Garmin and synced to Strava.
    If this returns 0 results (Strava not connected), fall back to
    garmin__get_garmin_activities. Do NOT call both and compare them.

    Returns id, name, date, distance, duration, elevation_gain_m (total
    vertical meters climbed), elevation_high_m (highest GPS altitude in meters
    above sea level), elevation_low_m (lowest GPS altitude in meters above sea
    level), avg/max speed, avg/max heart rate, pace (min/km), suffer_score
    (HR-based relative effort, often null for activities without HR), kilojoules
    (meaningful only for cycling with a power meter), pr_count, and kudos. Each
    activity's numeric id can be passed to get_activity_detail or
    get_activity_streams for deeper analysis.

    Args:
        limit: Max activities to return (default 50).
        sport_type: Filter by type, e.g. 'Run', 'Ride', 'Hike', 'Walk', 'Swim'.
        start_date: Return only activities on or after YYYY-MM-DD.
        end_date: Return only activities on or before YYYY-MM-DD.
    """
    activities = await _api.get_activities_cached(
        limit=limit,
        sport_type=sport_type,
        start_date=start_date,
        end_date=end_date,
    )

    rows = []
    for a in activities:
        spd = round(a.get("average_speed", 0) * 3.6, 2)
        rows.append({
            "id":                a.get("id"),
            "name":              a.get("name", "Unknown"),
            "type":              a.get("type", "Unknown"),
            "sport_type":        a.get("sport_type"),          # Strava v3 sport_type field
            "date":              a.get("start_date", "")[:10], # YYYY-MM-DD for display
            "start_date":        a.get("start_date", ""),      # full ISO for date filtering
            "distance_km":       round(a.get("distance", 0) / 1000, 2),
            "moving_time_hours": round(a.get("moving_time", 0) / 3600, 2),
            "elevation_gain_m":  a.get("total_elevation_gain", 0),
            "elevation_high_m":  a.get("elev_high"),     # peak altitude above sea level (m)
            "elevation_low_m":   a.get("elev_low"),      # lowest point above sea level (m)
            "avg_speed_kmh":     spd,
            "pace_min_per_km":   _pace(spd),
            "pace_display":      _pace_str(spd),
            "avg_heart_rate":    a.get("average_heartrate"),
            "max_heart_rate":    a.get("max_heartrate"),
            "suffer_score":      a.get("suffer_score"),
            "kilojoules":        a.get("kilojoules"),
            "pr_count":          a.get("pr_count", 0),
            "kudos":             a.get("kudos_count", 0),
            "gear_id":           a.get("gear_id"),
            "map_polyline":      (a.get("map") or {}).get("summary_polyline", ""),
        })
    return {"total_count": len(rows), "activities": rows}


@mcp.tool()
async def get_activity_stats() -> Dict[str, Any]:
    """Aggregate statistics across all recorded activities.

    Returns totals (distance, time, elevation, kilojoules for power-metered
    rides), averages, per-sport-type breakdown, and the single longest activity.
    Call when the user asks about overall training volume or totals.
    """
    activities = await _api.get_activities_cached(limit=400)
    total_dist = sum(a.get("distance", 0) for a in activities) / 1000
    total_time = sum(a.get("moving_time", 0) for a in activities) / 3600
    total_elev = sum(a.get("total_elevation_gain", 0) for a in activities)
    total_kj   = sum(a.get("kilojoules") or 0 for a in activities)

    breakdown: Dict[str, Dict] = {}
    for a in activities:
        t = a.get("type", "Unknown")
        if t not in breakdown:
            breakdown[t] = {"count": 0, "distance_km": 0.0, "time_hours": 0.0, "elevation_m": 0.0}
        breakdown[t]["count"]        += 1
        breakdown[t]["distance_km"]   = round(breakdown[t]["distance_km"]  + a.get("distance", 0) / 1000, 1)
        breakdown[t]["time_hours"]    = round(breakdown[t]["time_hours"]   + a.get("moving_time", 0) / 3600, 1)
        breakdown[t]["elevation_m"]   = round(breakdown[t]["elevation_m"]  + a.get("total_elevation_gain", 0), 0)

    longest = max(activities, key=lambda x: x.get("distance", 0)) if activities else None
    return {
        "total_activities":              len(activities),
        "total_distance_km":             round(total_dist, 1),
        "total_time_hours":              round(total_time, 1),
        "total_elevation_gain_m":        round(total_elev, 0),
        "avg_distance_per_activity_km":  round(total_dist / len(activities), 1) if activities else 0,
        "total_kilojoules":              round(total_kj, 0),
        "sport_breakdown":               breakdown,
        "longest_activity": {
            "id":                longest.get("id"),
            "name":              longest.get("name"),
            "type":              longest.get("type"),
            "date":              longest.get("start_date", "")[:10],
            "distance_km":       round(longest.get("distance", 0) / 1000, 2),
            "moving_time_hours": round(longest.get("moving_time", 0) / 3600, 2),
            "elevation_gain_m":  longest.get("total_elevation_gain", 0),
        } if longest else None,
    }


@mcp.tool()
async def get_athlete_profile() -> Dict[str, Any]:
    """Athlete profile (name, city, weight, FTP, bikes, shoes) plus Strava's
    official cumulative stats: all-time, year-to-date, and last-4-weeks totals
    for running, cycling, and swimming; biggest ride and climb ever.
    """
    athlete = await _api.get_athlete()
    stats   = await _api.get_athlete_stats(athlete["id"])

    def _fmt(t: Optional[Dict]) -> Dict:
        if not t:
            return {}
        return {
            "count":             t.get("count", 0),
            "distance_km":       round(t.get("distance", 0) / 1000, 1),
            "moving_time_hours": round(t.get("moving_time", 0) / 3600, 1),
            "elevation_gain_m":  round(t.get("elevation_gain", 0), 0),
        }

    return {
        "profile": {
            "name":           f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
            "firstname":      athlete.get("firstname", ""),
            "lastname":       athlete.get("lastname", ""),
            "username":       athlete.get("username"),
            "city":           athlete.get("city"),
            "state":          athlete.get("state"),
            "country":        athlete.get("country"),
            "sex":            athlete.get("sex"),
            "weight_kg":      athlete.get("weight"),
            "ftp":            athlete.get("ftp"),
            "follower_count": athlete.get("follower_count", 0),
            "friend_count":   athlete.get("friend_count", 0),
            "premium":        athlete.get("premium", False),
            "member_since":   athlete.get("created_at", "")[:10],
            "profile_url":    athlete.get("profile", ""),      # avatar image URL
            "bikes": [
                {"id": b.get("id"), "name": b.get("name"), "distance_km": round(b.get("distance", 0) / 1000, 1)}
                for b in athlete.get("bikes", [])
            ],
            "shoes": [
                {"id": s.get("id"), "name": s.get("name"), "distance_km": round(s.get("distance", 0) / 1000, 1)}
                for s in athlete.get("shoes", [])
            ],
        },
        "official_stats": {
            "all_time":     {"run": _fmt(stats.get("all_run_totals")),    "ride": _fmt(stats.get("all_ride_totals")),    "swim": _fmt(stats.get("all_swim_totals"))},
            "year_to_date": {"run": _fmt(stats.get("ytd_run_totals")),    "ride": _fmt(stats.get("ytd_ride_totals")),    "swim": _fmt(stats.get("ytd_swim_totals"))},
            "last_4_weeks": {"run": _fmt(stats.get("recent_run_totals")), "ride": _fmt(stats.get("recent_ride_totals")), "swim": _fmt(stats.get("recent_swim_totals"))},
            "biggest_ride_distance_km":       round(stats.get("biggest_ride_distance", 0) / 1000, 2),
            "biggest_climb_elevation_gain_m": stats.get("biggest_climb_elevation_gain", 0),
        },
    }


@mcp.tool()
async def get_training_trends(weeks: int = 12) -> Dict[str, Any]:
    """Weekly training volume (distance, time, elevation, activity count) for the
    last N weeks — useful for showing training consistency and peak weeks.

    NOTE: for ATL/CTL/TSB training load (overtraining, form, fitness), use
    strava__get_training_load instead.

    Args:
        weeks: Past weeks to include (default 12).
    """
    activities = await _api.get_activities_cached(limit=400)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    week_data: Dict[str, Dict] = {}
    for i in range(weeks):
        ws = now - timedelta(weeks=i + 1)
        wk = ws.strftime("%Y-W%W")
        week_data[wk] = {
            "week":              wk,
            "week_start":        ws.strftime("%Y-%m-%d"),
            "week_end":          (now - timedelta(weeks=i)).strftime("%Y-%m-%d"),
            "activities":        0,
            "distance_km":       0.0,
            "moving_time_hours": 0.0,
            "elevation_gain_m":  0.0,
            "sport_types":       {},
        }

    for a in activities:
        ds = a.get("start_date", "")
        if not ds:
            continue
        try:
            act_dt = datetime.strptime(ds, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
        age_days = (now - act_dt).days
        if age_days < 0 or age_days >= weeks * 7:
            continue
        wk = (now - timedelta(weeks=age_days // 7 + 1)).strftime("%Y-W%W")
        if wk not in week_data:
            continue
        w = week_data[wk]
        w["activities"]        += 1
        w["distance_km"]        = round(w["distance_km"]       + a.get("distance", 0) / 1000, 2)
        w["moving_time_hours"]  = round(w["moving_time_hours"] + a.get("moving_time", 0) / 3600, 2)
        w["elevation_gain_m"]   = round(w["elevation_gain_m"]  + a.get("total_elevation_gain", 0), 1)
        sport = a.get("type", "Unknown")
        w["sport_types"][sport] = w["sport_types"].get(sport, 0) + 1

    active = [w for w in week_data.values() if w["activities"] > 0]
    return {
        "period_weeks": weeks,
        "weeks":        sorted(week_data.values(), key=lambda x: x["week_start"], reverse=True),
        "summary": {
            "active_weeks":                    len(active),
            "total_activities":                sum(w["activities"] for w in week_data.values()),
            "avg_distance_per_active_week_km": round(sum(w["distance_km"] for w in active) / len(active), 1) if active else 0,
            "avg_hours_per_active_week":       round(sum(w["moving_time_hours"] for w in active) / len(active), 1) if active else 0,
        },
    }


@mcp.tool()
async def get_personal_bests(sport_type: Optional[str] = None) -> Dict[str, Any]:
    """Top personal performances: top-5 by distance, duration, elevation gain,
    and avg speed. Also: biggest single training week, longest consecutive
    activity streak, and total unique active days.

    Args:
        sport_type: Optionally restrict to one sport type (e.g. 'Run', 'Ride').
    """
    activities = await _api.get_activities_cached(limit=400, sport_type=sport_type)
    if not activities:
        return {"error": "No activities found"}

    def _fmt(a: Dict) -> Dict:
        spd_kmh = round(a.get("average_speed", 0) * 3.6, 2)
        return {
            "id":               a.get("id"),
            "name":             a.get("name", "Unknown"),
            "type":             a.get("type"),
            "date":             a.get("start_date", "")[:10],
            "distance_km":      round(a.get("distance", 0) / 1000, 2),
            "moving_time_h":    round(a.get("moving_time", 0) / 3600, 2),
            "elevation_gain_m": a.get("total_elevation_gain", 0),
            "avg_speed_kmh":    spd_kmh,
            "pace_min_per_km":  _pace(spd_kmh),
            "pace_display":     _pace_str(spd_kmh),
        }

    week_dist: Dict[str, float] = {}
    active_dates = set()
    for a in activities:
        ds = a.get("start_date", "")
        if not ds:
            continue
        try:
            dt = datetime.strptime(ds, "%Y-%m-%dT%H:%M:%SZ")
            active_dates.add(dt.date())
            wk = dt.strftime("%Y-W%W")
            week_dist[wk] = week_dist.get(wk, 0.0) + a.get("distance", 0) / 1000
        except ValueError:
            pass

    biggest_week = max(week_dist.items(), key=lambda x: x[1]) if week_dist else None
    max_streak = 0
    if active_dates:
        sorted_dates = sorted(active_dates)
        cur = max_streak = 1
        for i in range(1, len(sorted_dates)):
            cur = cur + 1 if (sorted_dates[i] - sorted_dates[i - 1]).days == 1 else 1
            max_streak = max(max_streak, cur)

    return {
        "top_5_by_distance":        [_fmt(a) for a in sorted(activities, key=lambda x: x.get("distance", 0), reverse=True)[:5]],
        "top_5_by_duration":        [_fmt(a) for a in sorted(activities, key=lambda x: x.get("moving_time", 0), reverse=True)[:5]],
        "top_5_by_elevation":       [_fmt(a) for a in sorted(activities, key=lambda x: x.get("total_elevation_gain", 0), reverse=True)[:5]],
        "top_5_fastest":            [_fmt(a) for a in sorted([a for a in activities if a.get("average_speed", 0) > 0], key=lambda x: x.get("average_speed", 0), reverse=True)[:5]],
        "biggest_week":             {"week": biggest_week[0], "distance_km": round(biggest_week[1], 1)} if biggest_week else None,
        "longest_streak_days":      max_streak,
        "total_unique_active_days": len(active_dates),
    }


@mcp.tool()
async def get_yearly_breakdown() -> Dict[str, Any]:
    """Year-over-year training statistics. Each year includes total activities,
    distance, time, elevation, and a per-sport breakdown.
    """
    activities = await _api.get_activities_cached(limit=400)
    yearly: Dict[int, Dict] = {}
    for a in activities:
        try:
            yr = datetime.strptime(a.get("start_date", ""), "%Y-%m-%dT%H:%M:%SZ").year
        except (ValueError, TypeError):
            continue
        if yr not in yearly:
            yearly[yr] = {
                "year": yr, "total_activities": 0, "total_distance_km": 0.0,
                "total_time_hours": 0.0, "total_elevation_m": 0.0, "sport_breakdown": {},
            }
        y = yearly[yr]
        y["total_activities"]   += 1
        y["total_distance_km"]  += a.get("distance", 0) / 1000
        y["total_time_hours"]   += a.get("moving_time", 0) / 3600
        y["total_elevation_m"]  += a.get("total_elevation_gain", 0)
        sport = a.get("type", "Unknown")
        if sport not in y["sport_breakdown"]:
            y["sport_breakdown"][sport] = {"count": 0, "distance_km": 0.0, "time_hours": 0.0}
        y["sport_breakdown"][sport]["count"]        += 1
        y["sport_breakdown"][sport]["distance_km"]  += a.get("distance", 0) / 1000
        y["sport_breakdown"][sport]["time_hours"]   += a.get("moving_time", 0) / 3600

    for y in yearly.values():
        y["total_distance_km"] = round(y["total_distance_km"], 1)
        y["total_time_hours"]  = round(y["total_time_hours"], 1)
        y["total_elevation_m"] = round(y["total_elevation_m"], 0)
        for s in y["sport_breakdown"].values():
            s["distance_km"] = round(s["distance_km"], 1)
            s["time_hours"]  = round(s["time_hours"], 1)

    return {"years": sorted(yearly.values(), key=lambda x: x["year"], reverse=True)}


@mcp.tool()
async def get_gear_info() -> Dict[str, Any]:
    """The athlete's registered bikes and running shoes with brand, model,
    accumulated mileage, and whether it is the primary gear item.
    """
    athlete = await _api.get_athlete()
    result: Dict[str, List] = {"bikes": [], "shoes": []}

    for item in athlete.get("bikes", []):
        gear = await _api.get_gear(item["id"])
        result["bikes"].append({
            "name":        (gear or item).get("name"),
            "brand":       (gear or {}).get("brand_name"),
            "model":       (gear or {}).get("model_name"),
            "description": (gear or {}).get("description", ""),
            "distance_km": round((gear or item).get("distance", 0) / 1000, 1),
            "primary":     (gear or {}).get("primary", False),
        })

    for item in athlete.get("shoes", []):
        gear = await _api.get_gear(item["id"])
        result["shoes"].append({
            "name":        (gear or item).get("name"),
            "brand":       (gear or {}).get("brand_name"),
            "model":       (gear or {}).get("model_name"),
            "description": (gear or {}).get("description", ""),
            "distance_km": round((gear or item).get("distance", 0) / 1000, 1),
            "primary":     (gear or {}).get("primary", False),
        })

    return result


@mcp.tool()
async def get_activity_detail(
    activity_id: Optional[int] = None,
    activity_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Deep detail for one activity: per-km splits, lap data, heart rate, power,
    cadence, calories, suffer score, PRs, gear, and location.

    Identify by numeric ID or by a name substring. When using activity_name,
    returns the MOST RECENT activity whose name contains the keyword (searches
    the 100 most recent activities, newest first).

    Args:
        activity_id: Strava numeric activity ID.
        activity_name: Short keyword from the activity name (e.g. 'karlsruhe', 'morning run'). NOT the full user sentence.
    """
    if not activity_id and not activity_name:
        return {"error": "Provide activity_id or activity_name"}

    name_search = (activity_name or "").strip().lower()

    if activity_id:
        a = await _api.get_activity_by_id(int(activity_id))
    else:
        activities = await _api.get_activities_cached(limit=100)
        matches = [x for x in activities if name_search in x.get("name", "").lower()]
        if not matches:
            return {"error": f"No activity found matching '{name_search}'"}
        a = await _api.get_activity_by_id(int(matches[0]["id"]))

    avg_spd = round(a.get("average_speed", 0) * 3.6, 2)
    laps = []
    for lap in a.get("laps", []):
        lap_spd = round(lap.get("average_speed", 0) * 3.6, 2)
        laps.append({
            "lap":             lap.get("lap_index"),
            "distance_km":     round(lap.get("distance", 0) / 1000, 2),
            "time_min":        round(lap.get("moving_time", 0) / 60, 1),
            "avg_speed_kmh":   lap_spd,
            "pace_min_per_km": _pace(lap_spd),
            "pace_display":    _pace_str(lap_spd),
            "avg_hr":          lap.get("average_heartrate"),
            "elevation_m":     lap.get("total_elevation_gain"),
        })

    return {
        "id":                   a.get("id"),
        "name":                 a.get("name"),
        "type":                 a.get("type"),
        "date":                 a.get("start_date_local", "")[:10],
        "start_time_local":     a.get("start_date_local"),
        "description":          a.get("description", ""),
        "distance_km":          round(a.get("distance", 0) / 1000, 2),
        "moving_time_hours":    round(a.get("moving_time", 0) / 3600, 2),
        "elapsed_time_hours":   round(a.get("elapsed_time", 0) / 3600, 2),
        "elevation_gain_m":     a.get("total_elevation_gain"),
        "elevation_high_m":     a.get("elev_high"),
        "elevation_low_m":      a.get("elev_low"),
        "avg_speed_kmh":        avg_spd,
        "pace_min_per_km":      _pace(avg_spd),
        "pace_display":         _pace_str(avg_spd),
        "max_speed_kmh":        round(a.get("max_speed", 0) * 3.6, 2),
        "avg_heart_rate_bpm":   a.get("average_heartrate"),
        "max_heart_rate_bpm":   a.get("max_heartrate"),
        "avg_cadence":          a.get("average_cadence"),
        "avg_watts":            a.get("average_watts"),
        "weighted_avg_watts":   a.get("weighted_average_watts"),
        "calories":             a.get("calories"),
        "suffer_score":         a.get("suffer_score"),
        "kudos_count":          a.get("kudos_count", 0),
        "pr_count":             a.get("pr_count", 0),
        "achievement_count":    a.get("achievement_count", 0),
        "gear":                 a.get("gear", {}).get("name") if a.get("gear") else None,
        "city":                 a.get("location_city"),
        "country":              a.get("location_country"),
        "laps":                 laps,
        "splits_per_km": [
            {
                "km":              s.get("split"),
                "distance_m":      round(s.get("distance", 0), 0),
                "time_s":          s.get("elapsed_time"),
                "pace_min_per_km": round(s.get("elapsed_time", 0) / 60 / max(s.get("distance", 1) / 1000, 0.001), 1) if s.get("distance", 0) > 0 else None,
                "avg_hr":          s.get("average_heartrate"),
                "avg_speed_kmh":   round(s.get("average_speed", 0) * 3.6, 2),
                "elevation_diff_m": s.get("elevation_difference"),
            }
            for s in a.get("splits_metric", [])
        ],
    }


@mcp.tool()
async def get_activity_streams(
    activity_id: Optional[int] = None,
    activity_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Raw GPS streams for one activity: lat/lon, altitude (m), elapsed time (s),
    distance, heart rate, cadence, velocity, and power (watts).

    Also returns activity metadata (name, date, distance, pace, avg HR) so no
    separate lookup is needed. Use for route visualisation, elevation profiling,
    or metric-over-distance charts.

    Identify by numeric activity_id OR by activity_name substring. When using
    activity_name, returns the MOST RECENT activity whose name contains the
    keyword — no prior get_activities call is needed.

    Args:
        activity_id: Strava numeric activity ID.
        activity_name: Short keyword extracted from the activity name (e.g. 'karlsruhe', 'trail run'). NOT the full user sentence.
    """
    if not activity_id and not activity_name:
        return {"error": "Provide activity_id or activity_name"}

    name_search = (activity_name or "").strip().lower()
    act_meta = None

    if not activity_id:
        acts = await _api.get_activities_cached(limit=100)
        matches = [a for a in acts if name_search in a.get("name", "").lower()]
        if not matches:
            return {"error": f"No activity found matching '{name_search}'"}
        act_meta    = matches[0]
        activity_id = int(act_meta["id"])

    raw = await _api.get_activity_streams(int(activity_id))

    if act_meta is None:
        try:
            act_meta = await _api.get_activity_by_id(int(activity_id))
        except Exception:
            act_meta = {}

    spd = round((act_meta.get("average_speed") or 0) * 3.6, 2)
    metadata = {
        "name":          act_meta.get("name", ""),
        "date":          (act_meta.get("start_date") or "")[:10],
        "distance_km":   round((act_meta.get("distance") or 0) / 1000, 2),
        "avg_speed_kmh": spd,
        "pace_display":  _pace_str(spd),
        "avg_hr":        act_meta.get("average_heartrate"),
    }

    latlng    = (raw.get("latlng")          or {}).get("data", [])
    altitude  = (raw.get("altitude")        or {}).get("data", [])
    time_s    = (raw.get("time")            or {}).get("data", [])
    distance  = (raw.get("distance")        or {}).get("data", [])
    heartrate = (raw.get("heartrate")       or {}).get("data", [])
    cadence   = (raw.get("cadence")         or {}).get("data", [])
    velocity  = (raw.get("velocity_smooth") or {}).get("data", [])
    watts     = (raw.get("watts")           or {}).get("data", [])

    points = []
    for i, (lat, lon) in enumerate(latlng):
        points.append({
            "lat":      lat,
            "lon":      lon,
            "ele":      altitude[i]  if i < len(altitude)  else None,
            "time_s":   time_s[i]    if i < len(time_s)    else None,
            "dist_m":   distance[i]  if i < len(distance)  else None,
            "hr":       heartrate[i] if i < len(heartrate) else None,
            "cadence":  cadence[i]   if i < len(cadence)   else None,
            "velocity": velocity[i]  if i < len(velocity)  else None,
            "watts":    watts[i]     if i < len(watts)      else None,
        })

    return {
        "activity_id":  activity_id,
        "activity":     metadata,
        "total":        len(points),
        "has_hr":       bool(heartrate),
        "has_cadence":  bool(cadence),
        "has_velocity": bool(velocity),
        "has_watts":    bool(watts),
        "points":       points,
    }


@mcp.tool()
async def delete_activity(activity_id: int) -> Dict[str, Any]:
    """Permanently delete a Strava activity by its numeric ID. This action cannot
    be undone. Requires the Strava token to have the activity:write scope.

    Use only when the user explicitly asks to delete or remove an activity.
    Always confirm the activity name and ID with the user before calling this.

    Args:
        activity_id: The numeric Strava activity ID to delete.
    """
    await _api._ensure_token()
    resp = _api._delete(f"{_api.BASE}/activities/{int(activity_id)}")
    if resp.status_code == 204:
        return {"success": True, "activity_id": activity_id, "message": "Activity deleted."}
    err = ""
    try:
        err = resp.json().get("message", "") or resp.text[:200]
    except Exception:
        err = resp.text[:200]
    if resp.status_code == 401:
        err += " — reconnect Strava in ⚙️ Settings (activity:write scope required)"
    return {"error": f"Strava {resp.status_code}: {err}"}


@mcp.tool()
async def analyze_performance_trends(
    sport_type: str = "Run",
    limit: int = 30,
    start_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyse how key performance metrics evolve across the most recent N activities
    of one sport type. Returns a per-activity time series (oldest → newest) for pace,
    average heart rate, distance, elevation, and effort score — plus a linear trend
    direction (improving / declining / stable) for pace and HR, and highlights the
    best and worst activity.

    Use when the user asks: "is my pace improving?", "how has my training changed
    over the last month?", "show me my running progress", "Trainingsfortschritt",
    "Entwicklung meiner Laufleistung", etc.

    Args:
        sport_type: Strava sport type, e.g. "Run", "Ride", "Hike", "Walk".
        limit: Number of most recent activities to analyse (default 30, max 100).
        start_date: Optional ISO date YYYY-MM-DD; restrict analysis to activities on
                    or after this date (combined with limit).
    """
    activities = await _api.get_activities_cached(
        limit=min(int(limit), 100), sport_type=sport_type, start_date=start_date
    )
    if not activities:
        return {"error": f"No {sport_type} activities found"}

    # Sort oldest → newest for trend analysis
    activities = sorted(activities, key=lambda a: a.get("start_date", ""))

    series = []
    paces: List[float] = []
    hrs:   List[float] = []
    dists: List[float] = []
    elevs: List[float] = []

    for a in activities:
        spd   = (a.get("average_speed") or 0) * 3.6  # m/s → km/h
        pace  = round(60.0 / spd, 3) if spd > 0 else None
        hr    = a.get("average_heartrate")
        dist  = round((a.get("distance") or 0) / 1000, 2)
        elev  = round(a.get("total_elevation_gain") or 0, 1)
        elev_km = round(elev / dist, 1) if dist > 0 else None
        suffer = a.get("suffer_score")

        series.append({
            "date":               (a.get("start_date") or "")[:10],
            "name":               a.get("name", ""),
            "distance_km":        dist,
            "pace_min_per_km":    pace,
            "pace_display":       _pace_str(spd) if spd > 0 else None,
            "avg_hr":             hr,
            "elevation_m":        elev,
            "elevation_per_km":   elev_km,
            "suffer_score":       suffer,
            "moving_time_h":      round((a.get("moving_time") or 0) / 3600, 2),
        })
        if pace: paces.append(pace)
        if hr:   hrs.append(float(hr))
        dists.append(dist)
        if elev_km is not None: elevs.append(elev_km)

    def _trend(values: List[float], lower_is_better: bool = True) -> str:
        n = len(values)
        if n < 4:
            return "insufficient data"
        xs = list(range(n))
        x_m = sum(xs) / n
        y_m = sum(values) / n
        num = sum((xs[i] - x_m) * (values[i] - y_m) for i in range(n))
        den = sum((x - x_m) ** 2 for x in xs) or 1e-9
        slope = num / den
        if abs(slope) / (abs(y_m) or 1) < 0.003:
            return "stable"
        getting_better = (slope < 0) == lower_is_better
        return "improving" if getting_better else "declining"

    paced = [e for e in series if e["pace_min_per_km"]]
    best  = min(paced, key=lambda x: x["pace_min_per_km"]) if paced else None
    worst = max(paced, key=lambda x: x["pace_min_per_km"]) if paced else None

    def _avg(lst): return round(sum(lst) / len(lst), 2) if lst else None

    return {
        "sport_type":    sport_type,
        "activity_count": len(series),
        "date_range":    {"from": series[0]["date"], "to": series[-1]["date"]},
        "trends": {
            "pace":        _trend(paces, lower_is_better=True)  if len(paces) >= 4 else "insufficient data",
            "heart_rate":  _trend(hrs,   lower_is_better=True)  if len(hrs)   >= 4 else "insufficient data",
        },
        "averages": {
            "pace_min_per_km":  _avg(paces),
            "avg_hr_bpm":       _avg(hrs),
            "distance_km":      _avg(dists),
            "elevation_per_km": _avg(elevs),
        },
        "highlights": {
            "fastest": {"name": best["name"],  "date": best["date"],  "pace": best["pace_display"]}  if best  else None,
            "slowest": {"name": worst["name"], "date": worst["date"], "pace": worst["pace_display"]} if worst else None,
        },
        "series": series,
    }


@mcp.tool()
async def compare_activity_to_baseline(
    activity_id: Optional[int] = None,
    activity_name: Optional[str] = None,
    baseline_count: int = 30,
) -> Dict[str, Any]:
    """Compare one specific activity's effort and performance metrics against the user's
    personal historical baseline for the same sport type.

    Computes the user's typical values (mean ± std) for pace, heart rate, distance, and
    elevation per km across the last N same-type activities, then places the target
    activity on a difficulty percentile. Returns a human-readable assessment such as
    "harder than usual", "one of your hardest hikes", or "easier than typical".

    Use when the user asks: "war die Bergtour anspruchsvoller als usual?",
    "wie war mein letzter Lauf im Vergleich?", "was this ride hard for me?",
    "compare this activity to my average", etc.

    Args:
        activity_id: Numeric Strava activity ID.
        activity_name: Short keyword in the activity name (e.g. 'karlsruhe', 'morning run').
                       Used only if activity_id is not provided.
        baseline_count: Number of recent same-sport activities to compare against (default 30).
    """
    if not activity_id and not activity_name:
        return {"error": "Provide activity_id or activity_name"}

    name_kw = (activity_name or "").strip().lower()

    if activity_id:
        target = await _api.get_activity_by_id(int(activity_id))
    else:
        candidates = await _api.get_activities_cached(limit=100)
        matches = [a for a in candidates if name_kw in a.get("name", "").lower()]
        if not matches:
            return {"error": f"No activity found matching '{name_kw}'"}
        target = await _api.get_activity_by_id(int(matches[0]["id"]))

    sport   = target.get("type", "")
    tgt_id  = target.get("id")

    baseline_raw = await _api.get_activities_cached(limit=baseline_count + 10, sport_type=sport)
    baseline = [a for a in baseline_raw if a.get("id") != tgt_id][:baseline_count]

    if len(baseline) < 3:
        return {"error": f"Not enough {sport} activities for comparison (need ≥ 3, have {len(baseline)})"}

    def _stat(values: List[float], tgt: Optional[float], lower_harder: bool = False):
        if not values or tgt is None:
            return None
        n    = len(values)
        mean = sum(values) / n
        std  = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
        if lower_harder:
            pct = round(100 * sum(1 for v in values if v > tgt) / n)  # fraction slower
        else:
            pct = round(100 * sum(1 for v in values if v < tgt) / n)  # fraction with less
        z = round((tgt - mean) / std, 1) if std > 0 else 0.0
        return {
            "baseline_mean": round(mean, 2),
            "baseline_std":  round(std, 2),
            "target":        round(tgt, 2),
            "difficulty_percentile": pct,
            "z_score": z,
        }

    t_dist   = (target.get("distance")            or 0) / 1000
    t_elev   = target.get("total_elevation_gain") or 0
    t_spd    = (target.get("average_speed")       or 0) * 3.6
    t_pace   = 60.0 / t_spd if t_spd > 0 else None
    t_hr     = target.get("average_heartrate")
    t_ekm    = t_elev / t_dist if t_dist > 0 else None

    def _bvals(key, fn=lambda a: a):
        return [fn(a) for a in baseline if fn(a) is not None and fn(a) > 0]

    b_dists  = _bvals("distance",            lambda a: (a.get("distance") or 0) / 1000)
    b_elevs  = _bvals("total_elevation_gain", lambda a: a.get("total_elevation_gain") or 0)
    b_ekm    = [
        (a.get("total_elevation_gain") or 0) / max((a.get("distance") or 1) / 1000, 0.01)
        for a in baseline
    ]
    b_speeds = [(a.get("average_speed") or 0) * 3.6 for a in baseline if (a.get("average_speed") or 0) > 0]
    b_paces  = [60.0 / s for s in b_speeds if s > 0]
    b_hrs    = [float(a["average_heartrate"]) for a in baseline if a.get("average_heartrate")]

    comparisons: Dict[str, Any] = {}
    if t_dist and b_dists:
        comparisons["distance_km"]      = _stat(b_dists, t_dist)
    if t_elev and b_elevs:
        comparisons["elevation_m"]      = _stat(b_elevs, t_elev)
    if t_ekm is not None and b_ekm:
        comparisons["elevation_per_km"] = _stat(b_ekm, t_ekm)
    if t_pace and b_paces:
        comparisons["pace_min_per_km"]  = _stat(b_paces, t_pace, lower_harder=True)
    if t_hr and b_hrs:
        comparisons["avg_hr_bpm"]       = _stat(b_hrs, float(t_hr))

    pcts = [v["difficulty_percentile"] for v in comparisons.values() if v]
    overall = round(sum(pcts) / len(pcts)) if pcts else None

    if overall is None:        assessment = "unknown (insufficient data)"
    elif overall >= 85:        assessment = "one of your hardest"
    elif overall >= 65:        assessment = "harder than usual"
    elif overall >= 35:        assessment = "typical"
    elif overall >= 15:        assessment = "easier than usual"
    else:                      assessment = "one of your easiest"

    return {
        "activity": {
            "id":           tgt_id,
            "name":         target.get("name"),
            "date":         (target.get("start_date") or "")[:10],
            "sport_type":   sport,
            "distance_km":  round(t_dist, 2),
            "elevation_m":  round(t_elev, 0),
            "pace_display": _pace_str(t_spd) if t_spd > 0 else None,
            "avg_hr":       t_hr,
        },
        "baseline_activity_count": len(baseline),
        "comparisons":             comparisons,
        "overall_difficulty_percentile": overall,
        "assessment": assessment,
    }


@mcp.tool()
async def get_training_load(weeks: int = 16) -> Dict[str, Any]:
    """Compute the user's training load over the last N weeks using the classic
    ATL/CTL/TSB model (Banister's Impulse–Response model):

    • ATL (Acute Training Load, τ=7d) — recent fatigue / short-term training stress
    • CTL (Chronic Training Load, τ=42d) — fitness base / long-term average
    • TSB (Training Stress Balance) = CTL − ATL:
        positive (> +5) = fresh, rested, ready to race or push hard
        near zero        = neutral, balanced
        negative (< -10) = accumulated fatigue, needs recovery

    Load per activity is taken from Strava's suffer_score (HR-based) when available,
    otherwise estimated from duration × sport-type intensity factor.

    Use when the user asks: "am I overtraining?", "what's my current form?",
    "should I rest this week?", "training load", "overtraining",
    "am I improving my fitness?", etc.

    Args:
        weeks: Number of weeks to look back (default 16; max 52).
    """
    weeks = min(int(weeks), 52)
    activities = await _api.get_activities_cached(limit=500)

    _SPORT_FACTOR = {
        "Run": 1.0, "TrailRun": 1.1, "VirtualRun": 0.9,
        "Ride": 0.6, "MountainBikeRide": 0.8, "GravelRide": 0.7, "VirtualRide": 0.5, "EBikeRide": 0.4,
        "Hike": 0.7, "Walk": 0.4,
        "Swim": 0.9, "Workout": 0.7, "WeightTraining": 0.5,
        "NordicSki": 0.9, "AlpineSki": 0.5,
    }

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    daily: Dict[str, float] = {}

    for a in activities:
        ds = a.get("start_date", "")
        if not ds:
            continue
        try:
            dt = datetime.strptime(ds[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        age = (now - dt).days
        if age < 0 or age >= weeks * 7:
            continue
        key = dt.strftime("%Y-%m-%d")
        suffer = a.get("suffer_score")
        if suffer:
            load = float(suffer)
        else:
            hours  = (a.get("moving_time") or 0) / 3600
            factor = _SPORT_FACTOR.get(a.get("type", ""), 0.7)
            load   = hours * 100 * factor
        daily[key] = daily.get(key, 0.0) + load

    # Build day list oldest → newest
    day_list = [
        (now - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(weeks * 7 - 1, -1, -1)
    ]

    atl, ctl = 0.0, 0.0
    k_atl, k_ctl = 1.0 / 7, 1.0 / 42

    weekly: List[Dict] = []
    cur_week: List[Dict] = []

    for day in day_list:
        load = daily.get(day, 0.0)
        atl  = atl + k_atl * (load - atl)
        ctl  = ctl + k_ctl * (load - ctl)
        cur_week.append({
            "date": day, "load": round(load, 1),
            "atl": round(atl, 1), "ctl": round(ctl, 1), "tsb": round(ctl - atl, 1),
        })
        if len(cur_week) == 7:
            weekly.append({
                "week_start":  cur_week[0]["date"],
                "total_load":  round(sum(d["load"] for d in cur_week), 1),
                "avg_atl":     round(sum(d["atl"]  for d in cur_week) / 7, 1),
                "avg_ctl":     round(sum(d["ctl"]  for d in cur_week) / 7, 1),
                "avg_tsb":     round(sum(d["tsb"]  for d in cur_week) / 7, 1),
            })
            cur_week = []
    if cur_week:
        weekly.append({
            "week_start": cur_week[0]["date"],
            "total_load": round(sum(d["load"] for d in cur_week), 1),
            "avg_atl":    round(sum(d["atl"]  for d in cur_week) / len(cur_week), 1),
            "avg_ctl":    round(sum(d["ctl"]  for d in cur_week) / len(cur_week), 1),
            "avg_tsb":    round(sum(d["tsb"]  for d in cur_week) / len(cur_week), 1),
        })

    last_day   = cur_week[-1] if cur_week else (weekly[-1] if weekly else {})
    final_atl  = last_day.get("atl", atl)
    final_ctl  = last_day.get("ctl", ctl)
    final_tsb  = last_day.get("tsb", ctl - atl)

    if   final_tsb >  15: form = "very fresh — well rested, ready to race or push hard"
    elif final_tsb >   5: form = "fresh — good balance, can push when needed"
    elif final_tsb >  -5: form = "neutral — balanced load"
    elif final_tsb > -15: form = "productively tired — moderate training stress, normal"
    elif final_tsb > -30: form = "tired — high load, schedule recovery"
    else:                 form = "very fatigued — overtraining risk, rest week recommended"

    return {
        "current": {
            "atl":  round(final_atl, 1),
            "ctl":  round(final_ctl, 1),
            "tsb":  round(final_tsb, 1),
            "form": form,
        },
        "explanation": (
            "ATL (Acute Load, 7d) = short-term fatigue. "
            "CTL (Chronic Load, 42d) = fitness base. "
            "TSB = CTL − ATL: positive = rested, negative = training stress. "
            "Load uses suffer_score where available, otherwise duration × sport factor × 100."
        ),
        "weeks": weekly,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
