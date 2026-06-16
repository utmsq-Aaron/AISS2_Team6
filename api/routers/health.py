"""Health / status endpoints — drive the sidebar connection dots."""

import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter

from api import connections as conn
from core.config import MCP_SERVERS

router = APIRouter()

# Servers with no per-user auth — service is "connected" whenever reachable.
_NO_AUTH = {"weather", "calendar", "flythrough"}

_LABELS = {
    "strava": "Strava", "garmin": "Garmin", "routes": "Routes",
    "weather": "Open-Meteo", "calendar": "Calendar",
    "telegram": "Telegram", "flythrough": "Flythrough",
}


def _port_open(url: str) -> bool:
    p = urllib.parse.urlparse(url)
    if not p.port:
        return False
    try:
        with socket.create_connection((p.hostname or "127.0.0.1", p.port), timeout=0.4):
            return True
    except OSError:
        return False


def _service_ok(key: str) -> bool:
    if key in _NO_AUTH:
        return True
    return {
        "strava": conn.strava_connected,
        "garmin": conn.garmin_connected,
        "routes": conn.routes_connected,
        "telegram": conn.telegram_connected,
    }.get(key, lambda: False)()


@router.get("/health/servers")
def servers():
    """Per-server status: server process up (TCP) + service configured."""
    with ThreadPoolExecutor(max_workers=len(MCP_SERVERS)) as ex:
        up = dict(zip(MCP_SERVERS.keys(), ex.map(_port_open, MCP_SERVERS.values())))
    return {
        "garmin_mock": conn.garmin_mock_mode(),
        "servers": [
            {
                "key": key,
                "label": _LABELS.get(key, key.capitalize()),
                "server_up": up.get(key, False),
                "service_ok": _service_ok(key),
            }
            for key in MCP_SERVERS
        ],
    }


@router.get("/health/config")
def config():
    """Startup warnings, mirroring ui.shared.validate_config()."""
    issues = []
    if not conn.strava_connected():
        issues.append("Strava not connected — open the ⚙️ Settings tab to connect")
    if not conn.garmin_connected():
        issues.append("Garmin not connected — open the ⚙️ Settings tab to connect or enable mock mode")
    if not conn.openai_configured():
        issues.append("OPENAI_API_KEY not set — AI features disabled")
    return {"issues": issues}
