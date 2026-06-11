"""
Settings tab — configure all integrations from the UI.

OAuth services (Strava, Google …) get a "Connect" button that opens the
provider's auth page in a new browser tab and polls for the callback.

Credential services (Garmin) get a secure form with optional MFA step.

API-key services (OpenAI, ORS, Weather …) get a simple text-input that
saves the key to .env and reloads the registry.

Adding support for a new provider:
  1. Add an entry to INTEGRATION_META below.
  2. Implement a _setup_<key>() render function.
  3. Register the server in servers/registry.py as usual.
  → The card appears automatically in the UI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
from dotenv import load_dotenv, set_key

from ui.styles import ACCENT, BORDER, C_GREEN, C_AMBER, TEXT_MUTED, TEXT_PRIMARY

# ── Telegram bridge process state (module-level → survives Streamlit rerenders) ──
_BRIDGE_LOG = Path(".logs/telegram_bridge.log")
_bridge: Dict[str, Any] = {"proc": None}


def _bridge_running() -> bool:
    p = _bridge["proc"]
    return p is not None and p.poll() is None


def _bridge_start() -> None:
    if _bridge_running():
        return
    # Inherit parent stdout/stderr so bridge logs appear in the terminal where
    # `streamlit run app.py` is running — no growing log file that freezes the UI.
    _bridge["proc"] = subprocess.Popen(
        [sys.executable, "telegram_bridge.py"],
        stdout=None, stderr=None,
        cwd=str(Path(".").resolve()),
    )


def _bridge_stop() -> None:
    p = _bridge.get("proc")
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    _bridge["proc"] = None


# ── Garmin auth thread state ───────────────────────────────────────────────────
# st.session_state is not writable from background threads (ScriptRunContext error).
# Use a plain module-level dict instead — the main thread polls it on rerun.
_garmin_auth: dict = {"done": False, "result": None, "mfa_needed": False, "mfa_input": None}
_garmin_mfa_event: threading.Event = threading.Event()

# ── Integration metadata (drives the card layout) ────────────────────────────
# Keys must match the server registry key OR a standalone service key.
INTEGRATION_META: Dict[str, Dict[str, Any]] = {
    "strava": {
        "label":    "Strava",
        "icon":     "🏃",
        "type":     "oauth",
        "description": "Activities, GPS streams, statistics",
        "docs_url": "https://www.strava.com/settings/api",
    },
    "garmin": {
        "label":    "Garmin Connect",
        "icon":     "⌚",
        "type":     "credentials",
        "description": "Sleep, HRV, Body Battery, steps",
        "docs_url": "https://connect.garmin.com",
    },
    "openai": {
        "label":    "OpenAI / LLM",
        "icon":     "🤖",
        "type":     "api_key",
        "env_var":  "OPENAI_API_KEY",
        "description": "LLM for chat and analysis — set model and base URL below",
        "docs_url": "https://platform.openai.com/api-keys",
        "placeholder": "sk-...",
    },
    "routes": {
        "label":    "OpenRouteService",
        "icon":     "🗺️",
        "type":     "api_key",
        "env_var":  "ORS_API_KEY",
        "description": "Route planning, trail search, isochrones",
        "docs_url": "https://openrouteservice.org/dev/#/signup",
        "placeholder": "5b3ce3...",
    },
    "weather": {
        "label":    "Open-Meteo",
        "icon":     "🌤️",
        "type":     "none",
        "description": "Weather, pollen, UV index — no API key needed",
        "docs_url": "https://open-meteo.com",
    },
    "google": {
        "label":    "Google Calendar",
        "icon":     "📅",
        "type":     "oauth",
        "description": "Appointments and training schedule",
        "docs_url": "https://console.cloud.google.com/apis/credentials",
    },
    "telegram": {
        "label":    "Telegram",
        "icon":     "✈️",
        "type":     "telegram",
        "description": "Chats, messages, contacts (via external telegram-mcp)",
        "docs_url": "https://my.telegram.org/apps",
    },
    # ── Future providers — uncomment and implement _setup_<key>() ─────────────
    # "wahoo": {
    #     "label":    "Wahoo",
    #     "icon":     "🚴",
    #     "type":     "oauth",
    #     "description": "ELEMNT-Daten und Workouts",
    #     "docs_url": "https://developer.wahooligan.com",
    # },
}

DISPLAY_ORDER = ["strava", "garmin", "google", "openai", "routes", "weather", "telegram"]

ENV_FILE = Path(".env")

# ── Main render entry point ───────────────────────────────────────────────────

def render_settings() -> None:
    st.markdown("## ⚙️ Integrations")
    st.caption(
        "Connect your services. Credentials are stored locally in `.env` "
        "and never shared with third parties."
    )

    _progress_bar()

    strava_ok = _is_connected("strava", INTEGRATION_META["strava"])
    openai_ok = _is_connected("openai", INTEGRATION_META["openai"])
    if strava_ok and openai_ok:
        all_ok = all(
            _is_connected(k, INTEGRATION_META[k])
            for k in ["strava", "garmin", "google", "openai"]
        )
        if all_ok:
            st.success("**All services connected** — Training Copilot is fully set up. 🎉")
        else:
            st.info("**Required services connected** — Training Copilot is ready. Garmin and Google Calendar are optional.")

    st.divider()

    for key in DISPLAY_ORDER:
        meta = INTEGRATION_META.get(key, {})
        if not meta:
            continue
        _render_card(key, meta)
        st.divider()

    # Catch-all: show registry servers not in DISPLAY_ORDER
    _render_unknown_servers()

    st.divider()
    st.markdown("### 🔧 Developer")
    c1, c2 = st.columns([3, 1])
    with c1:
        st.caption(
            "Restart all MCP servers to pick up code changes. "
            "Use this after updating server files or when tools show as 'Unknown'."
        )
    with c2:
        if st.button("🔄 Restart MCP Servers", key="restart_mcp", width='stretch'):
            with st.spinner("Restarting servers…"):
                _killed, _started = _restart_mcp_servers()
                st.cache_resource.clear()
            st.success(f"Done — stopped {_killed}, started {_started} servers.")
            time.sleep(1)
            st.rerun()


# ── Card renderer ─────────────────────────────────────────────────────────────

def _render_card(key: str, meta: Dict) -> None:
    icon  = meta.get("icon", "🔌")
    label = meta.get("label", key.capitalize())
    kind  = meta.get("type", "none")
    desc  = meta.get("description", "")

    col_info, col_action = st.columns([3, 2])

    with col_info:
        status_html, is_connected = _status(key, meta)
        st.markdown(f"### {icon} {label}")
        st.caption(desc)
        st.markdown(status_html, unsafe_allow_html=True)
        if meta.get("docs_url"):
            st.markdown(f"[Documentation ↗]({meta['docs_url']})")

    with col_action:
        st.markdown("<br>", unsafe_allow_html=True)
        if kind == "oauth":
            _setup_oauth(key, meta, is_connected)
        elif kind == "credentials":
            _setup_credentials(key, meta, is_connected)
        elif kind == "api_key":
            _setup_api_key(key, meta, is_connected)
        elif kind == "telegram":
            _setup_telegram(is_connected)
        else:
            st.success("Active — no setup needed")

    # Full-width Telegram Bridge panel (needs to be outside the narrow col_action)
    if key == "telegram":
        st.markdown("---")
        _render_bridge_control()


# ── Status helpers ────────────────────────────────────────────────────────────

def _status(key: str, meta: Dict) -> tuple[str, bool]:
    """Return (html_badge, is_connected)."""
    connected = _is_connected(key, meta)
    if connected:
        return '<span style="color:#22c55e;font-weight:600">✅ Connected</span>', True
    if meta.get("type") == "none":
        return '<span style="color:#22c55e;font-weight:600">✅ Active</span>', True
    return '<span style="color:#f59e0b;font-weight:600">⚠️ Not configured</span>', False


def _is_connected(key: str, meta: Dict) -> bool:
    kind = meta.get("type", "none")
    if kind == "none":
        return True
    if kind == "oauth":
        if key == "strava":
            from ui.shared import strava_connected
            return strava_connected()
        if key == "google":
            return Path(".tokens/google.json").is_file()
        return False
    if kind == "credentials":
        if key == "garmin":
            from ui.shared import garmin_connected
            return garmin_connected()
        return False
    if kind == "api_key":
        env_var = meta.get("env_var", "")
        val = os.getenv(env_var, "")
        # Placeholder values count as not connected
        if not val or val.startswith("your_") or val == "sk-...":
            return False
        return True
    if kind == "telegram":
        from ui.shared import telegram_connected
        return telegram_connected()
    return False


# ── OAuth setup (Strava, Google, …) ──────────────────────────────────────────

def _setup_oauth(key: str, meta: Dict, is_connected: bool) -> None:
    if key == "strava":
        _setup_strava(is_connected)
    elif key == "google":
        _setup_google(is_connected)
    else:
        st.info(f"OAuth for {meta['label']} not yet implemented.")


def _setup_strava(is_connected: bool) -> None:
    if is_connected:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Reconnect", key="strava_reconnect", width='stretch'):
                _strava_revoke()
                st.rerun()
        with col2:
            if st.button("🔌 Disconnect", key="strava_disconnect", width='stretch'):
                _strava_revoke()
                st.rerun()
        return

    # ── Step 1: check API app credentials ────────────────────────────────────
    # CLIENT_ID / CLIENT_SECRET identify the *Strava API app*, not the user account.
    # The user's own account is linked via the OAuth flow below.
    cid  = os.getenv("CLIENT_ID", "")
    csec = os.getenv("CLIENT_SECRET", "")

    if not cid or not csec:
        with st.expander("🔑 Enter Strava API app credentials", expanded=True):
            st.caption(
                "These identify the **Strava API application** (not your personal account). "
                "Create one at strava.com/settings/api — takes about 2 minutes."
            )
            _env_row("CLIENT_ID",     "not set")
            _env_row("CLIENT_SECRET", "not set")
            st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

            new_cid  = st.text_input("Client ID",     value=cid,  key="strava_cid")
            new_csec = st.text_input("Client Secret", value=csec, type="password", key="strava_csec")
            if st.button("Save & continue", key="strava_save_creds", width='stretch'):
                _save_env("CLIENT_ID",     new_cid)
                _save_env("CLIENT_SECRET", new_csec)
                os.environ["CLIENT_ID"]     = new_cid
                os.environ["CLIENT_SECRET"] = new_csec
                st.rerun()

            st.markdown("<div style='margin-top:4px'></div>", unsafe_allow_html=True)
            with st.expander("What to enter in the Strava form"):
                st.markdown("""
