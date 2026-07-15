"""Public UI config — non-secret feature flags for the SPA (sidebar section
visibility, etc.). Unauthenticated: read independent of login, no secrets."""

import os

from dotenv import dotenv_values
from fastapi import APIRouter

router = APIRouter()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        raw = dotenv_values(".env").get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes")


@router.get("/config")
def get_config():
    """Public, non-secret UI flags. Defaults keep every section visible."""
    return {
        "show_service_status": _flag("SHOW_SERVICE_STATUS", True),
        "show_sport_filter":   _flag("SHOW_SPORT_FILTER", True),
        "show_refresh":        _flag("SHOW_REFRESH", True),
    }
