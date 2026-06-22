"""Settings backend — Streamlit-free port of ui/settings.py's flows.

Handles the stateful interactive flows (Strava/Google OAuth via a local callback
server, Garmin credential login with MFA over a background thread, Telegram phone
login, the agent bridge, and MCP-server restart). State that outlived a Streamlit
rerun (module-level dicts) is preserved here as module-level state.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv, set_key

ENV_FILE = Path(".env")
TOKENS = Path(".tokens")

# KIT gateway model choices surfaced in the OpenAI/LLM card (provider=openai).
KIT_MODELS = [
    "kit.gpt-5.1", "kit.gpt-5", "kit.gpt-5-mini", "kit.gpt-5-nano",
    "kit.gpt-4.1", "kit.gpt-4.1-mini", "kit.gpt-4.1-nano",
    "meta-llama-3.1-8b-instruct", "qwen3.5-397b-a17b", "qwen3.5-122b-a10b",
    "qwen3.5-35b-a3b", "qwen3.5-27b", "qwen3-30b-a3b-instruct-2507",
    "kit.minimax-m2.7-229b", "minimax-m2.5-229b", "openai-gpt-oss-120b",
    "teuken-7b-instruct-research", "glm-4.7",
]

# Google Gemini free-tier flash models surfaced when provider=gemini.
GEMINI_MODELS = [
    "gemini-2.0-flash", "gemini-2.5-flash", "gemini-flash-latest",
    "gemini-2.0-flash-lite", "gemini-2.5-flash-lite", "gemini-flash-lite-latest",
]

# Official OpenAI (api.openai.com) models surfaced when provider=openai_official.
OPENAI_MODELS = [
    "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "gpt-4.1-nano",
    "gpt-5-mini", "gpt-5", "o4-mini",
]

# Curated static fallback per provider (used when the live /models call fails).
_STATIC_MODELS = {"openai": KIT_MODELS, "openai_official": OPENAI_MODELS, "gemini": GEMINI_MODELS}
# Substrings that mark non-chat models (embeddings, audio, image, …) — filtered out.
_NON_CHAT = ("embedding", "whisper", "tts", "dall-e", "image", "moderation",
             "audio", "transcribe", "rerank", "guard", "bert", "aqa", "veo", "imagen")


def list_models(provider: str) -> Dict[str, Any]:
    """Fetch the live model list from a provider's /models endpoint (chat models only).

    Falls back to the curated static list on any error so the picker is never empty.
    Uses the saved .env credentials for that provider.
    """
    fallback = _STATIC_MODELS.get(provider) or KIT_MODELS
    try:
        from core.llm import client_for
        raw = client_for(provider).models.list()
        ids = set()
        for m in getattr(raw, "data", []) or []:
            mid = (getattr(m, "id", "") or "").replace("models/", "").strip()
            if mid and not any(x in mid.lower() for x in _NON_CHAT):
                ids.add(mid)
        if not ids:
            return {"models": fallback, "source": "fallback"}
        return {"models": sorted(ids), "source": "live"}
    except Exception as exc:  # noqa: BLE001 — surface as fallback, never 500
        return {"models": fallback, "source": "fallback", "error": str(exc)[:140]}


def save_env(key: str, value: str) -> None:
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), key, value)
    os.environ[key] = value
    load_dotenv(override=True)


# ── Strava OAuth ──────────────────────────────────────────────────────────────

def strava_start_flow() -> str:
    """Start a one-shot local callback server on :8080, return the authorize URL."""
    cid = os.getenv("CLIENT_ID")
    csec = os.getenv("CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("CLIENT_ID / CLIENT_SECRET not set")

    AUTH_URL = "https://www.strava.com/oauth/authorize"
    TOKEN_URL = "https://www.strava.com/oauth/token"
    REDIRECT_URI = "http://localhost:8080/callback"
    SCOPE = "read,activity:read_all,activity:write"
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "approval_prompt": "force", "scope": SCOPE, "state": state,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    import requests

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q and q.get("state", [""])[0] == state:
                try:
                    resp = requests.post(TOKEN_URL, data={
                        "client_id": cid, "client_secret": csec,
                        "code": q["code"][0], "grant_type": "authorization_code",
                    }, timeout=15)
                    TOKENS.mkdir(exist_ok=True)
                    (TOKENS / "strava.json").write_text(json.dumps(resp.json(), indent=2))
                    self.send_response(200)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'><h1 style='color:#FC4C02'>Strava connected!</h1><p>Return to Training Copilot.</p><script>setTimeout(window.close,3000)</script></body></html>")
                except Exception as exc:  # noqa: BLE001
                    self.send_error(500, str(exc))
            else:
                self.send_error(400, "Invalid callback")

        def log_message(self, *a):
            pass

    def _serve():
        try:
            srv = HTTPServer(("localhost", 8080), _Handler)
            srv.allow_reuse_address = True
            srv.timeout = 300
            srv.handle_request()
        except Exception:
            pass

    threading.Thread(target=_serve, daemon=True).start()
    return auth_url


def revoke(service: str) -> None:
    f = TOKENS / f"{service}.json"
    if f.exists():
        f.unlink()


def strava_save_token(token: dict) -> dict:
    """Verify + persist an uploaded Strava token (refreshing if expired)."""
    import requests

    token = {k.lower().replace("-", "_"): v for k, v in token.items()}
    for drop in ("client_id", "client_secret"):
        token.pop(drop, None)
    if not token.get("access_token"):
        raise RuntimeError("access_token not found")
    token.setdefault("expires_at", int(time.time()) - 1)
    TOKENS.mkdir(exist_ok=True)
    (TOKENS / "strava.json").write_text(json.dumps(token, indent=2))

    cid, csec = os.getenv("CLIENT_ID", ""), os.getenv("CLIENT_SECRET", "")
    resp = requests.get("https://www.strava.com/api/v3/athlete",
                        headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=8)
    if resp.status_code == 401 and token.get("refresh_token") and cid and csec:
        r = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": cid, "client_secret": csec,
            "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
        }, timeout=10)
        if r.status_code == 200:
            new = r.json()
            token.update({"access_token": new["access_token"],
                          "refresh_token": new.get("refresh_token", token["refresh_token"]),
                          "expires_at": new.get("expires_at", 0)})
            (TOKENS / "strava.json").write_text(json.dumps(token, indent=2))
            resp = requests.get("https://www.strava.com/api/v3/athlete",
                                headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=8)
    if resp.status_code != 200:
        (TOKENS / "strava.json").unlink(missing_ok=True)
        raise RuntimeError(f"Token verification failed (HTTP {resp.status_code})")
    ath = resp.json()
    return {"name": f"{ath.get('firstname','')} {ath.get('lastname','')}".strip()}


# ── Google OAuth ────────────────────────────────────────────────────────────────

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
# User-facing Google connect = CALENDAR ONLY (read + write events). This is the
# integration any logged-in user may (re)connect, so it must NOT request gmail.send
# — the OTP-email credential lives separately in google_mail.json (api/email_service)
# and the admin connects it via auth/google_oauth.py. calendar.events alone grants
# no reads, hence both calendar scopes.
_GOOGLE_SCOPE = ("https://www.googleapis.com/auth/calendar.readonly "
                 "https://www.googleapis.com/auth/calendar.events")

# The redirect must point at an *always-running* endpoint and be registered in the
# Google Cloud Console. We use the FastAPI server's own public callback route
# (no fragile single-shot localhost listener). Override with GOOGLE_OAUTH_REDIRECT
# (e.g. behind a different host/port or the Node BFF).
_GOOGLE_REDIRECT_DEFAULT = "http://localhost:8000/api/settings/google/callback"

# CSRF state → issued-at, validated in the callback. Module-level so it survives
# between the connect request and Google's redirect (two separate HTTP requests).
_google_states: Dict[str, float] = {}
_GOOGLE_STATE_TTL = 600  # seconds a pending auth attempt stays valid


def google_redirect_uri() -> str:
    return os.getenv("GOOGLE_OAUTH_REDIRECT", _GOOGLE_REDIRECT_DEFAULT)


def google_start_flow() -> str:
    """Build the Google consent URL and remember the CSRF state.

    No local listener — Google redirects the browser to ``google_redirect_uri()``
    (the FastAPI ``/settings/google/callback`` route), which calls
    :func:`google_handle_callback` to finish the exchange.
    """
    cid = os.getenv("GOOGLE_CLIENT_ID", "")
    csec = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not cid or not csec:
        raise RuntimeError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set")

    state = secrets.token_urlsafe(16)
    now = time.time()
    # Opportunistically drop expired states so the dict can't grow unbounded.
    for s, ts in list(_google_states.items()):
        if now - ts > _GOOGLE_STATE_TTL:
            _google_states.pop(s, None)
    _google_states[state] = now

    params = {
        "client_id": cid, "response_type": "code", "redirect_uri": google_redirect_uri(),
        "scope": _GOOGLE_SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
    }
    return f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def google_handle_callback(code: str, state: str) -> None:
    """Exchange the auth ``code`` for tokens and persist them. Raises on failure."""
    import requests

    ts = _google_states.pop(state or "", None)
    if ts is None:
        raise RuntimeError("Unknown or expired OAuth state — start the connect flow again.")
    if time.time() - ts > _GOOGLE_STATE_TTL:
        raise RuntimeError("OAuth attempt expired — start the connect flow again.")
    if not code:
        raise RuntimeError("No authorization code in callback.")

    cid = os.getenv("GOOGLE_CLIENT_ID", "")
    csec = os.getenv("GOOGLE_CLIENT_SECRET", "")
    resp = requests.post(_GOOGLE_TOKEN_URL, data={
        "client_id": cid, "client_secret": csec, "code": code,
        "grant_type": "authorization_code", "redirect_uri": google_redirect_uri(),
    }, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} {resp.text}")
    tok = resp.json()
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    TOKENS.mkdir(exist_ok=True)
    (TOKENS / "google.json").write_text(json.dumps(tok, indent=2))


# ── Garmin credential login (background thread + MFA) ──────────────────────────

_garmin: Dict[str, Any] = {"done": False, "result": None, "mfa_needed": False, "mfa_input": None}
_garmin_mfa_event = threading.Event()


def garmin_login(email: str, password: str) -> None:
    global _garmin, _garmin_mfa_event
    save_env("GARMIN_EMAIL", email)
    save_env("GARMIN_PASSWORD", password)
    _garmin = {"done": False, "result": None, "mfa_needed": False, "mfa_input": None}
    _garmin_mfa_event = threading.Event()

    def _run():
        try:
            from garminconnect import Garmin

            def _mfa_prompt():
                _garmin["mfa_needed"] = True
                _garmin_mfa_event.clear()
                _garmin_mfa_event.wait(timeout=300)
                code = _garmin.get("mfa_input")
                if not code:
                    raise TimeoutError("MFA timeout")
                _garmin["mfa_input"] = None
                return code

            g = Garmin(email=email, password=password, prompt_mfa=_mfa_prompt)
            g.login(tokenstore=str(TOKENS))
            _garmin["result"] = "success"
        except Exception as exc:  # noqa: BLE001
            _garmin["result"] = f"error:{exc}"
        finally:
            _garmin["done"] = True

    threading.Thread(target=_run, daemon=True).start()


def garmin_submit_mfa(code: str) -> None:
    _garmin["mfa_input"] = code
    _garmin_mfa_event.set()


def garmin_login_status() -> Dict[str, Any]:
    if _garmin.get("mfa_needed") and not _garmin.get("done"):
        return {"state": "mfa_needed"}
    if not _garmin.get("done"):
        return {"state": "authenticating"}
    result = _garmin.get("result", "")
    if result == "success":
        return {"state": "success"}
    err = (result or "").replace("error:", "").strip()
    if "429" in err or "rate limit" in err.lower():
        err = "Garmin rate limit (429) — wait 15–30 minutes and try again."
    return {"state": "error", "error": err}


def garmin_disconnect() -> None:
    import shutil
    excluded = {"strava.json", "google.json"}
    if TOKENS.is_dir():
        for f in TOKENS.iterdir():
            if f.name not in excluded:
                try:
                    f.unlink() if f.is_file() else shutil.rmtree(f)
                except Exception:
                    pass


# ── Telegram phone login (Telethon) ─────────────────────────────────────────────

def _run_tg(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def tg_send_code(phone: str) -> Dict[str, str]:
    api_id, api_hash = os.getenv("TELEGRAM_API_ID", ""), os.getenv("TELEGRAM_API_HASH", "")

    async def _go():
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(), int(api_id), api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
            return {"inter": StringSession.save(client.session), "code_hash": sent.phone_code_hash}
        finally:
            await client.disconnect()

    return _run_tg(_go())


def tg_sign_in(inter: str, phone: str, code: str, code_hash: str) -> Dict[str, str]:
    api_id, api_hash = os.getenv("TELEGRAM_API_ID", ""), os.getenv("TELEGRAM_API_HASH", "")

    async def _go():
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.errors import SessionPasswordNeededError
        client = TelegramClient(StringSession(inter), int(api_id), api_hash)
        await client.connect()
        try:
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
                return {"status": "ok", "session": StringSession.save(client.session)}
            except SessionPasswordNeededError:
                return {"status": "password", "inter": StringSession.save(client.session)}
        finally:
            await client.disconnect()

    return _run_tg(_go())


def tg_password(inter: str, password: str) -> Dict[str, str]:
    api_id, api_hash = os.getenv("TELEGRAM_API_ID", ""), os.getenv("TELEGRAM_API_HASH", "")

    async def _go():
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(inter), int(api_id), api_hash)
        await client.connect()
        try:
            await client.sign_in(password=password)
            return {"status": "ok", "session": StringSession.save(client.session)}
        finally:
            await client.disconnect()

    return _run_tg(_go())


# ── Telegram agent bridge ────────────────────────────────────────────────────

_bridge: Dict[str, Any] = {"proc": None}


def bridge_running() -> bool:
    p = _bridge["proc"]
    return p is not None and p.poll() is None


def bridge_start() -> None:
    if bridge_running():
        return
    _bridge["proc"] = subprocess.Popen([sys.executable, "telegram_bridge.py"], cwd=str(Path(".").resolve()))


def bridge_stop() -> None:
    p = _bridge.get("proc")
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    _bridge["proc"] = None


# ── MCP server restart ───────────────────────────────────────────────────────

def restart_mcp_servers() -> Dict[str, int]:
    from core.config import MCP_SERVERS

    optional = {"telegram"}
    killed = 0
    for name, url in MCP_SERVERS.items():
        if name in optional:
            continue
        port = urllib.parse.urlparse(url).port
        if not port:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                continue
        try:
            out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5).stdout
            for pid in out.strip().split("\n"):
                if pid.strip():
                    subprocess.run(["kill", "-9", pid.strip()], capture_output=True, timeout=5)
                    killed += 1
        except Exception:
            pass
    time.sleep(1.0)
    started = 0
    for name, url in MCP_SERVERS.items():
        if name in optional:
            continue
        port = urllib.parse.urlparse(url).port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            up = s.connect_ex(("127.0.0.1", port)) == 0
        if not up:
            subprocess.Popen([sys.executable, "-m", f"servers.{name}_mcp"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            started += 1
    time.sleep(3.0)
    return {"killed": killed, "started": started}