| Field | Value |
|---|---|
| **Application Name** | anything, e.g. `Training Copilot` |
| **Category** | `Health and Fitness` |
| **Club** | leave empty |
| **Website** | `http://localhost:8501` |
| **Application Description** | e.g. `Personal sports dashboard` |
| **Authorization Callback Domain** | `localhost` ← domain only, no port, no `http://` |

> **Client ID** and **Client Secret** appear on the page after saving, below the app name.
""")
            st.link_button(
                "→ Open Strava API page",
                "https://www.strava.com/settings/api",
                width='stretch',
            )
        return

    # ── Step 2: start OAuth flow ──────────────────────────────────────────────
    token_key = "strava_oauth_started"

    if not st.session_state.get(token_key):
        if st.button("🔗 Connect with Strava", key="strava_connect", width='stretch', type="primary"):
            _strava_start_flow()
            st.rerun()
    else:
        # Flow is running — show auth link and poll
        auth_url = st.session_state.get("strava_auth_url", "")
        st.link_button("🌐 Authorize on Strava (opens new tab)", auth_url,
                       width='stretch', type="primary")
        st.caption("After authorizing, return here — the connection will be detected automatically.")

        from ui.shared import strava_connected
        if strava_connected():
            st.session_state.pop("strava_oauth_started", None)
            st.session_state.pop("strava_auth_url", None)
            st.success("✅ Strava connected successfully!")
            st.cache_resource.clear()
            st.rerun()
        else:
            if st.button("🔄 Check connection", key="strava_poll", width='stretch'):
                st.rerun()

            # Fallback: browser may show "connection refused" if the local callback
            # server couldn't start (e.g. port 8080 already in use).
            # The URL still contains the auth code — user can paste it here.
            with st.expander("Browser showed an error on localhost:8080? Paste the URL here"):
                st.caption(
                    "Copy the full redirect URL from your browser's address bar "
                    "(starts with `http://localhost:8080/callback?...`) and paste it below."
                )
                _cb_url = st.text_input("Callback URL", key="strava_callback_url",
                                        placeholder="http://localhost:8080/callback?state=...&code=...")
                if st.button("Exchange code", key="strava_exchange_code", width='stretch'):
                    import urllib.parse as _up, requests as _req
                    _parsed = _up.urlparse(_cb_url)
                    _q      = _up.parse_qs(_parsed.query)
                    _code   = (_q.get("code") or [None])[0]
                    if not _code:
                        st.error("No `code` parameter found in the URL.")
                    else:
                        _cid  = os.getenv("CLIENT_ID")
                        _csec = os.getenv("CLIENT_SECRET")
                        try:
                            _r = _req.post("https://www.strava.com/oauth/token", data={
                                "client_id":     _cid,
                                "client_secret": _csec,
                                "code":          _code,
                                "grant_type":    "authorization_code",
                            }, timeout=15)
                            if _r.status_code == 200:
                                _tok = _r.json()
                                _tok.pop("client_id", None); _tok.pop("client_secret", None)
                                Path(".tokens").mkdir(exist_ok=True)
                                Path(".tokens/strava.json").write_text(json.dumps(_tok, indent=2))
                                _ath = _tok.get("athlete", {})
                                _name = f"{_ath.get('firstname','')} {_ath.get('lastname','')}".strip()
                                st.session_state.pop("strava_oauth_started", None)
                                st.cache_resource.clear()
                                st.success(f"✅ Connected as **{_name}**!")
                                st.rerun()
                            else:
                                st.error(f"❌ Code exchange failed (HTTP {_r.status_code}): {_r.text[:200]}")
                        except Exception as _ex:
                            st.error(f"❌ Error: {_ex}")

    # ── Alternative: upload existing token ────────────────────────────────────
    with st.expander("📁 Already have a token? Upload it"):
        st.caption(
            "Upload the OAuth token JSON returned by Strava's `/oauth/token` endpoint "
            "(fields: `access_token`, `refresh_token`, `expires_at`, optionally `athlete`). "
            "**`client_id` / `client_secret` are not needed here** — they are read from `.env`."
        )
        uploaded = st.file_uploader("strava.json", type="json", key="strava_token_upload",
                                    label_visibility="collapsed")
        if uploaded is not None:
            try:
                raw = json.loads(uploaded.read())
                # Normalize any capitalized keys (e.g. Access_Token → access_token)
                token_data = {k.lower().replace("-", "_"): v for k, v in raw.items()}

                # Strip app credentials — they live in .env, not in the token file
                for _drop in ("client_id", "client_secret"):
                    token_data.pop(_drop, None)

                if not token_data.get("access_token"):
                    st.error("Invalid file — `access_token` not found.")
                else:
                    import time as _t
                    if "expires_at" not in token_data:
                        token_data["expires_at"] = int(_t.time()) - 1  # treat as expired → force refresh

                    Path(".tokens").mkdir(exist_ok=True)
                    Path(".tokens/strava.json").write_text(json.dumps(token_data, indent=2))

                    import requests as _req
                    _cid  = os.getenv("CLIENT_ID", "")
                    _csec = os.getenv("CLIENT_SECRET", "")

                    # Try the access token as-is first
                    with st.spinner("Verifying token…"):
                        try:
                            _resp = _req.get(
                                "https://www.strava.com/api/v3/athlete",
                                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                                timeout=8,
                            )
                        except Exception:
                            _resp = None

                    # Access token expired or missing — refresh using env-stored app credentials
                    if (_resp is None or _resp.status_code == 401) and token_data.get("refresh_token"):
                        if not _cid or not _csec:
                            Path(".tokens/strava.json").unlink(missing_ok=True)
                            st.error(
                                "❌ Access token expired and `CLIENT_ID` / `CLIENT_SECRET` are not set in `.env`. "
                                "Add your Strava API app credentials first."
                            )
                        else:
                            with st.spinner("Access token expired — refreshing…"):
                                try:
                                    _rresp = _req.post(
                                        "https://www.strava.com/oauth/token",
                                        data={
                                            "client_id":     _cid,
                                            "client_secret": _csec,
                                            "refresh_token": token_data["refresh_token"],
                                            "grant_type":    "refresh_token",
                                        },
                                        timeout=10,
                                    )
                                    if _rresp.status_code == 200:
                                        _new = _rresp.json()
                                        token_data.update({
                                            "access_token":  _new["access_token"],
                                            "refresh_token": _new.get("refresh_token", token_data["refresh_token"]),
                                            "expires_at":    _new.get("expires_at", 0),
                                        })
                                        Path(".tokens/strava.json").write_text(json.dumps(token_data, indent=2))
                                        _resp = _req.get(
                                            "https://www.strava.com/api/v3/athlete",
                                            headers={"Authorization": f"Bearer {token_data['access_token']}"},
                                            timeout=8,
                                        )
                                    else:
                                        _resp = None
                                        st.error(
                                            f"❌ Token refresh failed (HTTP {_rresp.status_code}). "
                                            "The refresh token may be invalid — reconnect via Strava OAuth."
                                        )
                                except Exception as _re:
                                    _resp = None
                                    st.error(f"❌ Refresh error: {_re}")

                    if _resp is not None and _resp.status_code == 200:
                        _ath = _resp.json()
                        _name = f"{_ath.get('firstname', '')} {_ath.get('lastname', '')}".strip()
                        st.cache_resource.clear()
                        st.success(f"✅ Token verified — connected as **{_name}**.")
                        st.rerun()
                    elif _resp is not None and _resp.status_code not in (200, 401):
                        Path(".tokens/strava.json").unlink(missing_ok=True)
                        st.error(f"❌ Strava returned HTTP {_resp.status_code}. Check your internet connection.")
            except Exception as exc:
                st.error(f"Error reading file: {exc}")


def _strava_start_flow() -> None:
    """Start the OAuth callback server and store the auth URL in session state."""
    import secrets
    import urllib.parse
    from http.server import BaseHTTPRequestHandler, HTTPServer

    AUTH_URL     = "https://www.strava.com/oauth/authorize"
    TOKEN_URL    = "https://www.strava.com/oauth/token"
    REDIRECT_URI = "http://localhost:8080/callback"
    SCOPE        = "read,activity:read_all,activity:write"
    TOKEN_FILE   = ".tokens/strava.json"

    state = secrets.token_urlsafe(16)
    params = {
        "client_id":       os.getenv("CLIENT_ID"),
        "response_type":   "code",
        "redirect_uri":    REDIRECT_URI,
        "approval_prompt": "force",
        "scope":           SCOPE,
        "state":           state,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    st.session_state["strava_auth_url"]   = auth_url
    st.session_state["strava_oauth_started"] = True

    # Start callback server in background thread
    cid  = os.getenv("CLIENT_ID")
    csec = os.getenv("CLIENT_SECRET")

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            import json
            parsed = urllib.parse.urlparse(self.path)
            q      = urllib.parse.parse_qs(parsed.query)
            if "code" in q and q.get("state", [""])[0] == state:
                code = q["code"][0]
                try:
                    resp = __import__("requests").post(TOKEN_URL, data={
                        "client_id":     cid,
                        "client_secret": csec,
                        "code":          code,
                        "grant_type":    "authorization_code",
                    }, timeout=15)
                    tokens = resp.json()
                    Path(TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
                    Path(TOKEN_FILE).write_text(json.dumps(tokens, indent=2))
                    self.send_response(200)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("""
                        <html><head><meta charset="utf-8"></head><body
                          style="font-family:sans-serif;text-align:center;padding:60px">
                        <h1 style="color:#FC4C02">✅ Strava connected!</h1>
                        <p>You can close this window and return to Training Copilot.</p>
                        <script>setTimeout(window.close, 3000);</script>
                        </body></html>
                    """.encode())
                except Exception as exc:
                    self.send_error(500, str(exc))
            else:
                self.send_error(400, "Ungültiger Callback")

        def log_message(self, *args):
            pass

    def _serve():
        try:
            srv = HTTPServer(("localhost", 8080), _Handler)
            srv.allow_reuse_address = True  # avoids EADDRINUSE on quick restarts
            srv.timeout = 300  # 5 min
            srv.handle_request()  # serve exactly one request
        except Exception:
            pass  # port busy → user uses the paste-URL fallback

    threading.Thread(target=_serve, daemon=True).start()


def _strava_revoke() -> None:
    token_file = Path(".tokens/strava.json")
    if token_file.exists():
        token_file.unlink()
    st.cache_resource.clear()


# ── OAuth setup (Google Calendar) ────────────────────────────────────────────

def _setup_google(is_connected: bool) -> None:
    if is_connected:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Reconnect", key="google_reconnect", width='stretch'):
                _google_revoke()
                st.rerun()
        with col2:
            if st.button("🔌 Disconnect", key="google_disconnect", width='stretch'):
                _google_revoke()
                st.rerun()
        return

    cid  = os.getenv("GOOGLE_CLIENT_ID", "")
    csec = os.getenv("GOOGLE_CLIENT_SECRET", "")

    if not cid or not csec:
        with st.expander("🔑 Enter API credentials", expanded=True):
            st.caption("Create an OAuth project in the Google Cloud Console.")
            _env_row("GOOGLE_CLIENT_ID",     "not set")
            _env_row("GOOGLE_CLIENT_SECRET", "not set")
            st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
            new_cid  = st.text_input("Client ID",     value=cid,  key="google_cid")
            new_csec = st.text_input("Client Secret", value=csec, type="password", key="google_csec")
            if st.button("Save & continue", key="google_save_creds", width='stretch'):
                _save_env("GOOGLE_CLIENT_ID",     new_cid)
                _save_env("GOOGLE_CLIENT_SECRET", new_csec)
                os.environ["GOOGLE_CLIENT_ID"]     = new_cid
                os.environ["GOOGLE_CLIENT_SECRET"] = new_csec
                st.rerun()

            with st.expander("How do I get the Client ID and Secret?"):
                st.markdown("""
