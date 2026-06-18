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

def google_start_flow() -> str:
    cid = os.getenv("GOOGLE_CLIENT_ID", "")
    csec = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not cid or not csec:
        raise RuntimeError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set")
    AUTH = "https://accounts.google.com/o/oauth2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    REDIRECT = "http://localhost:8888/callback"
    SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
    state = secrets.token_urlsafe(16)
    params = {"client_id": cid, "response_type": "code", "redirect_uri": REDIRECT,
              "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state}
    auth_url = f"{AUTH}?{urllib.parse.urlencode(params)}"

    import requests

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q and q.get("state", [""])[0] == state:
                try:
                    resp = requests.post(TOKEN, data={
                        "client_id": cid, "client_secret": csec, "code": q["code"][0],
                        "grant_type": "authorization_code", "redirect_uri": REDIRECT,
                    }, timeout=15)
                    tok = resp.json()
                    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
                    TOKENS.mkdir(exist_ok=True)
                    (TOKENS / "google.json").write_text(json.dumps(tok, indent=2))
                    self.send_response(200)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'><h1 style='color:#4285F4'>Google Calendar connected!</h1><script>setTimeout(window.close,3000)</script></body></html>")
                except Exception as exc:  # noqa: BLE001
                    self.send_error(500, str(exc))
            else:
                self.send_error(400, "Invalid callback")

        def log_message(self, *a):
            pass

    def _serve():
        try:
            srv = HTTPServer(("localhost", 8888), _Handler)
            srv.timeout = 300
            srv.handle_request()
        except Exception:
            pass

    threading.Thread(target=_serve, daemon=True).start()
    return auth_url


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
