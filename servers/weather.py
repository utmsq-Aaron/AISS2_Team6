"""
Weather MCP Server — current weather, pollen, and UV index via Open-Meteo.

No API key required (Open-Meteo is free and open).
Default location: Karlsruhe (can be overridden via LAT/LON env vars).
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

from servers._base_server import BaseMCPServer

load_dotenv()

LAT = float(os.getenv("WEATHER_LAT", "49.0069"))
LON = float(os.getenv("WEATHER_LON", "8.4037"))
LOCATION_NAME = os.getenv("WEATHER_LOCATION", "Karlsruhe")

_TIMEOUT = 10


class WeatherMCPServer(BaseMCPServer):
    """Open-Meteo backed server for weather, pollen, and UV index."""

    def list_tools(self) -> List[Dict]:
        return [
            {
                "name": "get_current_weather",
                "description": (
                    f"Fetch current temperature, wind speed, and weather condition for {LOCATION_NAME}."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "get_pollen_levels",
                "description": (
                    f"Fetch current alder, birch, grass, and mugwort pollen levels (Grains/m³) for {LOCATION_NAME}."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "get_uv_index",
                "description": f"Fetch the current UV index for {LOCATION_NAME}.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        ]

    async def call_tool(self, name: str, args: Dict[str, Any]) -> str:
        handlers = {
            "get_current_weather": self._get_weather,
            "get_pollen_levels":   self._get_pollen,
            "get_uv_index":        self._get_uv,
        }
        return json.dumps(handlers[name]())

    # ── Tool implementations ──────────────────────────────────────────────────

    def _get_weather(self) -> Dict:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  LAT,
                "longitude": LON,
                "current":   "temperature_2m,weathercode,windspeed_10m",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        current = resp.json()["current"]
        return {
            "location":          LOCATION_NAME,
            "temperature_c":     current["temperature_2m"],
            "wind_speed_kmh":    current["windspeed_10m"],
            "weather_code":      current["weathercode"],
            "weather_condition": _decode_weather_code(current["weathercode"]),
        }

    def _get_pollen(self) -> Dict:
        resp = requests.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude":     LAT,
                "longitude":    LON,
                "hourly":       "alder_pollen,birch_pollen,grass_pollen,mugwort_pollen",
                "forecast_days": 1,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        hourly = resp.json()["hourly"]
        hour = datetime.now().hour

        pollen_types = ["alder_pollen", "birch_pollen", "grass_pollen", "mugwort_pollen"]
        levels = {}
        for pt in pollen_types:
            values = hourly.get(pt, [])
            val = (
                values[hour]
                if hour < len(values) and values[hour] is not None
                else next((v for v in reversed(values) if v is not None), 0)
            )
            levels[pt] = {"value_grains_m3": val, "level": _pollen_level(val)}

        return {"location": LOCATION_NAME, "pollen": levels}

    def _get_uv(self) -> Dict:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  LAT,
                "longitude": LON,
                "current":   "uv_index",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        uv = resp.json()["current"]["uv_index"]
        return {
            "location": LOCATION_NAME,
            "uv_index": uv,
            "risk":     _uv_risk(uv),
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode_weather_code(code: int) -> str:
    table = {
        0: "Clear sky",
        1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Foggy", 48: "Depositing rime fog",
        51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
        61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
        71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
        95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
    }
    return table.get(code, f"Unknown (code {code})")


def _pollen_level(value: float) -> str:
    if value == 0:      return "none"
    if value <= 10:     return "low"
    if value <= 30:     return "moderate"
    if value <= 100:    return "high"
    return "very high"


def _uv_risk(uv: float) -> str:
    if uv < 3:   return "low"
    if uv < 6:   return "moderate"
    if uv < 8:   return "high"
    if uv < 11:  return "very high"
    return "extreme"
