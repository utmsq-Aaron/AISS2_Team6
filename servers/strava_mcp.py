"""Strava — native FastMCP server (Streamable HTTP).

Self-contained MCP server for Strava activity data, backed by the Strava v3 REST API
with OAuth2. No BaseMCPServer, no dispatch indirection — the tools call the API directly.
The app reaches it as a plain MCP client via ``core.host.ToolHost``.

Run locally:   python -m servers.strava_mcp
Endpoint:      http://127.0.0.1:8103/mcp   (override host/port via env)
"""

import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
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
        "personal bests, yearly breakdown, gear info, GPS streams, and activity detail."
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
                wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                time.sleep(min(wait, 60))
                continue
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

    async def get_activity_by_id(self, activity_id: int) -> Dict:
        await self._ensure_token()
        resp = self._get(f"{self.BASE}/activities/{activity_id}")
        if not resp.ok:
            raise RuntimeError(f"Activity {activity_id} not found ({resp.status_code})")
        return resp.json()

    async def get_activity_streams(self, activity_id: int) -> Dict:
        await self._ensure_token()
        resp = self._get(
            f"{self.BASE}/activities/{activity_id}/streams",
            params={
                "keys":        "latlng,altitude,time,distance,heartrate,cadence,velocity_smooth,watts",
                "key_by_type": "true",
            },
        )
        if not resp.ok:
            raise RuntimeError(f"Streams {activity_id}: {resp.status_code}")
        return resp.json()

    async def get_gear(self, gear_id: str) -> Optional[Dict]:
        await self._ensure_token()
        resp = self._get(f"{self.BASE}/gear/{gear_id}")
        return resp.json() if resp.ok else None


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
    filtered by sport type and date range. May return 0 results if Strava is not
    connected or the account has no activities — in that case ALWAYS also call
    garmin__get_garmin_activities as the alternative source.

    Returns id, name, date, distance, duration, elevation, avg/max speed,
    avg/max heart rate, pace (min/km), suffer_score (HR-based relative effort,
    often null for activities without HR), kilojoules (meaningful only for
    cycling with a power meter), pr_count, and kudos. Each activity's numeric
    id can be passed to get_activity_detail or get_activity_streams for deeper
    analysis.

    Args:
        limit: Max activities to return (default 50).
        sport_type: Filter by type, e.g. 'Run', 'Ride', 'Hike', 'Walk', 'Swim'.
        start_date: Return only activities on or after YYYY-MM-DD.
        end_date: Return only activities on or before YYYY-MM-DD.
    """
    activities = await _api.get_activities(
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
    activities = await _api.get_activities(limit=400)
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
    """Per-week training load for the last N weeks.

    Returns distance, time, elevation gain, activity count, and sport types per
    week. Useful for analysing training consistency, progression, and peak weeks.

    Args:
        weeks: Past weeks to include (default 12).
    """
    activities = await _api.get_activities(limit=400)
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
    activities = await _api.get_activities(limit=400, sport_type=sport_type)
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
    activities = await _api.get_activities(limit=400)
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
        activity_name: Short keyword from the activity name (e.g. 'bergen', 'morning run'). NOT the full user sentence.
    """
    if not activity_id and not activity_name:
        return {"error": "Provide activity_id or activity_name"}

    name_search = (activity_name or "").strip().lower()

    if activity_id:
        a = await _api.get_activity_by_id(int(activity_id))
    else:
        activities = await _api.get_activities(limit=100)
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
        activity_name: Short keyword extracted from the activity name (e.g. 'bergen', 'trail run'). NOT the full user sentence.
    """
    if not activity_id and not activity_name:
        return {"error": "Provide activity_id or activity_name"}

    name_search = (activity_name or "").strip().lower()
    act_meta = None

    if not activity_id:
        acts = await _api.get_activities(limit=100)
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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
