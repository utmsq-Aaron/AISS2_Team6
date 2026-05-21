#!/usr/bin/env python3
"""
Garmin Connect MCP Server — JSON-RPC interface for health and activity data.

Provides 9 tools covering activities, daily health stats, heart rate timelines,
sleep analysis, Body Battery, HRV status, training metrics, and wellness trends.

Prerequisites: run `python auth/garmin_setup.py` once to cache credentials.
"""

import asyncio
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

TOKEN_STORE    = ".tokens"
_BB_CHUNK_DAYS = 28   # Garmin body-battery API rejects date ranges larger than this


# ── Garmin API client ─────────────────────────────────────────────────────────

class GarminAPI:
    """Lazy-loading wrapper around garminconnect that reuses cached OAuth tokens.

    Thread-safe: uses a lock for initialization so parallel orchestrator calls
    don't race during first login.
    """

    def __init__(self) -> None:
        self._client = None
        self._lock = threading.Lock()

    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:  # double-checked locking
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

    def _call(self, fn, *args, **kwargs):
        """Call a garminconnect method, wrapping any exception."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise RuntimeError(f"Garmin API error: {e}") from e


garmin_api = GarminAPI()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def _date(s: Optional[str]) -> str:
    return s if s else _today()

def _pace(speed_kmh: float) -> Optional[float]:
    """Return pace in min/km (float, 2 dp), or None when speed is zero/missing."""
    if not speed_kmh:
        return None
    return round(60.0 / speed_kmh, 2)

def _h(seconds: int) -> float:
    return round(seconds / 3600, 2)


# ── MCP Server ────────────────────────────────────────────────────────────────

class GarminMCPServer:
    """JSON-RPC MCP server exposing 9 Garmin health and activity tools."""

    def __init__(self) -> None:
        self.tools = [
            {
                "name": "get_garmin_activities",
                "description": (
                    "List Garmin activities (runs, hikes, rides, …) with distance, duration, "
                    "avg/max HR, calories, elevation, and training effect. Supports date filtering."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit":      {"type": "integer", "description": "Max activities (default 50)"},
                        "start":      {"type": "integer", "description": "Pagination offset (default 0)"},
                        "start_date": {"type": "string",  "description": "Filter from YYYY-MM-DD"},
                        "end_date":   {"type": "string",  "description": "Filter to YYYY-MM-DD"},
                    },
                    "required": [],
                },
            },
            {
                "name": "get_garmin_activity_detail",
                "description": (
                    "Full detail for one Garmin activity: per-lap splits, HR zone breakdown, "
                    "training effect, power zones."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "activity_id": {"type": "integer", "description": "Garmin activity ID"},
                    },
                    "required": ["activity_id"],
                },
            },
            {
                "name": "get_garmin_daily_health",
                "description": (
                    "Daily wellness summary: steps, calories, resting HR, active minutes, "
                    "avg stress, Body Battery high/low, floors climbed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                    },
                    "required": [],
                },
            },
            {
                "name": "get_garmin_heart_rate_timeline",
                "description": (
                    "All-day heart rate in ~15-minute intervals. Shows resting HR, min/max, "
                    "and full timeline — useful for seeing HR response to activities or stress."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                    },
                    "required": [],
                },
            },
            {
                "name": "get_garmin_sleep",
                "description": (
                    "Sleep analysis: total sleep, deep/light/REM/awake minutes, sleep score, "
                    "avg SpO2, avg respiration rate, HRV during sleep."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                    },
                    "required": [],
                },
            },
            {
                "name": "get_garmin_body_battery",
                "description": (
                    "Body Battery levels over a date range: daily high/low and intraday timeline. "
                    "Shows energy charge vs. drain patterns."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "YYYY-MM-DD (default 14 days ago)"},
                        "end_date":   {"type": "string", "description": "YYYY-MM-DD (default today)"},
                    },
                    "required": [],
                },
            },
            {
                "name": "get_garmin_hrv_status",
                "description": (
                    "HRV status: last-night 5-min high HRV, personal baseline range, "
                    "and readiness status (balanced / unbalanced / low / poor)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                    },
                    "required": [],
                },
            },
            {
                "name": "get_garmin_training_metrics",
                "description": (
                    "Advanced training analytics: VO2max, training load (7/28-day), "
                    "training status (peaking/maintaining/recovery/…), race predictions "
                    "(5K, 10K, half, marathon), and training readiness score."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                    },
                    "required": [],
                },
            },
            {
                "name": "get_garmin_wellness_trends",
                "description": (
                    "Multi-day health trends: daily resting HR, steps, stress, sleep score, "
                    "Body Battery high. Good for spotting fatigue, overtraining, or recovery. "
                    "Use start_date/end_date for a specific historical window (e.g. comparing seasons). "
                    "Use days for a recent rolling window. start_date/end_date take priority over days."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "days":       {"type": "integer", "description": "Past days to include (default 14). Ignored if start_date is set."},
                        "start_date": {"type": "string",  "description": "Start of range YYYY-MM-DD (inclusive). Use with end_date."},
                        "end_date":   {"type": "string",  "description": "End of range YYYY-MM-DD (inclusive, default today). Requires start_date."},
                    },
                    "required": [],
                },
            },
            {
                "name": "get_garmin_steps_timeline",
                "description": (
                    "Intraday step counts in 15-minute buckets for one day. Each bucket also "
                    "includes the activity level (sleeping, sedentary, active, …). "
                    "Use together with get_garmin_heart_rate_timeline to correlate elevated HR "
                    "with absence of movement (stress, illness, stimulants)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                    },
                    "required": [],
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

    async def _dispatch(self, tool_name: str, args: Dict[str, Any]) -> str:
        print(f"[garmin] {tool_name}({json.dumps(args)})", file=sys.stderr)
        handlers = {
            "get_garmin_activities":          self._get_activities,
            "get_garmin_activity_detail":     self._get_activity_detail,
            "get_garmin_daily_health":        self._get_daily_health,
            "get_garmin_heart_rate_timeline": self._get_hr_timeline,
            "get_garmin_sleep":               self._get_sleep,
            "get_garmin_body_battery":        self._get_body_battery,
            "get_garmin_hrv_status":          self._get_hrv_status,
            "get_garmin_training_metrics":    self._get_training_metrics,
            "get_garmin_wellness_trends":     self._get_wellness_trends,
            "get_garmin_steps_timeline":      self._get_steps_timeline,
        }
        if tool_name not in handlers:
            raise ValueError(f"Unknown Garmin tool: {tool_name}")
        return await handlers[tool_name](args)

    # ── Tool implementations ──────────────────────────────────────────────────

    async def _get_activities(self, args: Dict) -> str:
        limit = args.get("limit", 50)
        start = args.get("start", 0)
        start_date = args.get("start_date")
        end_date = args.get("end_date")
        g = garmin_api.client()

        if start_date or end_date:
            raw = garmin_api._call(g.get_activities_by_date,
                                   start_date or "2010-01-01", end_date or _today())
        else:
            raw = garmin_api._call(g.get_activities, start, limit)

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
                "training_effect":  a.get("aerobicTrainingEffect"),
                "steps":            a.get("steps"),
            })
        return json.dumps({"total": len(rows), "activities": rows}, indent=2)

    async def _get_activity_detail(self, args: Dict) -> str:
        aid = int(args["activity_id"])
        g = garmin_api.client()
        summary = garmin_api._call(g.get_activity, aid)
        try:
            details = garmin_api._call(g.get_activity_details, aid)
        except Exception:
            details = {}
        try:
            hr_zones = garmin_api._call(g.get_activity_hr_in_timezones, aid)
        except Exception:
            hr_zones = []

        laps = []
        for lap in (details.get("activityDetailMetrics") or []):
            if isinstance(lap, dict) and lap.get("lapIndex") is not None:
                spd = round((lap.get("averageSpeed") or 0) * 3.6, 2)
                laps.append({
                    "lap":            lap.get("lapIndex"),
                    "distance_km":    round((lap.get("distance") or 0) / 1000, 2),
                    "time_min":       round((lap.get("duration") or 0) / 60, 1),
                    "avg_speed_kmh":  spd,
                    "pace_min_per_km": _pace(spd),
                    "avg_hr":         lap.get("averageHR"),
                    "elevation_m":    lap.get("elevationGain"),
                })

        atype = (summary.get("activityType") or {}).get("typeKey", "unknown")
        spd   = round((summary.get("averageSpeed") or 0) * 3.6, 2)
        return json.dumps({
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
            "training_effect":  summary.get("aerobicTrainingEffect"),
            "anaerobic_effect": summary.get("anaerobicTrainingEffect"),
            "steps":            summary.get("steps"),
            "avg_cadence":      summary.get("averageBikingCadenceInRevPerMin") or summary.get("averageRunningCadenceInStepsPerMin"),
            "avg_power":        summary.get("avgPower"),
            "normalized_power": summary.get("normPower"),
            "hr_zones": [
                {"zone": z.get("zoneNumber"), "time_min": round((z.get("secsInZone") or 0) / 60, 1), "hr_low": z.get("zoneLowBoundary")}
                for z in (hr_zones or [])
            ],
            "laps": laps[:20],
        }, indent=2)

    async def _get_daily_health(self, args: Dict) -> str:
        date = _date(args.get("date"))
        g = garmin_api.client()
        stats = garmin_api._call(g.get_stats, date)
        try:
            bb = garmin_api._call(g.get_body_battery, date, date)
            bb_day = bb[0] if bb else {}
        except Exception:
            bb_day = {}

        # Extract battery levels from intraday array (API has no highestValue/lowestValue)
        bb_arr = [pt[1] for pt in (bb_day.get("bodyBatteryValuesArray") or [])
                  if pt and len(pt) >= 2 and pt[1] is not None]
        body_battery_now = bb_arr[-1] if bb_arr else None
        body_battery_max = max(bb_arr) if bb_arr else None
        body_battery_min = min(bb_arr) if bb_arr else None

        return json.dumps({
            "date":                   date,
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
            "body_battery_now":       body_battery_now,
            "body_battery_max":       body_battery_max,
            "body_battery_min":       body_battery_min,
        }, indent=2)

    async def _get_hr_timeline(self, args: Dict) -> str:
        date = _date(args.get("date"))
        g = garmin_api.client()
        data = garmin_api._call(g.get_heart_rates, date)
        timeline = []
        for entry in (data.get("heartRateValues") or []):
            if entry and len(entry) >= 2 and entry[1] is not None:
                try:
                    t = datetime.fromtimestamp(entry[0] / 1000).strftime("%H:%M")
                except Exception:
                    t = str(entry[0])
                timeline.append({"time": t, "hr": entry[1]})

        return json.dumps({
            "date":        date,
            "resting_hr":  data.get("restingHeartRate"),
            "min_hr":      data.get("minHeartRate"),
            "max_hr":      data.get("maxHeartRate"),
            "data_points": len(timeline),
            "timeline":    timeline,
        }, indent=2)

    async def _get_sleep(self, args: Dict) -> str:
        date = _date(args.get("date"))
        g = garmin_api.client()
        raw = garmin_api._call(g.get_sleep_data, date)
        dto = raw.get("dailySleepDTO") or {}
        total_secs = dto.get("sleepTimeSeconds")
        if not total_secs:
            return json.dumps({
                "date": date,
                "total_sleep_h": None, "deep_h": None, "light_h": None,
                "rem_h": None, "awake_h": None, "sleep_score": None,
                "feedback": None, "avg_spo2": None, "avg_respiration": None,
                "avg_stress": None, "hrv_5min_avg": None, "hrv_overnight_avg": None,
            }, indent=2)

        return json.dumps({
            "date":              date,
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
        }, indent=2)

    async def _get_body_battery(self, args: Dict) -> str:
        end = _date(args.get("end_date"))
        start = args.get("start_date") or (
            datetime.strptime(end, "%Y-%m-%d") - timedelta(days=13)
        ).strftime("%Y-%m-%d")
        g = garmin_api.client()
        raw = garmin_api._call(g.get_body_battery, start, end)

        days = []
        for d in (raw or []):
            timeline = []
            for pt in (d.get("bodyBatteryValuesArray") or []):
                if pt and len(pt) >= 2:
                    try:
                        t = datetime.fromtimestamp(pt[0] / 1000).strftime("%H:%M")
                    except Exception:
                        t = str(pt[0])
                    timeline.append({"time": t, "value": pt[1]})
            days.append({
                "date":    d.get("calendarDate"),
                "charged": d.get("charged"),
                "drained": d.get("drained"),
                "highest": d.get("highestValue"),
                "lowest":  d.get("lowestValue"),
                "timeline": timeline,
            })

        return json.dumps({"start_date": start, "end_date": end, "days": days}, indent=2)

    async def _get_hrv_status(self, args: Dict) -> str:
        date = _date(args.get("date"))
        g = garmin_api.client()
        raw = garmin_api._call(g.get_hrv_data, date)
        summary = (raw.get("hrvSummary") or {})

        return json.dumps({
            "date":                    date,
            "last_night_hrv":          summary.get("lastNight5MinHighHrv"),
            "baseline_low":            summary.get("baselineLowUpper"),
            "baseline_balanced_low":   summary.get("baselineBalancedLow"),
            "baseline_balanced_high":  summary.get("baselineBalancedUpper"),
            "status":                  summary.get("status"),
            "feedback":                summary.get("feedbackPhrase"),
        }, indent=2)

    async def _get_training_metrics(self, args: Dict) -> str:
        date = _date(args.get("date"))
        g = garmin_api.client()
        metrics: Dict[str, Any] = {"date": date}

        try:
            mx = garmin_api._call(g.get_max_metrics, date)
            if mx:
                latest = mx[-1] if isinstance(mx, list) else mx
                metrics["vo2max_running"] = (latest.get("generic") or {}).get("vo2MaxPreciseValue")
                metrics["vo2max_cycling"] = (latest.get("cycling") or {}).get("vo2MaxPreciseValue")
        except Exception:
            pass

        try:
            ts = garmin_api._call(g.get_training_status, date)
            if isinstance(ts, list) and ts:
                ts = ts[-1]
            metrics["training_status"]   = (ts.get("latestTrainingStatus") or {}).get("trainingStatus")
            metrics["training_load_7d"]  = (ts.get("latestTrainingStatus") or {}).get("trainingLoadBalance", {}).get("shortTermTrainingLoad")
            metrics["training_load_28d"] = (ts.get("latestTrainingStatus") or {}).get("trainingLoadBalance", {}).get("longTermTrainingLoad")
        except Exception:
            pass

        try:
            rp = garmin_api._call(g.get_race_predictions)
            if rp:
                p = rp[-1] if isinstance(rp, list) else rp
                def _fmt_time(secs):
                    if not secs:
                        return None
                    m, s = divmod(int(secs), 60)
                    h, m = divmod(m, 60)
                    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                metrics["race_predictions"] = {
                    "5k":           _fmt_time(p.get("time5K")),
                    "10k":          _fmt_time(p.get("time10K")),
                    "half_marathon": _fmt_time(p.get("timeHalfMarathon")),
                    "marathon":     _fmt_time(p.get("timeMarathon")),
                }
        except Exception:
            pass

        try:
            tr = garmin_api._call(g.get_training_readiness, date)
            if isinstance(tr, list) and tr:
                tr = tr[-1]
            metrics["training_readiness_score"] = tr.get("score")
            metrics["training_readiness_level"] = tr.get("levelLabel")
        except Exception:
            pass

        return json.dumps(metrics, indent=2)

    async def _get_wellness_trends(self, args: Dict) -> str:
        g = garmin_api.client()
        if args.get("start_date"):
            start_dt = datetime.strptime(args["start_date"], "%Y-%m-%d")
            end_dt   = datetime.strptime(args["end_date"], "%Y-%m-%d") if args.get("end_date") else datetime.now()
            days_n   = (end_dt - start_dt).days + 1
            end      = end_dt
        else:
            days_n = args.get("days", 14)
            end    = datetime.now()

        # ── Body battery: fetch in chunks (API rejects ranges > _BB_CHUNK_DAYS) ──
        bb_by_date: Dict[str, Any] = {}
        chunk_start_dt = end - timedelta(days=days_n - 1)
        while chunk_start_dt <= end:
            chunk_end_dt = min(chunk_start_dt + timedelta(days=_BB_CHUNK_DAYS - 1), end)
            try:
                chunk_raw = garmin_api._call(
                    g.get_body_battery,
                    chunk_start_dt.strftime("%Y-%m-%d"),
                    chunk_end_dt.strftime("%Y-%m-%d"),
                ) or []
                for item in chunk_raw:
                    key = (item.get("calendarDate") or item.get("date")
                           or item.get("startDate"))
                    if key:
                        bb_by_date[key] = item
            except Exception:
                pass
            chunk_start_dt = chunk_end_dt + timedelta(days=1)

        # ── Per-day stats + sleep: fetch in parallel ──────────────────────────
        def _fetch_day(i: int) -> Tuple[str, Dict[str, Any]]:
            d     = (end - timedelta(days=i)).strftime("%Y-%m-%d")
            entry: Dict[str, Any] = {"date": d}
            try:
                stats = garmin_api._call(g.get_stats, d)
                entry["resting_hr"]    = stats.get("restingHeartRate")
                entry["max_hr"]        = stats.get("maxHeartRate")
                entry["steps"]         = stats.get("totalSteps")
                entry["avg_stress"]    = stats.get("averageStressLevel")
                mod = stats.get("moderateIntensityMinutes") or 0
                vig = stats.get("vigorousIntensityMinutes") or 0
                entry["intensity_min"] = (mod + vig) or None
                entry["active_cal"]    = stats.get("activeKilocalories")
                entry["total_cal"]     = stats.get("totalKilocalories")
            except Exception:
                pass
            try:
                sl  = garmin_api._call(g.get_sleep_data, d)
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

        workers = min(15, days_n)  # stay within Garmin's unofficial rate limits
        day_results: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_day, i): i for i in range(days_n)}
            for future in as_completed(futures):
                d, entry = future.result()
                day_results[d] = entry

        # ── Assemble trend in chronological order, inject body battery ────────
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

        return json.dumps({"days": days_n, "trend": trend}, indent=2)

    async def _get_steps_timeline(self, args: Dict) -> str:
        date = _date(args.get("date"))
        g = garmin_api.client()
        raw = garmin_api._call(g.get_steps_data, date) or []
        buckets = []
        for b in raw:
            start_gmt = b.get("startGMT", "")
            end_gmt   = b.get("endGMT",   "")
            steps     = b.get("steps", 0) or 0
            level     = b.get("primaryActivityLevel", "")
            # Extract local HH:MM from the GMT timestamp (server stores local time in the GMT field)
            time_str  = start_gmt[11:16] if len(start_gmt) >= 16 else start_gmt
            buckets.append({
                "time":           time_str,
                "start_gmt":      start_gmt,
                "end_gmt":        end_gmt,
                "steps":          steps,
                "activity_level": level,
            })
        return json.dumps({"date": date, "buckets_15min": buckets}, indent=2)


# ── Subprocess entry point ────────────────────────────────────────────────────

async def _main() -> None:
    print("Garmin MCP Server started.", file=sys.stderr)
    server = GarminMCPServer()
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
