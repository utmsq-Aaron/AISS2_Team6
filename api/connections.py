"""Service-connection checks — Streamlit-free ports of the helpers in ui/shared.py.

These reflect *configuration presence* (token files / env), not a live ping, and
are read fresh each call so they pick up edits made via the Settings tab. Paths are
relative to the process CWD, so the API must run from the project root.
"""

import json
import os
from pathlib import Path

from dotenv import dotenv_values


def strava_connected() -> bool:
    token_path = Path(".tokens/strava.json")
    if not token_path.is_file():
        return False
    try:
        return bool(json.loads(token_path.read_text()).get("access_token"))
    except Exception:
        return False


def garmin_mock_mode() -> bool:
    file_vals = dotenv_values(".env")
    flag = os.getenv("GARMIN_MOCK_HEALTH") or file_vals.get("GARMIN_MOCK_HEALTH", "false")
    return str(flag).lower() in ("1", "true", "yes")


def garmin_connected() -> bool:
    if garmin_mock_mode():
        return True
    token_dir = Path(".tokens")
    if not token_dir.is_dir():
        return False
    excluded = {"strava.json", "google.json"}
    return any(
        f.is_file() and f.suffix in (".json", ".txt", "") and f.name not in excluded
        for f in token_dir.iterdir()
    )


def google_connected() -> bool:
    return Path(".tokens/google.json").is_file()


def routes_connected() -> bool:
    return bool(os.getenv("ORS_API_KEY", "") or dotenv_values(".env").get("ORS_API_KEY", ""))


def telegram_connected() -> bool:
    file_vals = dotenv_values(".env")

    def _real(v: str) -> bool:
        return bool(v) and not v.startswith("your_")

    def _get(k: str) -> str:
        return os.getenv(k) or file_vals.get(k) or ""

    return all(_real(_get(k)) for k in
               ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_STRING"))


def google_maps_connected() -> bool:
    v = os.getenv("GOOGLE_MAPS_API_KEY") or dotenv_values(".env").get("GOOGLE_MAPS_API_KEY") or ""
    return bool(v) and not v.startswith("your_")


def openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") or dotenv_values(".env").get("OPENAI_API_KEY", ""))