1. Open [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → **APIs & Services → Library** → enable *Google Calendar API*
3. **Credentials → Create OAuth 2.0 Client ID**
   - Type: **Desktop app**
   - Authorized redirect URI: `http://localhost:8888/callback`
4. Enter the Client ID and Client Secret here
""")
            st.link_button(
                "→ Open Google Cloud Console",
                "https://console.cloud.google.com/apis/credentials",
                width='stretch',
            )
        return

    # Has credentials → start OAuth flow
    if not st.session_state.get("google_oauth_started"):
        if st.button("🔗 Connect with Google", key="google_connect",
                     width='stretch', type="primary"):
            _google_start_flow(cid, csec)
            st.rerun()
        return

    auth_url = st.session_state.get("google_auth_url", "")
    st.link_button("🌐 Authorize with Google (opens new tab)", auth_url,
                   width='stretch', type="primary")
    st.caption("After authorizing, return here — the connection will be detected automatically.")

    from ui.shared import google_connected
    if google_connected():
        st.session_state.pop("google_oauth_started", None)
        st.session_state.pop("google_auth_url", None)
        st.success("✅ Google Calendar connected successfully!")
        st.cache_resource.clear()
        st.rerun()
    else:
        if st.button("🔄 Check connection", key="google_poll", width='stretch'):
            st.rerun()


def _google_start_flow(client_id: str, client_secret: str) -> None:
    """Start Google OAuth callback server on port 8888 and store auth URL in session state."""
    import secrets
    import urllib.parse
    from http.server import BaseHTTPRequestHandler, HTTPServer

    GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    REDIRECT_URI     = "http://localhost:8888/callback"
    SCOPE            = "https://www.googleapis.com/auth/calendar.readonly"
    TOKEN_FILE       = ".tokens/google.json"

    state = secrets.token_urlsafe(16)
    params = {
        "client_id":     client_id,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPE,
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         state,
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    st.session_state["google_auth_url"]      = auth_url
    st.session_state["google_oauth_started"] = True

    def _serve():
        try:
            class _Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    import json, time as _time
                    parsed = urllib.parse.urlparse(self.path)
                    q      = urllib.parse.parse_qs(parsed.query)
                    if "code" in q and q.get("state", [""])[0] == state:
                        code = q["code"][0]
                        try:
                            resp = __import__("requests").post(GOOGLE_TOKEN_URL, data={
                                "client_id":     client_id,
                                "client_secret": client_secret,
                                "code":          code,
                                "grant_type":    "authorization_code",
                                "redirect_uri":  REDIRECT_URI,
                            }, timeout=15)
                            tokens = resp.json()
                            tokens["expires_at"] = _time.time() + int(tokens.get("expires_in", 3600))
                            Path(TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
                            Path(TOKEN_FILE).write_text(json.dumps(tokens, indent=2))
                            self.send_response(200)
                            self.send_header("Content-type", "text/html; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(
                                b"<html><head><meta charset='utf-8'></head>"
                                b"<body style='font-family:sans-serif;text-align:center;padding:60px'>"
                                b"<h1 style='color:#4285F4'>&#10003; Google Calendar connected!</h1>"
                                b"<p>You can close this window and return to Training Copilot.</p>"
                                b"<script>setTimeout(window.close, 3000);</script>"
                                b"</body></html>"
                            )
                        except Exception as exc:
                            self.send_error(500, str(exc))
                    else:
                        self.send_error(400, "Ungültiger Callback")

                def log_message(self, *args): pass

            srv = HTTPServer(("localhost", 8888), _Handler)
            srv.timeout = 300
            srv.handle_request()
        except Exception:
            pass

    threading.Thread(target=_serve, daemon=True).start()


def _google_revoke() -> None:
    token_file = Path(".tokens/google.json")
    if token_file.exists():
        token_file.unlink()
    st.cache_resource.clear()


# ── Credentials setup (Garmin) ────────────────────────────────────────────────

def _setup_credentials(key: str, meta: Dict, is_connected: bool) -> None:
    if key == "garmin":
        _setup_garmin(is_connected)


def _setup_garmin(is_connected: bool) -> None:
    # ── Mock mode toggle ──────────────────────────────────────────────────────
    from dotenv import dotenv_values as _dv
    # Read from file first (external .env edits win over stale os.environ).
    _file_val = _dv(".env").get("GARMIN_MOCK_HEALTH", "false")
    _env_val  = os.getenv("GARMIN_MOCK_HEALTH", _file_val)
    mock_on   = str(_env_val).lower() in ("1", "true", "yes")
    # Sync the toggle widget to the saved env value if it changed externally
    # (e.g. .env edited by hand).  Only overwrite session state when the saved
    # value differs from what was last seen — preserves in-flight user clicks.
    if st.session_state.get("_garmin_mock_saved") != mock_on:
        st.session_state["garmin_mock_toggle"] = mock_on
        st.session_state["_garmin_mock_saved"] = mock_on
    new_mock = st.toggle(
        "🔄 Mock mode (demo data — no real device needed)",
        value=mock_on, key="garmin_mock_toggle",
        help="Generates realistic Garmin health & activity data without credentials.",
    )
    if new_mock != mock_on:
        _save_env("GARMIN_MOCK_HEALTH", "true" if new_mock else "false")
        os.environ["GARMIN_MOCK_HEALTH"] = "true" if new_mock else "false"
        st.session_state["_garmin_mock_saved"] = new_mock
        st.cache_data.clear()
        st.success("Saved — Garmin data source switched. Data will reload automatically.")
        st.rerun()
    if new_mock:
        st.info("Mock mode active — demo data is generated. No Garmin account needed.")
        return

    if is_connected:
        if st.button("🔌 Disconnect Garmin", key="garmin_disconnect", width='stretch'):
            import shutil
            _excluded = {"strava.json", "google.json"}
            token_dir = Path(".tokens")
            for f in token_dir.iterdir():
                if f.name not in _excluded:
                    try:
                        f.unlink() if f.is_file() else shutil.rmtree(f)
                    except Exception:
                        pass
            st.cache_resource.clear()
            st.rerun()
        return

    flow = st.session_state.get("garmin_flow", "idle")

    if flow == "idle":
        email    = os.getenv("GARMIN_EMAIL", "")
        password = os.getenv("GARMIN_PASSWORD", "")
        with st.form("garmin_login"):
            st.caption("Garmin Connect E-Mail und Passwort")
            new_email = st.text_input("E-Mail", value=email, key="g_email")
            new_pw    = st.text_input("Passwort", type="password", key="g_pw")
            submitted = st.form_submit_button("Verbinden", width='stretch', type="primary")
        if submitted:
            _save_env("GARMIN_EMAIL",    new_email)
            _save_env("GARMIN_PASSWORD", new_pw)
            os.environ["GARMIN_EMAIL"]    = new_email
            os.environ["GARMIN_PASSWORD"] = new_pw
            st.session_state["garmin_flow"]  = "authenticating"
            st.session_state["garmin_email"] = new_email
            st.session_state["garmin_pw"]    = new_pw
            st.session_state["garmin_error"] = None
            _garmin_start_auth(new_email, new_pw)
            st.rerun()

    elif flow == "authenticating":
        st.info("🔄 Verbinde mit Garmin Connect…")
        if st.button("✕ Abbrechen", key="garmin_cancel", width='stretch'):
            st.session_state["garmin_flow"] = "idle"
            st.rerun()
        time.sleep(1)
        _garmin_check_result()

    elif flow == "mfa_needed":
        st.warning("🔐 Zwei-Faktor-Authentifizierung erforderlich")
        with st.form("garmin_mfa_form"):
            mfa_code = st.text_input("MFA / OTP Code", placeholder="123456", key="g_mfa_code")
            submitted = st.form_submit_button("Bestätigen", width='stretch', type="primary")
        if submitted:
            st.session_state["garmin_flow"] = "mfa_submitted"
            _garmin_submit_mfa(mfa_code)
            st.rerun()

    elif flow == "mfa_submitted":
        st.info("🔄 MFA wird verifiziert…")
        time.sleep(1)
        _garmin_check_result()

    elif flow == "error":
        err = st.session_state.get("garmin_error", "Unbekannter Fehler")
        st.error(f"❌ Error: {err}")
        if st.button("Nochmal versuchen", key="garmin_retry", width='stretch'):
            st.session_state["garmin_flow"] = "idle"
            st.rerun()

    elif flow == "success":
        st.success("✅ Garmin connected successfully!")
        st.cache_resource.clear()
        st.session_state["garmin_flow"] = "idle"
        st.rerun()

    # ── Alternative: upload existing token ────────────────────────────────────
    if flow in ("idle", "error"):
        with st.expander("📁 Already have a token? Upload it"):
            st.caption(
                "Upload a `garmin_tokens.json` shared by a teammate. "
                "No email or password needed — the token is sufficient for all API calls."
            )
            uploaded = st.file_uploader("garmin_tokens.json", type="json", key="garmin_token_upload",
                                        label_visibility="collapsed")
            if uploaded is not None:
                try:
                    data = json.loads(uploaded.read())
                    required = {"di_token", "di_refresh_token", "di_client_id"}
                    missing = required - set(data.keys())
                    if missing:
                        st.error(f"Invalid token file — missing fields: {', '.join(missing)}")
                    else:
                        Path(".tokens").mkdir(exist_ok=True)
                        Path(".tokens/garmin_tokens.json").write_text(json.dumps(data, indent=2))
                        # Verify: log in and fetch the account name
                        with st.spinner("Verifying token…"):
                            try:
                                from garminconnect import Garmin as _G
                                _g = _G()
                                _g.login(tokenstore=".tokens")
                                _name = _g.get_full_name()
                                st.cache_resource.clear()
                                st.success(f"✅ Garmin connected as **{_name}**.")
                                st.rerun()
                            except Exception as _ve:
                                Path(".tokens/garmin_tokens.json").unlink(missing_ok=True)
                                st.error(
                                    f"❌ Token verification failed: {_ve}\n\n"
                                    "The token may be expired or belong to a different region. "
                                    "Ask your teammate to generate a fresh one via "
                                    "`python auth/garmin_setup.py`."
                                )
                except Exception as exc:
                    st.error(f"Error reading file: {exc}")


def _garmin_start_auth(email: str, password: str) -> None:
    """Start Garmin auth in a background thread.

    Communicates via the module-level _garmin_auth dict, NOT st.session_state,
    because Streamlit raises ScriptRunContext errors on session_state writes
    from background threads.
    """
    global _garmin_auth, _garmin_mfa_event
    _garmin_auth      = {"done": False, "result": None, "mfa_needed": False, "mfa_input": None}
    _garmin_mfa_event = threading.Event()

    def _run():
        try:
            from garminconnect import Garmin

            def _mfa_prompt():
                _garmin_auth["mfa_needed"] = True
                _garmin_mfa_event.clear()
                _garmin_mfa_event.wait(timeout=300)
                code = _garmin_auth.get("mfa_input")
                if not code:
                    raise TimeoutError("MFA timeout — no code entered within 5 minutes")
                _garmin_auth["mfa_input"] = None
                return code

            garmin = Garmin(email=email, password=password, prompt_mfa=_mfa_prompt)
            garmin.login(tokenstore=".tokens")
            _garmin_auth["result"] = "success"
        except Exception as exc:
            _garmin_auth["result"] = f"error:{exc}"
        finally:
            _garmin_auth["done"] = True

    threading.Thread(target=_run, daemon=True).start()


def _garmin_submit_mfa(code: str) -> None:
    _garmin_auth["mfa_input"] = code
    _garmin_mfa_event.set()


def _garmin_check_result() -> None:
    # MFA prompt arrived from thread — switch UI to MFA input screen
    if _garmin_auth.get("mfa_needed"):
        _garmin_auth["mfa_needed"] = False
        st.session_state["garmin_flow"] = "mfa_needed"
        st.rerun()
        return

    if not _garmin_auth.get("done"):
        time.sleep(1)
        st.rerun()
        return

    result = _garmin_auth.get("result", "")
    if result == "success":
        st.session_state["garmin_flow"] = "success"
    else:
        err = result.replace("error:", "").strip()
        if "429" in err or "rate limit" in err.lower():
            err = (
                "Garmin rate limit (429) — your IP was temporarily blocked. "
                "Wait 15–30 minutes and try again."
            )
        st.session_state["garmin_flow"]  = "error"
        st.session_state["garmin_error"] = err
    st.rerun()


# ── API key setup ─────────────────────────────────────────────────────────────

def _setup_api_key(key: str, meta: Dict, is_connected: bool) -> None:
    env_var     = meta.get("env_var", "")
    placeholder = meta.get("placeholder", "")
    load_dotenv(override=True)  # always read fresh from disk
    current     = os.getenv(env_var, "")
    # Don't pre-fill placeholder values — show empty so user knows to enter a real key
    if current.startswith("your_"):
        current = ""

    label = "🔄 Update API key" if is_connected else "🔑 Enter API key"

    with st.form(f"apikey_{key}"):
        new_val = st.text_input(
            env_var,
            value=current,
            type="password",
            placeholder=placeholder,
            label_visibility="visible",
        )
        # For the LLM card also expose model + base URL
        new_model = new_base = None
        if key == "openai":
            load_dotenv(override=True)
            _KIT_MODELS = [
                # GPT-5 Familie
                "kit.gpt-5.1",
                "kit.gpt-5",
                "kit.gpt-5-mini",
                "kit.gpt-5-nano",
                # GPT-4.1 Familie
                "kit.gpt-4.1",
                "kit.gpt-4.1-mini",
                "kit.gpt-4.1-nano",
                # Open-Weight Modelle
                "meta-llama-3.1-8b-instruct",
                "qwen3.5-397b-a17b",
                "qwen3.5-122b-a10b",
                "qwen3.5-35b-a3b",
                "qwen3.5-27b",
                "qwen3-30b-a3b-instruct-2507",
                # Sonstige
                "kit.minimax-m2.7-229b",
                "minimax-m2.5-229b",
                "openai-gpt-oss-120b",
                "teuken-7b-instruct-research",
                "glm-4.7",
            ]
            cur_model = os.getenv("AGENT_MODEL", "kit.gpt-5.1")
            cur_base  = os.getenv("OPENAI_BASE_URL", "https://ai-gateway.dsi-experimente.de")
            # Aktuell gesetztes Modell immer anzeigen, auch wenn nicht in der Liste
            model_list = list(_KIT_MODELS)
            if cur_model and cur_model not in model_list:
                model_list.insert(0, cur_model)
            idx = model_list.index(cur_model) if cur_model in model_list else 0
            new_model = st.selectbox("AGENT_MODEL", model_list, index=idx)
            new_base  = st.text_input("OPENAI_BASE_URL", value=cur_base)

        saved = st.form_submit_button(label, width='stretch', type="primary")

    if saved:
        if new_val.strip():
            _save_env(env_var, new_val.strip())
            os.environ[env_var] = new_val.strip()
        if new_model:
            _save_env("AGENT_MODEL", new_model)
            os.environ["AGENT_MODEL"] = new_model
        if new_base and new_base.strip():
            _save_env("OPENAI_BASE_URL", new_base.strip())
            os.environ["OPENAI_BASE_URL"] = new_base.strip()
        if new_val.strip() or new_model:
            st.cache_resource.clear()
            st.success(f"✅ Saved!")
            time.sleep(0.8)
            st.rerun()
        else:
            st.error("Please enter a valid API key.")


# ── Telegram setup (external telegram-mcp proxy) ──────────────────────────────

def _setup_telegram(is_connected: bool) -> None:
    load_dotenv(override=True)
    api_id   = os.getenv("TELEGRAM_API_ID", "")
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    if api_id.startswith("your_"):   api_id = ""
    if api_hash.startswith("your_"): api_hash = ""

    # ── Connected: offer regenerate / disconnect ──────────────────────────────
    if is_connected:
        st.caption("Tools laufen über den Telegram-Server (`python -m servers.telegram_mcp`, :8106).")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Regenerate session", key="tg_regen", width='stretch'):
                _tg_reset_session()
                st.rerun()
        with col2:
            if st.button("🔌 Disconnect", key="tg_disconnect", width='stretch'):
                _tg_reset_session()
                st.rerun()
        return

    # ── Step 1: API ID + hash ─────────────────────────────────────────────────
    with st.expander("🔑 API ID & Hash", expanded=not (api_id and api_hash)):
        st.caption("Create an app at my.telegram.org/apps")
        new_id   = st.text_input("API ID",   value=api_id,   key="tg_api_id")
        new_hash = st.text_input("API Hash", value=api_hash, type="password", key="tg_api_hash")
        if st.button("Save & continue", key="tg_save_creds", width='stretch'):
            _save_env("TELEGRAM_API_ID", new_id.strip())
            _save_env("TELEGRAM_API_HASH", new_hash.strip())
            os.environ["TELEGRAM_API_ID"]   = new_id.strip()
            os.environ["TELEGRAM_API_HASH"] = new_hash.strip()
            st.rerun()

    if not (api_id and api_hash):
        st.info("Enter your API ID and Hash first.")
        return

    # ── Step 2: generate a session string ─────────────────────────────────────
    try:
        import telethon  # noqa: F401
        have_telethon = True
    except Exception:
        have_telethon = False

    if have_telethon:
        _telegram_session_flow(api_id, api_hash)
    else:
        st.warning("`telethon` not installed — in-app login unavailable. "
                   "Install it (`pip install telethon`) or paste the session string manually below.")

    with st.expander("✍️ Paste session string manually", expanded=not have_telethon):
        st.caption("Alternative — generate via CLI:  "
                   "`uv run --directory external/telegram-mcp session_string_generator.py`")
        manual = st.text_input("TELEGRAM_SESSION_STRING", type="password", key="tg_manual")
        if st.button("Save session string", key="tg_manual_save", width='stretch'):
            if manual.strip():
                _tg_save_session(manual.strip())
                st.success("✅ Session saved!")
                st.rerun()
            else:
                st.error("Please enter a session string.")

    st.caption("ℹ️ After saving, (re)start the Telegram server: `python -m servers.telegram_mcp`")


def _telegram_session_flow(api_id: str, api_hash: str) -> None:
    """Phone-number login → code → (optional) 2FA password → session string."""
    flow = st.session_state.get("tg_flow", "idle")
    st.markdown("**Sign in with phone number**")

    if flow == "idle":
        phone = st.text_input("Phone number (with country code, e.g. +49…)", key="tg_phone_in")
        if st.button("📲 Send code", key="tg_send_code", width='stretch', type="primary"):
            if not phone.strip():
                st.error("Please enter a phone number.")
                return
            try:
                with st.spinner("Sending code…"):
                    inter, code_hash = _run_tg(_tg_send_code(api_id, api_hash, phone.strip()))
                st.session_state.update(
                    tg_flow="code_sent", tg_inter=inter,
                    tg_phone=phone.strip(), tg_code_hash=code_hash,
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to send code: {exc}")

    elif flow == "code_sent":
        st.caption(f"Code sent to {st.session_state.get('tg_phone')} (check your Telegram app).")
        code = st.text_input("Confirmation code", key="tg_code_in")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Sign in", key="tg_signin", width='stretch', type="primary"):
                try:
                    with st.spinner("Signing in…"):
                        status, result = _run_tg(_tg_sign_in(
                            api_id, api_hash, st.session_state["tg_inter"],
                            st.session_state["tg_phone"], code.strip(),
                            st.session_state["tg_code_hash"],
                        ))
                    if status == "ok":
                        _tg_save_session(result)
                        _tg_clear_flow()
                        st.success("✅ Telegram connected!")
                        st.rerun()
                    else:  # 2FA password required
                        st.session_state.update(tg_flow="password", tg_inter=result)
                        st.rerun()
                except Exception as exc:
                    st.error(f"Sign-in failed: {exc}")
        with col2:
            if st.button("↩︎ Cancel", key="tg_cancel", width='stretch'):
                _tg_clear_flow()
                st.rerun()

    elif flow == "password":
        st.warning("🔐 Two-factor authentication required")
        pw = st.text_input("2FA password", type="password", key="tg_pw_in")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔓 Confirm", key="tg_pw_btn", width='stretch', type="primary"):
                try:
                    with st.spinner("Checking password…"):
                        result = _run_tg(_tg_password(
                            api_id, api_hash, st.session_state["tg_inter"], pw,
                        ))
                    _tg_save_session(result)
                    _tg_clear_flow()
                    st.success("✅ Telegram connected!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Wrong password or error: {exc}")
        with col2:
            if st.button("↩︎ Cancel", key="tg_pw_cancel", width='stretch'):
                _tg_clear_flow()
                st.rerun()


# ── Telegram session helpers ──────────────────────────────────────────────────

def _tg_save_session(session_string: str) -> None:
    _save_env("TELEGRAM_SESSION_STRING", session_string)
    os.environ["TELEGRAM_SESSION_STRING"] = session_string
    st.cache_resource.clear()


def _tg_reset_session() -> None:
    _save_env("TELEGRAM_SESSION_STRING", "")
    os.environ["TELEGRAM_SESSION_STRING"] = ""
    _tg_clear_flow()
    st.cache_resource.clear()


def _tg_clear_flow() -> None:
    for k in ("tg_flow", "tg_inter", "tg_phone", "tg_code_hash"):
        st.session_state.pop(k, None)


def _run_tg(coro):
    """Run a Telethon coroutine on a fresh event loop bound to the current thread."""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            asyncio.set_event_loop(None)
        finally:
            loop.close()


async def _tg_send_code(api_id: str, api_hash: str, phone: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        return StringSession.save(client.session), sent.phone_code_hash
    finally:
        await client.disconnect()


async def _tg_sign_in(api_id: str, api_hash: str, inter: str, phone: str, code: str, code_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import SessionPasswordNeededError
    client = TelegramClient(StringSession(inter), int(api_id), api_hash)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
            return "ok", StringSession.save(client.session)
        except SessionPasswordNeededError:
            return "password", StringSession.save(client.session)
    finally:
        await client.disconnect()


async def _tg_password(api_id: str, api_hash: str, inter: str, password: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(inter), int(api_id), api_hash)
    await client.connect()
    try:
        await client.sign_in(password=password)
        return StringSession.save(client.session)
    finally:
        await client.disconnect()


# ── Telegram bridge control ───────────────────────────────────────────────────

def _render_bridge_control() -> None:
    """Full-width Telegram Bridge start/stop panel (rendered below the card columns)."""
    load_dotenv(override=True)

    api_id   = os.getenv("TELEGRAM_API_ID", "")
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    session  = (os.getenv("TELEGRAM_BRIDGE_SESSION_STRING") or
                os.getenv("TELEGRAM_SESSION_STRING") or "")
    has_creds = bool(api_id and api_hash and session)

    running = _bridge_running()

    st.markdown("#### 🤖 Telegram Bridge")
    st.caption(
        "Expose the agent over Telegram. "
        "Bridge logs appear in the **terminal** where `streamlit run app.py` is running."
    )

    if not has_creds:
        st.warning(
            "Telegram credentials missing. "
            "Configure **API ID, API Hash** and **Session String** above first."
        )
        return

    # ── Internal / External mode toggle ──────────────────────────────────────
    internal_val = os.getenv("TELEGRAM_BRIDGE_INTERNAL_ONLY", "false").lower() in ("1", "true", "yes")
    new_internal = st.toggle(
        "🔒 Internal only (respond only to your own Saved Messages)",
        value=internal_val,
        key="bridge_internal_toggle",
        help=(
            "ON: only react when you write to yourself (Saved Messages) — safe for dev/testing.\n"
            "OFF: also respond to incoming DMs from anyone (or TELEGRAM_ALLOWED_USERS if set)."
        ),
        disabled=running,
    )
    if new_internal != internal_val and not running:
        _save_env("TELEGRAM_BRIDGE_INTERNAL_ONLY", "true" if new_internal else "false")
        os.environ["TELEGRAM_BRIDGE_INTERNAL_ONLY"] = "true" if new_internal else "false"

    col_dot, col_label, col_btn = st.columns([1, 6, 3])

    with col_dot:
        color = "#22c55e" if running else "#ef4444"
        st.markdown(
            f'<div style="padding-top:8px">'
            f'<span style="width:12px;height:12px;border-radius:50%;background:{color};'
            f'display:inline-block"></span></div>',
            unsafe_allow_html=True,
        )
    with col_label:
        if running:
            pid = _bridge["proc"].pid if _bridge.get("proc") else "?"
            mode = "internal (Saved Messages)" if internal_val else "public (incoming DMs)"
            st.markdown(f"**Bridge running** (PID {pid}) — mode: {mode}")
        else:
            st.markdown("**Bridge stopped**")

    with col_btn:
        if running:
            if st.button("⏹ Stop bridge", key="bridge_stop", width='stretch'):
                _bridge_stop()
                st.rerun()
        else:
            if st.button("▶ Start bridge", key="bridge_start",
                         width='stretch', type="primary"):
                _bridge_start()
                time.sleep(0.5)
                st.rerun()

    # Shared-session warning
    using_shared = (
        not os.getenv("TELEGRAM_BRIDGE_SESSION_STRING", "").strip()
        and bool(os.getenv("TELEGRAM_SESSION_STRING", "").strip())
    )
    if running and using_shared:
        st.warning(
            "⚠️ Bridge and Telegram MCP proxy share the same session "
            "(`TELEGRAM_SESSION_STRING`). Running both simultaneously may cause "
            "Telegram to revoke the key. For stable parallel operation, generate a "
            "dedicated login: `python telegram_bridge.py --login` → `TELEGRAM_BRIDGE_SESSION_STRING`."
        )


# ── MCP server restart ────────────────────────────────────────────────────────

def _restart_mcp_servers() -> tuple[int, int]:
    """Kill all MCP server processes by port, then restart them. Returns (killed, started)."""
    import socket
    import urllib.parse
    from core.config import MCP_SERVERS

    _optional = {"telegram"}
    killed = 0

    for name, url in MCP_SERVERS.items():
        if name in _optional:
            continue
        port = urllib.parse.urlparse(url).port
        if not port:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            _s.settimeout(0.3)
            if _s.connect_ex(("127.0.0.1", port)) != 0:
                continue  # nothing running on this port
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["netstat", "-ano"], capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        pid = int(parts[-1])
                        subprocess.run(
                            ["taskkill", "/PID", str(pid), "/F"],
                            capture_output=True, timeout=5,
                        )
                        killed += 1
                        break
            else:
                result = subprocess.run(
                    ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5
                )
                for pid_str in result.stdout.strip().split("\n"):
                    if pid_str.strip():
                        subprocess.run(
                            ["kill", "-9", pid_str.strip()], capture_output=True, timeout=5
                        )
                        killed += 1
        except Exception:
            pass

    time.sleep(1.0)

    started = 0
    for name, url in MCP_SERVERS.items():
        if name in _optional:
            continue
        port = urllib.parse.urlparse(url).port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            _s.settimeout(0.3)
            already_up = _s.connect_ex(("127.0.0.1", port)) == 0
        if not already_up:
            subprocess.Popen(
                [sys.executable, "-m", f"servers.{name}_mcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            started += 1

    time.sleep(3.0)  # give servers time to bind
    return killed, started


# ── Unknown registry servers ──────────────────────────────────────────────────

def _render_unknown_servers() -> None:
    """Placeholder — server registry removed in favour of core.config.MCP_SERVERS."""
    pass


# ── Progress / status UI helpers ─────────────────────────────────────────────

def _progress_bar() -> None:
    """Horizontal connection-status indicator for all tracked integrations."""
    steps = [
        ("Strava",   _is_connected("strava",  INTEGRATION_META["strava"])),
        ("Garmin",   _is_connected("garmin",  INTEGRATION_META["garmin"])),
        ("Google",   _is_connected("google",  INTEGRATION_META["google"])),
        ("OpenAI",   _is_connected("openai",  INTEGRATION_META["openai"])),
    ]
    n          = len(steps)
    done_count = sum(1 for _, ok in steps if ok)

    nodes: list[str] = []
    for i, (label, ok) in enumerate(steps):
        color  = C_GREEN if ok else (ACCENT if i == done_count else BORDER)
        bg     = color   if ok else "transparent"
        icon   = "✓"     if ok else str(i + 1)
        text_c = TEXT_PRIMARY if (ok or i == done_count) else TEXT_MUTED
        fw     = "600"   if (ok or i == done_count) else "400"
        nodes.append(f"""
        <div style="display:flex;flex-direction:column;align-items:center;gap:6px;flex:1">
          <div style="width:32px;height:32px;border-radius:50%;background:{bg};
                      border:2px solid {color};display:flex;align-items:center;
                      justify-content:center;font-size:13px;font-weight:700;
                      color:{'#fff' if ok else color}">{icon}</div>
          <span style="font-size:11px;color:{text_c};font-weight:{fw};
                       text-align:center;white-space:nowrap">{label}</span>
        </div>""")
        if i < n - 1:
            line_color = C_GREEN if ok else BORDER
            nodes.append(
                f'<div style="flex:2;height:2px;background:{line_color};'
                f'margin-top:15px;border-radius:2px"></div>'
            )

    st.markdown(
        '<div style="display:flex;align-items:flex-start;padding:8px 0 4px 0">'
        + "".join(nodes)
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{done_count} of {n} services connected")


def _env_row(key: str, hint: str) -> None:
    """Single-line ✓/✗ status row for a .env variable."""
    val   = os.getenv(key, "")
    ok    = bool(val) and not val.lower().startswith("your") and not val.endswith("_here")
    color = C_GREEN if ok else "#EF4444"
    icon  = "✓" if ok else "✗"
    preview = (
        f'<code style="color:{TEXT_MUTED};font-size:11px">{val[:6]}…</code>'
        if ok else
        f'<span style="color:{TEXT_MUTED};font-size:11px">{hint}</span>'
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;'
        f'border-bottom:1px solid {BORDER}">'
        f'<span style="color:{color};font-weight:700;font-size:13px;width:16px">{icon}</span>'
        f'<code style="color:{TEXT_PRIMARY};font-size:13px;margin-left:4px">{key}</code>'
        f'<span style="margin-left:auto">{preview}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── .env persistence ──────────────────────────────────────────────────────────

def _save_env(key: str, value: str) -> None:
    """Write a key=value pair to .env using python-dotenv."""
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), key, value)
    load_dotenv(override=True)
