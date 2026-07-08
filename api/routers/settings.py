"""Settings — integration status, .env editing, and the interactive connect flows
(Strava/Google OAuth, Garmin MFA login, Telegram phone login, bridge, MCP restart).
Faithful port of ui/settings.py onto HTTP endpoints.
"""

import os
from typing import Dict, Optional

from dotenv import dotenv_values
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api import auth as A
from api import connections as conn
from api import settings_service as svc
from api.auth import current_user, require_admin

router = APIRouter()

# Public router (mounted WITHOUT the Bearer-token guard): Google redirects the
# browser straight to the callback with no Authorization header, so this one
# route must be reachable unauthenticated. CSRF is covered by the OAuth `state`.
public_router = APIRouter()

# Keys editable from the Settings tab and whether to mask them in responses.
_EDITABLE_KEYS = {
    "LLM_PROVIDER": False,
    "OPENAI_API_KEY": True, "OPENAI_BASE_URL": False, "AGENT_MODEL": False, "AGENT_LLM_MODEL": False,
    "OPENAI_OFFICIAL_API_KEY": True, "OPENAI_OFFICIAL_BASE_URL": False, "OPENAI_MODEL": False,
    "GEMINI_API_KEY": True, "GEMINI_MODEL": False,
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
def get_settings(user: str = Depends(current_user)):
    integrations = {
        "strava": conn.strava_connected(),
        "garmin": conn.garmin_connected(),
        "garmin_mock": conn.garmin_mock_mode(),
        "google": conn.google_connected(),
        "routes": conn.routes_connected(),
        "telegram": conn.telegram_connected(),
        "openai": conn.openai_configured(),
    }
    admin = A.is_admin(user)
    file_vals = dotenv_values(".env")
    env: Dict[str, Dict] = {}
    for key, secret in _EDITABLE_KEYS.items():
        raw = os.getenv(key) or file_vals.get(key) or ""
        if admin:
            # Admin sees non-secret values (and masked secrets) so it can edit them.
            env[key] = {"set": bool(raw), "value": _mask(raw) if secret else raw, "secret": secret}
        else:
            # Non-admins get only presence flags (no values at all) — enough for the
            # Strava/Garmin/Calendar cards to show "Connect" vs "needs setup", without
            # leaking keys, base URLs, or the shared Garmin email.
            env[key] = {"set": bool(raw), "value": "", "secret": secret}
    return {
        "integrations": integrations,
        "is_admin": admin,
        "env": env,
        "models": svc.KIT_MODELS if admin else [],
        "gemini_models": svc.GEMINI_MODELS if admin else [],
        "openai_models": svc.OPENAI_MODELS if admin else [],
        "bridge_running": svc.bridge_running() if admin else False,
    }


_PROVIDERS = {"openai", "openai_official", "gemini"}


@router.get("/settings/models/{provider}")
def list_models(provider: str):
    """Live model list for a provider (chat models), with static fallback."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unknown provider: {provider}")
    return svc.list_models(provider)


class EnvUpdate(BaseModel):
    values: Dict[str, str]


@router.put("/settings/env")
def put_env(body: EnvUpdate, _admin: str = Depends(require_admin)):
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


def _google_result_page(title: str, body: str, color: str) -> HTMLResponse:
    return HTMLResponse(
        "<html><head><meta charset='utf-8'><title>FitDash · Google</title></head>"
        "<body style='font-family:sans-serif;text-align:center;padding:60px'>"
        f"<h1 style='color:{color}'>{title}</h1><p>{body}</p>"
        "<script>setTimeout(function(){window.close()},3000)</script>"
        "</body></html>"
    )


@public_router.get("/settings/google/callback")
def google_callback(code: str = "", state: str = "", error: str = ""):
    """Google's OAuth redirect target (public — no Bearer token on a browser redirect)."""
    if error:
        return _google_result_page("Google sign-in cancelled", error, "#d33")
    try:
        svc.google_handle_callback(code, state)
    except Exception as exc:  # noqa: BLE001 — show the reason in the browser tab
        return _google_result_page("Connection failed", str(exc), "#d33")
    return _google_result_page(
        "✓ Google Calendar connected!",
        "You can close this window and return to FitDash.", "#4285F4")


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
def tg_send_code(body: Phone, _admin: str = Depends(require_admin)):
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
def tg_sign_in(body: TgSignIn, _admin: str = Depends(require_admin)):
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
def tg_password(body: TgPassword, _admin: str = Depends(require_admin)):
    try:
        res = svc.tg_password(body.inter, body.password)
        svc.save_env("TELEGRAM_SESSION_STRING", res["session"])
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


class SessionString(BaseModel):
    session: str


@router.post("/settings/telegram/session")
def tg_session(body: SessionString, _admin: str = Depends(require_admin)):
    svc.save_env("TELEGRAM_SESSION_STRING", body.session.strip())
    return {"ok": True}


@router.post("/settings/telegram/disconnect")
def tg_disconnect(_admin: str = Depends(require_admin)):
    svc.save_env("TELEGRAM_SESSION_STRING", "")
    return {"ok": True}


class BridgeAction(BaseModel):
    action: str  # "start" | "stop"


@router.post("/settings/telegram/bridge")
def tg_bridge(body: BridgeAction, _admin: str = Depends(require_admin)):
    if body.action == "start":
        svc.bridge_start()
    elif body.action == "stop":
        svc.bridge_stop()
    else:
        raise HTTPException(status_code=400, detail="action must be start|stop")
    return {"running": svc.bridge_running()}


@router.get("/settings/telegram/bridge/status")
def tg_bridge_status(_admin: str = Depends(require_admin)):
    return {"running": svc.bridge_running()}


# ── MCP servers ────────────────────────────────────────────────────────────

@router.post("/settings/servers/restart")
def restart_servers(_admin: str = Depends(require_admin)):
    return svc.restart_mcp_servers()
