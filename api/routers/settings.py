"""Settings — integration status, .env editing, and the interactive connect flows
(Strava/Google OAuth, Garmin MFA login, Telegram phone login, bridge, MCP restart).
Faithful port of ui/settings.py onto HTTP endpoints.
"""

import os
from typing import Dict, Optional

from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import connections as conn
from api import settings_service as svc

router = APIRouter()

# Keys editable from the Settings tab and whether to mask them in responses.
_EDITABLE_KEYS = {
    "OPENAI_API_KEY": True, "OPENAI_BASE_URL": False, "AGENT_MODEL": False,
    "CLIENT_ID": False, "CLIENT_SECRET": True,
    "GARMIN_EMAIL": False, "GARMIN_PASSWORD": True, "GARMIN_MOCK_HEALTH": False,
    "ORS_API_KEY": True, "GOOGLE_CLIENT_ID": False, "GOOGLE_CLIENT_SECRET": True,
    "TELEGRAM_API_ID": False, "TELEGRAM_API_HASH": True,
}


def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:3] + "…" + value[-2:] if len(value) > 6 else "•••"


@router.get("/settings")
def get_settings():
    file_vals = dotenv_values(".env")
    env: Dict[str, Dict] = {}
    for key, secret in _EDITABLE_KEYS.items():
        raw = os.getenv(key) or file_vals.get(key) or ""
        env[key] = {"set": bool(raw), "value": _mask(raw) if secret else raw, "secret": secret}
    return {
        "integrations": {
            "strava": conn.strava_connected(),
            "garmin": conn.garmin_connected(),
            "garmin_mock": conn.garmin_mock_mode(),
            "google": conn.google_connected(),
            "routes": conn.routes_connected(),
            "telegram": conn.telegram_connected(),
            "openai": conn.openai_configured(),
        },
        "env": env,
        "models": svc.KIT_MODELS,
        "bridge_running": svc.bridge_running(),
    }


class EnvUpdate(BaseModel):
    values: Dict[str, str]


@router.put("/settings/env")
def put_env(body: EnvUpdate):
    written = []
    for key, value in body.values.items():
        if key not in _EDITABLE_KEYS:
            raise HTTPException(status_code=400, detail=f"Key not editable: {key}")
        svc.save_env(key, value)
        written.append(key)
    return {"written": written}


# ── Strava ────────────────────────────────────────────────────────────────────

@router.post("/settings/strava/connect")
def strava_connect():
    try:
        return {"auth_url": svc.strava_start_flow()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/settings/strava/disconnect")
def strava_disconnect():
    svc.revoke("strava")
    return {"ok": True}


class TokenUpload(BaseModel):
    token: dict


@router.post("/settings/strava/token")
def strava_token(body: TokenUpload):
    try:
        return svc.strava_save_token(body.token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


# ── Google ──────────────────────────────────────────────────────────────────

@router.post("/settings/google/connect")
def google_connect():
    try:
        return {"auth_url": svc.google_start_flow()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/settings/google/disconnect")
def google_disconnect():
    svc.revoke("google")
    return {"ok": True}


# ── Garmin ──────────────────────────────────────────────────────────────────

class GarminLogin(BaseModel):
    email: str
    password: str


@router.post("/settings/garmin/login")
def garmin_login(body: GarminLogin):
    svc.garmin_login(body.email, body.password)
    return {"state": "authenticating"}


@router.get("/settings/garmin/login/status")
def garmin_login_status():
    return svc.garmin_login_status()


class MfaCode(BaseModel):
    code: str


@router.post("/settings/garmin/mfa")
def garmin_mfa(body: MfaCode):
    svc.garmin_submit_mfa(body.code)
    return {"ok": True}


@router.post("/settings/garmin/disconnect")
def garmin_disconnect():
    svc.garmin_disconnect()
    return {"ok": True}


# ── Telegram ──────────────────────────────────────────────────────────────────

class Phone(BaseModel):
    phone: str


@router.post("/settings/telegram/send-code")
def tg_send_code(body: Phone):
    try:
        return svc.tg_send_code(body.phone)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


class TgSignIn(BaseModel):
    inter: str
    phone: str
    code: str
    code_hash: str


@router.post("/settings/telegram/sign-in")
def tg_sign_in(body: TgSignIn):
    try:
        res = svc.tg_sign_in(body.inter, body.phone, body.code, body.code_hash)
        if res.get("status") == "ok":
            svc.save_env("TELEGRAM_SESSION_STRING", res["session"])
        return res
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


class TgPassword(BaseModel):
    inter: str
    password: str


@router.post("/settings/telegram/password")
def tg_password(body: TgPassword):
    try:
        res = svc.tg_password(body.inter, body.password)
        svc.save_env("TELEGRAM_SESSION_STRING", res["session"])
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


class SessionString(BaseModel):
    session: str


@router.post("/settings/telegram/session")
def tg_session(body: SessionString):
    svc.save_env("TELEGRAM_SESSION_STRING", body.session.strip())
    return {"ok": True}


@router.post("/settings/telegram/disconnect")
def tg_disconnect():
    svc.save_env("TELEGRAM_SESSION_STRING", "")
    return {"ok": True}


class BridgeAction(BaseModel):
    action: str  # "start" | "stop"


@router.post("/settings/telegram/bridge")
def tg_bridge(body: BridgeAction):
    if body.action == "start":
        svc.bridge_start()
    elif body.action == "stop":
        svc.bridge_stop()
    else:
        raise HTTPException(status_code=400, detail="action must be start|stop")
    return {"running": svc.bridge_running()}


@router.get("/settings/telegram/bridge/status")
def tg_bridge_status():
    return {"running": svc.bridge_running()}


# ── MCP servers ────────────────────────────────────────────────────────────

@router.post("/settings/servers/restart")
def restart_servers():
    return svc.restart_mcp_servers()
