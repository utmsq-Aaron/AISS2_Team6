"""Garmin Connect — native FastMCP server (Streamable HTTP).

Self-contained MCP server for Garmin health and activity data. No BaseMCPServer,
no dispatch indirection — the tools call the garminconnect library directly using
cached OAuth tokens from the ``.tokens`` directory.
The app reaches it as a plain MCP client via ``core.host.ToolHost``.

Run locally:   python -m servers.garmin_mcp
Endpoint:      http://127.0.0.1:8104/mcp   (override host/port via env)

Prerequisites: run ``python auth/garmin_setup.py`` once to cache credentials.
"""

import io
import os
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

HOST        = os.getenv("GARMIN_MCP_HOST", "127.0.0.1")
PORT        = int(os.getenv("GARMIN_MCP_PORT", "8104"))
TOKEN_STORE = ".tokens"

_BB_CHUNK_DAYS = 28   # Garmin body-battery API rejects ranges larger than this

mcp = FastMCP(
    "garmin",
    instructions=(
        "Garmin Connect health and activity data: activities, daily wellness, "
        "heart rate timeline, sleep analysis, Body Battery, HRV status, "
        "training metrics (VO2max, race predictions), wellness trends, "
        "steps and stress timelines, body composition, and GPS track download."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
)


class GarminAPI:
    """Lazy-loading wrapper around garminconnect that reuses cached OAuth tokens.

    Thread-safe: uses a lock for init so parallel calls don't race on first login.
    """

    def __init__(self) -> None:
        self._client = None
        self._lock = threading.Lock()

    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    if not Path(TOKEN_STORE).exists():
                        raise RuntimeError(
                            "Garmin tokens not found. Run: python auth/garmin_setup.py"
                        )
                    try:
                        from garminconnect import Garmin
                        g = Garmin()
                        g.login(tokenstore=TOKEN_STORE)
                        self._client = g
                    except Exception as e:
                        raise RuntimeError(
                            f"Garmin login failed: {e}\n"
                            "Re-run: python auth/garmin_setup.py"
                        ) from e
        return self._client

    _TRANSIENT_SIGNALS = ("timeout", "connection", "429", "503", "502", "reset", "eof")
    _MAX_RETRIES = 3

    def _call(self, fn, *args, **kwargs):
        """Call a garminconnect method with retry on transient network errors."""
        last_exc: Optional[Exception] = None
        for attempt in range(self._MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_exc = e
                if any(sig in str(e).lower() for sig in self._TRANSIENT_SIGNALS):
                    time.sleep(2 ** attempt)
                    continue
                break
        raise RuntimeError(f"Garmin API error: {last_exc}") from last_exc


_api = GarminAPI()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _date(s: Optional[str]) -> str:
    return s if s else _today()


def _pace(speed_kmh: float) -> Optional[float]:
    if not speed_kmh:
        return None
    return round(60.0 / speed_kmh, 2)


def _pace_str(speed_kmh: float) -> Optional[str]:
    if not speed_kmh:
        return None
    total_sec = 3600 / speed_kmh
    mins = int(total_sec // 60)
    secs = int(total_sec % 60)
    return f"{mins}:{secs:02d}"


def _h(seconds: int) -> float:
    return round(seconds / 3600, 2)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_garmin_activities(
    limit: int = 50,
    start: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """List Garmin-recorded activities (runs, hikes, rides, swims, …) with
    distance, duration, avg/max HR, calories, elevation, pace, and training effect.

    Use this tool — together with strava__get_activities — whenever the user asks
    about their workouts, runs, rides or training history. Garmin and Strava are
    independent sources; always query both so no activity is missed.

    Args:
        limit: Max activities to return (default 50).
        start: Pagination offset (default 0).
        start_date: Filter from YYYY-MM-DD (inclusive).
        end_date: Filter to YYYY-MM-DD (inclusive).
    """
    g = _api.client()

    if start_date or end_date:
        raw = _api._call(g.get_activities_by_date,
                         start_date or "2010-01-01", end_date or _today())
    else:
        raw = _api._call(g.get_activities, start, limit)

    rows = []
    for a in (raw or [])[:limit]:
        atype = (a.get("activityType") or {}).get("typeKey", "unknown")
        spd   = round((a.get("averageSpeed") or 0) * 3.6, 2)
        rows.append({
            "id":               a.get("activityId"),
            "name":             a.get("activityName", ""),
            "type":             atype,
            "date":             (a.get("startTimeLocal") or "")[:10],
            "distance_km":      round((a.get("distance") or 0) / 1000, 2),
            "duration_min":     round((a.get("duration") or 0) / 60, 1),
            "moving_time_min":  round((a.get("movingDuration") or 0) / 60, 1),
            "avg_hr":           a.get("averageHR"),
            "max_hr":           a.get("maxHR"),
            "calories":         a.get("calories"),
            "elevation_gain_m": a.get("elevationGain"),
            "avg_speed_kmh":    spd,
            "pace_min_per_km":  _pace(spd),
            "pace_display":     _pace_str(spd),
            "training_effect":  a.get("aerobicTrainingEffect"),
            "steps":            a.get("steps"),
        })
    return {"total": len(rows), "activities": rows}


@mcp.tool()
def get_garmin_activity_detail(activity_id: int) -> Dict[str, Any]:
    """Full detail for one Garmin activity: per-lap splits, HR zone breakdown,
    training effect, average power and cadence.

    Args:
        activity_id: Garmin numeric activity ID (from get_garmin_activities).
    """
    g = _api.client()
    summary = _api._call(g.get_activity, activity_id)
    try:
        details = _api._call(g.get_activity_details, activity_id)
    except Exception:
        details = {}
    try:
        hr_zones = _api._call(g.get_activity_hr_in_timezones, activity_id)
    except Exception:
        hr_zones = []

    laps = []
    for lap in (details.get("activityDetailMetrics") or []):
        if isinstance(lap, dict) and lap.get("lapIndex") is not None:
            spd = round((lap.get("averageSpeed") or 0) * 3.6, 2)
            laps.append({
                "lap":             lap.get("lapIndex"),
                "distance_km":     round((lap.get("distance") or 0) / 1000, 2),
                "time_min":        round((lap.get("duration") or 0) / 60, 1),
                "avg_speed_kmh":   spd,
                "pace_min_per_km": _pace(spd),
                "pace_display":    _pace_str(spd),
                "avg_hr":          lap.get("averageHR"),
                "elevation_m":     lap.get("elevationGain"),
            })

    atype = (summary.get("activityType") or {}).get("typeKey", "unknown")
    spd   = round((summary.get("averageSpeed") or 0) * 3.6, 2)
    return {
        "id":               summary.get("activityId"),
        "name":             summary.get("activityName", ""),
        "type":             atype,
        "date":             (summary.get("startTimeLocal") or "")[:10],
        "distance_km":      round((summary.get("distance") or 0) / 1000, 2),
        "duration_min":     round((summary.get("duration") or 0) / 60, 1),
        "avg_hr":           summary.get("averageHR"),
        "max_hr":           summary.get("maxHR"),
        "calories":         summary.get("calories"),
        "elevation_gain_m": summary.get("elevationGain"),
        "avg_speed_kmh":    spd,
        "pace_min_per_km":  _pace(spd),
        "pace_display":     _pace_str(spd),
        "training_effect":  summary.get("aerobicTrainingEffect"),
        "anaerobic_effect": summary.get("anaerobicTrainingEffect"),
        "steps":            summary.get("steps"),
        "avg_cadence":      summary.get("averageBikingCadenceInRevPerMin") or summary.get("averageRunningCadenceInStepsPerMin"),
        "avg_power":        summary.get("avgPower"),
        "normalized_power": summary.get("normPower"),
        "hr_zones": [
            {
                "zone":     z.get("zoneNumber"),
                "time_min": round((z.get("secsInZone") or 0) / 60, 1),
                "hr_low":   z.get("zoneLowBoundary"),
            }
            for z in (hr_zones or [])
        ],
        "laps": laps[:20],
    }


@mcp.tool()
def get_garmin_daily_health(date: Optional[str] = None) -> Dict[str, Any]:
    """Daily wellness summary: steps, calories, resting HR, active minutes,
    avg/max stress, Body Battery high/low/current, and floors climbed.

    Args:
        date: YYYY-MM-DD (default today).
    """
    d = _date(date)
    g = _api.client()
    stats = _api._call(g.get_stats, d)
    try:
        bb = _api._call(g.get_body_battery, d, d)
        bb_day = bb[0] if bb else {}
    except Exception:
        bb_day = {}

    bb_arr = [pt[1] for pt in (bb_day.get("bodyBatteryValuesArray") or [])
              if pt and len(pt) >= 2 and pt[1] is not None]

    return {
        "date":                   d,
        "steps":                  stats.get("totalSteps"),
        "distance_m":             stats.get("totalDistanceMeters"),
        "active_calories":        stats.get("activeKilocalories"),
        "total_calories":         stats.get("totalKilocalories"),
        "resting_hr":             stats.get("restingHeartRate"),
        "min_hr":                 stats.get("minHeartRate"),
        "max_hr":                 stats.get("maxHeartRate"),
        "avg_stress":             stats.get("averageStressLevel"),
        "max_stress":             stats.get("maxStressLevel"),
        "stress_qualifier":       stats.get("stressQualifier"),
        "intensity_minutes":      (stats.get("moderateIntensityMinutes") or 0) + (stats.get("vigorousIntensityMinutes") or 0) or None,
        "moderate_intensity_min": stats.get("moderateIntensityMinutes"),
        "vigorous_intensity_min": stats.get("vigorousIntensityMinutes"),
        "floors_climbed":         stats.get("floorsAscended"),
        "body_battery_now":       bb_arr[-1] if bb_arr else None,
        "body_battery_max":       max(bb_arr) if bb_arr else None,
        "body_battery_min":       min(bb_arr) if bb_arr else None,
    }


@mcp.tool()
def get_garmin_heart_rate_timeline(date: Optional[str] = None) -> Dict[str, Any]:
    """All-day heart rate in ~15-minute intervals.

    Shows resting HR, min/max, and full timeline — useful for seeing HR response
    to activities, stress spikes, or unusual resting-HR elevation.

    Args:
        date: YYYY-MM-DD (default today).
    """
    d = _date(date)
    g = _api.client()
    data = _api._call(g.get_heart_rates, d)
    timeline = []
    for entry in (data.get("heartRateValues") or []):
        if entry and len(entry) >= 2 and entry[1] is not None:
            try:
                t = datetime.fromtimestamp(entry[0] / 1000).strftime("%H:%M")
            except Exception:
                t = str(entry[0])
            timeline.append({"time": t, "hr": entry[1]})

    return {
        "date":        d,
        "resting_hr":  data.get("restingHeartRate"),
        "min_hr":      data.get("minHeartRate"),
        "max_hr":      data.get("maxHeartRate"),
        "data_points": len(timeline),
        "timeline":    timeline,
    }


@mcp.tool()
def get_garmin_sleep(date: Optional[str] = None) -> Dict[str, Any]:
    """Sleep analysis: total sleep, deep/light/REM/awake minutes, sleep score,
    avg SpO2, avg respiration rate, and HRV during sleep.

    Args:
        date: YYYY-MM-DD (default today).
    """
    d = _date(date)
    g = _api.client()
    raw = _api._call(g.get_sleep_data, d)
    dto = raw.get("dailySleepDTO") or {}
    total_secs = dto.get("sleepTimeSeconds")
    if not total_secs:
        return {
            "date": d,
            "total_sleep_h": None, "deep_h": None, "light_h": None,
            "rem_h": None, "awake_h": None, "sleep_score": None,
            "feedback": None, "avg_spo2": None, "avg_respiration": None,
            "avg_stress": None, "hrv_5min_avg": None, "hrv_overnight_avg": None,
        }

    return {
        "date":              d,
        "total_sleep_h":     _h(total_secs),
        "deep_h":            _h(dto.get("deepSleepSeconds")  or 0),
        "light_h":           _h(dto.get("lightSleepSeconds") or 0),
        "rem_h":             _h(dto.get("remSleepSeconds")   or 0),
        "awake_h":           _h(dto.get("awakeSleepSeconds") or 0),
        "sleep_score":       (dto.get("sleepScores") or {}).get("overall", {}).get("value"),
        "feedback":          dto.get("sleepScoreFeedback"),
        "avg_spo2":          dto.get("averageSpO2Value"),
        "avg_respiration":   dto.get("averageRespirationValue"),
        "avg_stress":        dto.get("averageStressLevel"),
        "hrv_5min_avg":      dto.get("averageHrvValue"),
        "hrv_overnight_avg": dto.get("hmvValue"),
    }


@mcp.tool()
def get_garmin_body_battery(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Body Battery levels over a date range: daily high/low and intraday timeline.

    Shows energy charge vs. drain patterns. Useful for correlating recovery days
    with activity load, sleep quality, and stress.

    Args:
        start_date: YYYY-MM-DD (default 14 days ago).
        end_date: YYYY-MM-DD (default today).
    """
    end   = _date(end_date)
    start = start_date or (
        datetime.strptime(end, "%Y-%m-%d") - timedelta(days=13)
    ).strftime("%Y-%m-%d")
    g = _api.client()

    raw: list = []
    chunk_start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt         = datetime.strptime(end, "%Y-%m-%d")
    while chunk_start_dt <= end_dt:
        chunk_end_dt = min(chunk_start_dt + timedelta(days=_BB_CHUNK_DAYS - 1), end_dt)
        try:
            chunk = _api._call(
                g.get_body_battery,
                chunk_start_dt.strftime("%Y-%m-%d"),
                chunk_end_dt.strftime("%Y-%m-%d"),
            ) or []
            raw.extend(chunk)
        except Exception:
            pass
        chunk_start_dt = chunk_end_dt + timedelta(days=1)

    days = []
    for d in raw:
        timeline = []
        for pt in (d.get("bodyBatteryValuesArray") or []):
            if pt and len(pt) >= 2:
                try:
                    t = datetime.fromtimestamp(pt[0] / 1000).strftime("%H:%M")
                except Exception:
                    t = str(pt[0])
                timeline.append({"time": t, "value": pt[1]})
        vals = [pt["value"] for pt in timeline if pt["value"] is not None]
        days.append({
            "date":    d.get("calendarDate"),
            "charged": d.get("charged"),
            "drained": d.get("drained"),
            "highest": max(vals) if vals else None,
            "lowest":  min(vals) if vals else None,
            "timeline": timeline,
        })

    return {"start_date": start, "end_date": end, "days": days}


@mcp.tool()
def get_garmin_hrv_status(date: Optional[str] = None) -> Dict[str, Any]:
    """HRV status: last-night 5-min high HRV, personal baseline range,
    and readiness status (balanced / unbalanced / low / poor).

    Args:
        date: YYYY-MM-DD (default today).
    """
    d = _date(date)
    g = _api.client()
    raw     = _api._call(g.get_hrv_data, d)
    summary = (raw.get("hrvSummary") or {})

    return {
        "date":                   d,
        "last_night_hrv":         summary.get("lastNight5MinHighHrv"),
        "baseline_low":           summary.get("baselineLowUpper"),
        "baseline_balanced_low":  summary.get("baselineBalancedLow"),
        "baseline_balanced_high": summary.get("baselineBalancedUpper"),
        "status":                 summary.get("status"),
        "feedback":               summary.get("feedbackPhrase"),
    }


@mcp.tool()
def get_garmin_training_metrics(date: Optional[str] = None) -> Dict[str, Any]:
    """Advanced training analytics: VO2max, training load (7/28-day), training
    status (peaking/maintaining/recovery/…), race predictions (5K–marathon),
    and training readiness score.

    Args:
        date: YYYY-MM-DD (default today).
    """
    d = _date(date)
    g = _api.client()
    metrics: Dict[str, Any] = {"date": d}

    try:
        mx = _api._call(g.get_max_metrics, d)
        if mx:
            latest = mx[-1] if isinstance(mx, list) else mx
            metrics["vo2max_running"] = (latest.get("generic") or {}).get("vo2MaxPreciseValue")
            metrics["vo2max_cycling"] = (latest.get("cycling") or {}).get("vo2MaxPreciseValue")
    except Exception:
        pass

    try:
        ts = _api._call(g.get_training_status, d)
        if isinstance(ts, list) and ts:
            ts = ts[-1]
        metrics["training_status"]   = (ts.get("latestTrainingStatus") or {}).get("trainingStatus")
        metrics["training_load_7d"]  = (ts.get("latestTrainingStatus") or {}).get("trainingLoadBalance", {}).get("shortTermTrainingLoad")
        metrics["training_load_28d"] = (ts.get("latestTrainingStatus") or {}).get("trainingLoadBalance", {}).get("longTermTrainingLoad")
    except Exception:
        pass

    try:
        rp = _api._call(g.get_race_predictions)
        if rp:
            p = rp[-1] if isinstance(rp, list) else rp

            def _fmt_time(secs):
                if not secs:
                    return None
                m, s = divmod(int(secs), 60)
                h, m = divmod(m, 60)
                return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

            metrics["race_predictions"] = {
                "5k":            _fmt_time(p.get("time5K")),
                "10k":           _fmt_time(p.get("time10K")),
                "half_marathon": _fmt_time(p.get("timeHalfMarathon")),
                "marathon":      _fmt_time(p.get("timeMarathon")),
            }
    except Exception:
        pass

    try:
        tr = _api._call(g.get_training_readiness, d)
        if isinstance(tr, list) and tr:
            tr = tr[-1]
        metrics["training_readiness_score"] = tr.get("score")
        metrics["training_readiness_level"] = tr.get("levelLabel")
    except Exception:
        pass

    return metrics


@mcp.tool()
def get_garmin_wellness_trends(
    days: int = 14,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Multi-day health trends: daily resting HR, steps, stress, sleep score,
    Body Battery high. Good for spotting fatigue, overtraining, or recovery.

    Use start_date/end_date for a specific historical window (e.g. comparing
    seasons). Use days for a recent rolling window. start_date/end_date take
    priority over days.

    Args:
        days: Past days to include (default 14). Ignored if start_date is set.
        start_date: Start of range YYYY-MM-DD (inclusive). Use with end_date.
        end_date: End of range YYYY-MM-DD (inclusive, default today). Requires start_date.
    """
    g = _api.client()
    if start_date:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
        days_n   = (end_dt - start_dt).days + 1
        end      = end_dt
    else:
        days_n = days
        end    = datetime.now()

    # Body battery: fetch in chunks
    bb_by_date: Dict[str, Any] = {}
    chunk_start_dt = end - timedelta(days=days_n - 1)
    while chunk_start_dt <= end:
        chunk_end_dt = min(chunk_start_dt + timedelta(days=_BB_CHUNK_DAYS - 1), end)
        try:
            chunk_raw = _api._call(
                g.get_body_battery,
                chunk_start_dt.strftime("%Y-%m-%d"),
                chunk_end_dt.strftime("%Y-%m-%d"),
            ) or []
            for item in chunk_raw:
                key = (item.get("calendarDate") or item.get("date") or item.get("startDate"))
                if key:
                    bb_by_date[key] = item
        except Exception:
            pass
        chunk_start_dt = chunk_end_dt + timedelta(days=1)

    # Per-day stats + sleep: fetch in parallel
    def _fetch_day(i: int) -> Tuple[str, Dict[str, Any]]:
        d     = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        entry: Dict[str, Any] = {"date": d}
        try:
            stats = _api._call(g.get_stats, d)
            entry["resting_hr"]   = stats.get("restingHeartRate")
            entry["max_hr"]       = stats.get("maxHeartRate")
            entry["steps"]        = stats.get("totalSteps")
            entry["avg_stress"]   = stats.get("averageStressLevel")
            mod = stats.get("moderateIntensityMinutes") or 0
            vig = stats.get("vigorousIntensityMinutes") or 0
            entry["intensity_min"] = (mod + vig) or None
            entry["active_cal"]   = stats.get("activeKilocalories")
            entry["total_cal"]    = stats.get("totalKilocalories")
        except Exception:
            pass
        try:
            sl  = _api._call(g.get_sleep_data, d)
            dto        = sl.get("dailySleepDTO") or {}
            total_secs = dto.get("sleepTimeSeconds")
            if total_secs:
                entry["sleep_score"]   = (dto.get("sleepScores") or {}).get("overall", {}).get("value")
                entry["total_sleep_h"] = _h(total_secs)
                entry["deep_h"]        = _h(dto.get("deepSleepSeconds")  or 0)
                entry["light_h"]       = _h(dto.get("lightSleepSeconds") or 0)
                entry["rem_h"]         = _h(dto.get("remSleepSeconds")   or 0)
                entry["awake_h"]       = _h(dto.get("awakeSleepSeconds") or 0)
        except Exception:
            pass
        return d, entry

    workers    = min(15, days_n)
    day_results: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_day, i): i for i in range(days_n)}
        for future in as_completed(futures):
            d, entry = future.result()
            day_results[d] = entry

    # Assemble trend in chronological order, inject body battery
    trend = []
    for i in range(days_n - 1, -1, -1):
        d     = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        entry = day_results.get(d, {"date": d})
        bb_day = bb_by_date.get(d)
        if bb_day:
            bb_arr = [pt[1] for pt in (bb_day.get("bodyBatteryValuesArray") or [])
                      if pt and len(pt) >= 2 and pt[1] is not None]
            if bb_arr:
                entry["body_battery_high"] = max(bb_arr)
                entry["body_battery_low"]  = min(bb_arr)
        trend.append(entry)

    def _mean(gen):
        nums = [v for v in gen if v is not None]
        return round(sum(nums) / len(nums), 1) if nums else None

    summary = {
        "avg_sleep_score":       _mean(e.get("sleep_score")       for e in trend),
        "avg_total_sleep_h":     _mean(e.get("total_sleep_h")     for e in trend),
        "avg_deep_h":            _mean(e.get("deep_h")            for e in trend),
        "avg_rem_h":             _mean(e.get("rem_h")             for e in trend),
        "avg_light_h":           _mean(e.get("light_h")           for e in trend),
        "avg_steps":             _mean(e.get("steps")             for e in trend),
        "avg_stress":            _mean(e.get("avg_stress")        for e in trend),
        "avg_resting_hr":        _mean(e.get("resting_hr")        for e in trend),
        "avg_body_battery_high": _mean(e.get("body_battery_high") for e in trend),
    }
    # summary placed BEFORE trend so it survives context truncation downstream
    return {"days": days_n, "summary": summary, "trend": trend}


@mcp.tool()
def get_garmin_steps_timeline(date: Optional[str] = None) -> Dict[str, Any]:
    """Intraday step counts in 15-minute buckets for one day.

    Each bucket also includes the activity level (sleeping, sedentary, active,
    …). Use together with get_garmin_heart_rate_timeline to correlate elevated
    HR with absence of movement (stress, illness, stimulants).

    Args:
        date: YYYY-MM-DD (default today).
    """
    d = _date(date)
    g = _api.client()
    raw = _api._call(g.get_steps_data, d) or []
    buckets = []
    for b in raw:
        start_gmt = b.get("startGMT", "")
        end_gmt   = b.get("endGMT",   "")
        steps     = b.get("steps", 0) or 0
        level     = b.get("primaryActivityLevel", "")
        time_str  = start_gmt[11:16] if len(start_gmt) >= 16 else start_gmt
        buckets.append({
            "time":           time_str,
            "start_gmt":      start_gmt,
            "end_gmt":        end_gmt,
            "steps":          steps,
            "activity_level": level,
        })
    return {"date": d, "buckets_15min": buckets}


@mcp.tool()
def get_garmin_stress_timeline(date: Optional[str] = None) -> Dict[str, Any]:
    """Intraday stress levels throughout the day in ~3-minute intervals.

    Stress scale: 1–25 low, 26–50 medium, 51–75 high, 76–100 very high.
    Values of -1 (sleep/no measurement) are excluded from the timeline.
    Returns avg_stress, max_stress, max_stress_time, and a full timeline.

    Use for 'when was I most stressed?', 'stress pattern before/after a workout',
    or correlating stress with heart rate and steps on the same day.

    Args:
        date: YYYY-MM-DD (default today).
    """
    d = _date(date)
    g = _api.client()
    raw = _api._call(g.get_stress_data, d) or {}

    timeline: List[Dict] = []
    stress_values: List[int] = []
    max_stress = 0
    max_stress_time: Optional[str] = None

    for entry in (raw.get("stressValuesArray") or []):
        if not entry or len(entry) < 2 or entry[1] is None or entry[1] < 0:
            continue
        stress = int(entry[1])
        if stress <= 0:
            continue
        try:
            t = datetime.fromtimestamp(entry[0] / 1000).strftime("%H:%M")
        except Exception:
            t = str(entry[0])
        stress_values.append(stress)
        if stress > max_stress:
            max_stress = stress
            max_stress_time = t
        category = (
            "low"       if stress <= 25 else
            "medium"    if stress <= 50 else
            "high"      if stress <= 75 else
            "very_high"
        )
        timeline.append({"time": t, "stress": stress, "category": category})

    avg_stress = round(sum(stress_values) / len(stress_values)) if stress_values else None

    return {
        "date":            d,
        "avg_stress":      avg_stress,
        "max_stress":      max_stress if max_stress > 0 else None,
        "max_stress_time": max_stress_time,
        "data_points":     len(timeline),
        "timeline":        timeline,
    }


@mcp.tool()
def get_garmin_body_composition(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Weight, BMI, body fat %, and muscle mass from Garmin Connect over a date
    range. Only available when the user has a Garmin-compatible scale or manually
    logs weight. Returns an empty measurements list when no data exists — do not
    retry with a different range in that case.

    Also returns the latest measurement and the weight change (trend_kg) across
    the requested period.

    Args:
        start_date: YYYY-MM-DD (default 30 days ago).
        end_date: YYYY-MM-DD (default today).
    """
    end   = _date(end_date)
    start = start_date or (
        datetime.strptime(end, "%Y-%m-%d") - timedelta(days=29)
    ).strftime("%Y-%m-%d")
    g = _api.client()

    try:
        raw = _api._call(g.get_body_composition, start, end)
    except Exception as exc:
        return {
            "start_date": start, "end_date": end,
            "measurements": [], "latest": None, "trend_kg": None,
            "message": f"Body composition data unavailable: {exc}",
        }

    items = (
        raw.get("dateWeightList")
        if isinstance(raw, dict)
        else (raw if isinstance(raw, list) else [])
    ) or []

    entries = []
    for item in items:
        date_str  = item.get("calendarDate") or item.get("date")
        weight_g  = item.get("weight")
        if not date_str or not weight_g:
            continue
        entries.append({
            "date":           date_str,
            "weight_kg":      round(weight_g / 1000, 2),
            "bmi":            item.get("bmi"),
            "body_fat_pct":   item.get("bodyFat"),
            "muscle_mass_kg": round(item.get("muscleMass") / 1000, 2) if item.get("muscleMass") else None,
            "bone_mass_kg":   round(item.get("boneMass") / 1000, 2) if item.get("boneMass") else None,
        })

    entries.sort(key=lambda x: x["date"])

    if not entries:
        return {
            "start_date": start, "end_date": end,
            "measurements": [], "latest": None, "trend_kg": None,
            "message": "No body composition data found for this period.",
        }

    trend_kg = round(entries[-1]["weight_kg"] - entries[0]["weight_kg"], 2) if len(entries) >= 2 else None
    return {
        "start_date":   start,
        "end_date":     end,
        "measurements": entries,
        "latest":       entries[-1],
        "trend_kg":     trend_kg,
        "count":        len(entries),
    }


@mcp.tool()
def get_activity_gps_track(activity_id: int) -> Dict[str, Any]:
    """Download the GPS track for a Garmin activity as structured lat/lon/elevation
    time-stamped points, parsed from the GPX file.

    Returns the full list of track points with latitude, longitude, elevation (m),
    and UTC timestamp. Use this to render the route on a map or analyse the
    elevation profile of a specific Garmin activity.

    Note: GPS data is only available for activities recorded with a GPS-enabled device.

    Args:
        activity_id: Garmin numeric activity ID (from get_garmin_activities).
    """
    from garminconnect import Garmin as GarminClient  # local import to keep module light

    g = _api.client()

    try:
        raw = _api._call(g.download_activity, activity_id, dl_fmt=GarminClient.ActivityDownloadFormat.GPX)
    except Exception as e:
        return {"error": f"Could not download GPS track for activity {activity_id}: {e}"}

    if not isinstance(raw, (bytes, bytearray)):
        return {"error": f"Unexpected response type from download_activity: {type(raw).__name__}"}

    # garminconnect returns a ZIP file containing the GPX
    gpx_bytes: Optional[bytes] = None
    if raw[:2] == b"PK":  # ZIP magic bytes
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".gpx"):
                        gpx_bytes = zf.read(name)
                        break
        except zipfile.BadZipFile:
            gpx_bytes = raw   # might be raw GPX despite PK prefix
    else:
        gpx_bytes = raw

    if not gpx_bytes:
        return {"error": "No GPX data found in downloaded file"}

    try:
        root = ET.fromstring(gpx_bytes.decode("utf-8"))
    except Exception as e:
        return {"error": f"Failed to parse GPX XML: {e}"}

    # Support both namespaced and bare GPX 1.1
    NS = "{http://www.topografix.com/GPX/1/1}"
    trkpts = root.findall(f".//{NS}trkpt") or root.findall(".//trkpt")

    points = []
    for trkpt in trkpts:
        try:
            lat = float(trkpt.get("lat"))
            lon = float(trkpt.get("lon"))
        except (TypeError, ValueError):
            continue

        ele_el  = trkpt.find(f"{NS}ele")  or trkpt.find("ele")
        time_el = trkpt.find(f"{NS}time") or trkpt.find("time")

        ele     = None
        if ele_el is not None and ele_el.text:
            try:
                ele = round(float(ele_el.text), 1)
            except ValueError:
                pass

        points.append({
            "lat":  lat,
            "lon":  lon,
            "ele":  ele,
            "time": time_el.text if time_el is not None else None,
        })

    if not points:
        return {
            "activity_id":  activity_id,
            "total_points": 0,
            "points":       [],
            "message":      "No GPS track points found — activity may not have GPS data.",
        }

    return {
        "activity_id":  activity_id,
        "total_points": len(points),
        "points":       points,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
