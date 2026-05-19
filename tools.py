import requests
from datetime import datetime

LAT = 49.0069
LON = 8.4037


def get_weather():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "temperature_2m,weathercode,windspeed_10m",
    }
    response = requests.get(url, params=params, timeout=10)
    current = response.json()["current"]
    return (
        f"Temperature: {current['temperature_2m']}°C, "
        f"Wind speed: {current['windspeed_10m']} km/h, "
        f"Weather code: {current['weathercode']}"
    )


def get_pollen():
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "alder_pollen,birch_pollen,grass_pollen,mugwort_pollen",
        "forecast_days": 1,
    }
    response = requests.get(url, params=params, timeout=10)
    hourly = response.json()["hourly"]

    current_hour = datetime.now().hour
    result = {}
    for pollen_type in ["alder_pollen", "birch_pollen", "grass_pollen", "mugwort_pollen"]:
        values = hourly.get(pollen_type, [])
        if current_hour < len(values) and values[current_hour] is not None:
            result[pollen_type] = values[current_hour]
        else:
            result[pollen_type] = next((v for v in reversed(values) if v is not None), 0)

    lines = [f"{key.replace('_', ' ').title()}: {value} Grains/m³" for key, value in result.items()]
    return "\n".join(lines)


def get_uv_index():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "uv_index",
    }
    response = requests.get(url, params=params, timeout=10)
    uv = response.json()["current"]["uv_index"]
    return f"UV Index: {uv}"


TOOLS = {
    "check_weather.md": get_weather,
    "check_pollen.md": get_pollen,
    "check_uv_index.md": get_uv_index,
    "general_wisdom.md": lambda: "",
}
