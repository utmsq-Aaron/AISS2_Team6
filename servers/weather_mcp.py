"""Weather — native FastMCP server (Streamable HTTP).

Self-contained MCP server for current conditions AND forecasts, backed by
Open-Meteo (free, no API key). No BaseMCPServer, no dispatch indirection — the
tools call the API directly. The app reaches it as a plain MCP client via
``core.host.ToolHost``.

Run locally:   python -m servers.weather_mcp
Endpoint:      http://127.0.0.1:8101/mcp   (override host/port via env)
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

LAT = float(os.getenv("WEATHER_LAT", "49.0069"))
LON = float(os.getenv("WEATHER_LON", "8.4037"))
LOCATION = os.getenv("WEATHER_LOCATION", "Karlsruhe")
TIMEOUT = 10

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

HOST = os.getenv("WEATHER_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("WEATHER_MCP_PORT", "8101"))

mcp = FastMCP(
    "weather",
    instructions="Current weather plus multi-day forecast, pollen and UV for the configured city.",
    host=HOST,
    port=PORT,
    stateless_http=True,
)


# ── WMO weather-code / level helpers ──────────────────────────────────────────

_WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ slight hail", 99: "Thunderstorm w/ heavy hail",
}


def _condition(code: int) -> str:
    return _WMO.get(code, f"Unknown (code {code})")


def _pollen_level(v: float) -> str:
    if v == 0: return "none"
    if v <= 10: return "low"
    if v <= 30: return "moderate"
    if v <= 100: return "high"
    return "very high"


def _uv_risk(uv: float) -> str:
    if uv < 3: return "low"
    if uv < 6: return "moderate"
    if uv < 8: return "high"
    if uv < 11: return "very high"
    return "extreme"


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_current_weather() -> Dict[str, Any]:
    """Current temperature, wind speed and sky condition for the configured city.

    Call this when the user asks about the weather, temperature, wind, or whether
    it is raining/clear RIGHT NOW. For a future day, use get_weather_forecast instead.
    """
    resp = requests.get(FORECAST_URL, params={
        "latitude": LAT, "longitude": LON,
        "current": "temperature_2m,weathercode,windspeed_10m",
    }, timeout=TIMEOUT)
    resp.raise_for_status()
    cur = resp.json()["current"]
    return {
        "location": LOCATION,
        "temperature_c": cur["temperature_2m"],
        "wind_speed_kmh": cur["windspeed_10m"],
        "weather_code": cur["weathercode"],
        "weather_condition": _condition(cur["weathercode"]),
    }


@mcp.tool()
def get_weather_forecast(days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
    """Daily weather FORECAST for the coming days (high/low temp, rain chance, wind, max UV).

    Call this when the user asks about the weather on a FUTURE day or the coming days
    ("how is the weather on Friday?", "will it rain this weekend?", "is tomorrow good
    for a long run?").

    Args:
        days: How many days ahead to return (1–16). Default 7.
        date: Optional single day as YYYY-MM-DD; if given, only that day is returned.
    """
    days = max(1, min(int(days or 7), 16))
    resp = requests.get(FORECAST_URL, params={
        "latitude": LAT, "longitude": LON,
        "daily": ("temperature_2m_max,temperature_2m_min,precipitation_sum,"
                  "precipitation_probability_max,windspeed_10m_max,weathercode,uv_index_max"),
        "forecast_days": days,
        "timezone": "auto",
    }, timeout=TIMEOUT)
    resp.raise_for_status()
    d = resp.json()["daily"]

    out: List[Dict[str, Any]] = []
    for i, day in enumerate(d["time"]):
        out.append({
            "date": day,
            "temp_min_c": d["temperature_2m_min"][i],
            "temp_max_c": d["temperature_2m_max"][i],
            "precip_mm": d["precipitation_sum"][i],
            "precip_probability_pct": d["precipitation_probability_max"][i],
            "wind_max_kmh": d["windspeed_10m_max"][i],
            "uv_index_max": d["uv_index_max"][i],
            "weather_code": d["weathercode"][i],
            "weather_condition": _condition(d["weathercode"][i]),
        })

    if date:
        out = [day for day in out if day["date"] == date]
        if not out:
            return {"location": LOCATION, "error": f"No forecast for {date} within {days} days."}

    return {"location": LOCATION, "forecast": out}


@mcp.tool()
def get_pollen_levels() -> Dict[str, Any]:
    """Current alder, birch, grass and mugwort pollen levels (Grains/m³).

    Call this when the user asks about pollen, allergies, or hay-fever conditions.
    """
    resp = requests.get(AIR_URL, params={
        "latitude": LAT, "longitude": LON,
        "hourly": "alder_pollen,birch_pollen,grass_pollen,mugwort_pollen",
        "forecast_days": 1,
    }, timeout=TIMEOUT)
    resp.raise_for_status()
    hourly = resp.json()["hourly"]
    hour = datetime.now().hour

    levels = {}
    for pt in ("alder_pollen", "birch_pollen", "grass_pollen", "mugwort_pollen"):
        vals = hourly.get(pt, [])
        val = (vals[hour] if hour < len(vals) and vals[hour] is not None
               else next((v for v in reversed(vals) if v is not None), 0))
        levels[pt] = {"value_grains_m3": val, "level": _pollen_level(val)}

    return {"location": LOCATION, "pollen": levels}


@mcp.tool()
def get_uv_index() -> Dict[str, Any]:
    """Current UV index and its WHO risk category.

    Call this when the user asks about UV exposure, sunburn risk, or sun protection
    right now. For a future day's max UV, use get_weather_forecast.
    """
    resp = requests.get(FORECAST_URL, params={
        "latitude": LAT, "longitude": LON, "current": "uv_index",
    }, timeout=TIMEOUT)
    resp.raise_for_status()
    uv = resp.json()["current"]["uv_index"]
    return {"location": LOCATION, "uv_index": uv, "risk": _uv_risk(uv)}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
