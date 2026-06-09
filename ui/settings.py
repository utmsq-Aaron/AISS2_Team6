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

import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
from dotenv import load_dotenv, set_key

# ── Integration metadata (drives the card layout) ────────────────────────────
# Keys must match the server registry key OR a standalone service key.
INTEGRATION_META: Dict[str, Dict[str, Any]] = {
    "strava": {
        "label":    "Strava",
        "icon":     "🏃",
        "type":     "oauth",
        "description": "Aktivitäten, GPS-Streams, Statistiken",
        "docs_url": "https://www.strava.com/settings/api",
    },
    "garmin": {
        "label":    "Garmin Connect",
        "icon":     "⌚",
        "type":     "credentials",
        "description": "Schlaf, HRV, Body Battery, Schritte",
        "docs_url": "https://connect.garmin.com",
    },
    "openai": {
        "label":    "OpenAI",
        "icon":     "🤖",
        "type":     "api_key",
        "env_var":  "OPENAI_API_KEY",
        "description": "GPT-4o — KI-Chat und Analyse",
        "docs_url": "https://platform.openai.com/api-keys",
        "placeholder": "sk-...",
    },
    "routes": {
        "label":    "OpenRouteService",
        "icon":     "🗺️",
        "type":     "api_key",
        "env_var":  "ORS_API_KEY",
        "description": "Routenplanung, Trail-Suche, Isochronen",
        "docs_url": "https://openrouteservice.org/dev/#/signup",
        "placeholder": "5b3ce3...",
    },
    "weather": {
        "label":    "Open-Meteo",
        "icon":     "🌤️",
        "type":     "none",
        "description": "Wetter, Pollen, UV-Index — kein API-Key nötig",
        "docs_url": "https://open-meteo.com",
    },
    "telegram": {
        "label":    "Telegram",
        "icon":     "✈️",
        "type":     "telegram",
        "description": "Chats, Nachrichten, Kontakte (über externen telegram-mcp)",
        "docs_url": "https://my.telegram.org/apps",
    },
    # ── Future providers — uncomment and implement _setup_<key>() ─────────────
    # "google": {
    #     "label":    "Google Calendar",
    #     "icon":     "📅",
    #     "type":     "oauth",
    #     "description": "Termine und Trainingsplanung",
    #     "docs_url": "https://console.cloud.google.com",
    # },
    # "wahoo": {
    #     "label":    "Wahoo",
    #     "icon":     "🚴",
    #     "type":     "oauth",
    #     "description": "ELEMNT-Daten und Workouts",
    #     "docs_url": "https://developer.wahooligan.com",
    # },
}

DISPLAY_ORDER = ["strava", "garmin", "openai", "routes", "weather", "telegram"]

ENV_FILE = Path(".env")

# ── Main render entry point ───────────────────────────────────────────────────

def render_settings() -> None:
    st.markdown("## ⚙️ Integrationen")
    st.caption(
        "Verbinde deine Dienste. Credentials werden lokal in `.env` gespeichert "
        "und nie an Dritte übertragen."
    )
    st.divider()

    for key in DISPLAY_ORDER:
        meta = INTEGRATION_META.get(key, {})
        if not meta:
            continue
        _render_card(key, meta)
        st.divider()

    # Catch-all: show registry servers not in DISPLAY_ORDER
    _render_unknown_servers()


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
            st.markdown(f"[Dokumentation ↗]({meta['docs_url']})")

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
            st.success("Aktiv — kein Setup nötig")


# ── Status helpers ────────────────────────────────────────────────────────────

def _status(key: str, meta: Dict) -> tuple[str, bool]:
    """Return (html_badge, is_connected)."""
    connected = _is_connected(key, meta)
    if connected:
        return '<span style="color:#22c55e;font-weight:600">✅ Verbunden</span>', True
    if meta.get("type") == "none":
        return '<span style="color:#22c55e;font-weight:600">✅ Aktiv</span>', True
    return '<span style="color:#f59e0b;font-weight:600">⚠️ Nicht konfiguriert</span>', False


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
    else:
        st.info(f"OAuth für {meta['label']} noch nicht implementiert.")


def _setup_strava(is_connected: bool) -> None:
    if is_connected:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Neu verbinden", key="strava_reconnect", use_container_width=True):
                _strava_revoke()
                st.rerun()
        with col2:
            if st.button("🔌 Trennen", key="strava_disconnect", use_container_width=True):
                _strava_revoke()
                st.rerun()
        return

    # ── Step 1: check credentials ─────────────────────────────────────────────
    cid  = os.getenv("CLIENT_ID", "")
    csec = os.getenv("CLIENT_SECRET", "")

    if not cid or not csec:
        with st.expander("🔑 API-Credentials eingeben", expanded=True):
            st.caption("Erstelle eine Strava-App unter strava.com/settings/api")
            new_cid  = st.text_input("Client ID",     value=cid,  key="strava_cid")
            new_csec = st.text_input("Client Secret", value=csec, type="password", key="strava_csec")
            if st.button("Speichern & weiter", key="strava_save_creds", use_container_width=True):
                _save_env("CLIENT_ID",     new_cid)
                _save_env("CLIENT_SECRET", new_csec)
                os.environ["CLIENT_ID"]     = new_cid
                os.environ["CLIENT_SECRET"] = new_csec
                st.rerun()
        return

    # ── Step 2: start OAuth flow ──────────────────────────────────────────────
    state_key  = "strava_oauth_state"
    token_key  = "strava_oauth_started"

    if not st.session_state.get(token_key):
        if st.button("🔗 Mit Strava verbinden", key="strava_connect", use_container_width=True, type="primary"):
            _strava_start_flow()
            st.rerun()
        return

    # Flow is running — show auth link and poll
    auth_url = st.session_state.get("strava_auth_url", "")
    st.link_button("🌐 Strava autorisieren (öffnet neues Tab)", auth_url,
                   use_container_width=True, type="primary")
    st.caption("Nach der Autorisierung kehre hierher zurück — die Verbindung wird automatisch erkannt.")

    # Poll only when user is actively on this page — show a manual refresh button
    # instead of an automatic rerun loop that fires even on other tabs.
    from ui.shared import strava_connected
    if strava_connected():
        st.session_state.pop("strava_oauth_started", None)
        st.session_state.pop("strava_auth_url", None)
        st.success("✅ Strava erfolgreich verbunden!")
        st.cache_resource.clear()
        time.sleep(1)
        st.rerun()
    else:
        if st.button("🔄 Verbindung prüfen", key="strava_poll", use_container_width=True):
            st.rerun()


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
                        <h1 style="color:#FC4C02">✅ Strava verbunden!</h1>
                        <p>Du kannst dieses Fenster schließen und zu FitDash zurückkehren.</p>
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
            srv.timeout = 300  # 5 min
            srv.handle_request()  # serve exactly one request
        except Exception:
            pass

    threading.Thread(target=_serve, daemon=True).start()


def _strava_revoke() -> None:
    token_file = Path(".tokens/strava.json")
    if token_file.exists():
        token_file.unlink()
    st.cache_resource.clear()


# ── Credentials setup (Garmin) ────────────────────────────────────────────────

def _setup_credentials(key: str, meta: Dict, is_connected: bool) -> None:
    if key == "garmin":
        _setup_garmin(is_connected)


def _setup_garmin(is_connected: bool) -> None:
    if is_connected:
        if st.button("🔌 Garmin trennen", key="garmin_disconnect", use_container_width=True):
            import shutil
            token_dir = Path(".tokens")
            for f in token_dir.iterdir():
                if f.name != "strava.json":
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
            submitted = st.form_submit_button("Verbinden", use_container_width=True, type="primary")
        if submitted:
            _save_env("GARMIN_EMAIL",    new_email)
            _save_env("GARMIN_PASSWORD", new_pw)
            os.environ["GARMIN_EMAIL"]    = new_email
            os.environ["GARMIN_PASSWORD"] = new_pw
            st.session_state["garmin_flow"]  = "authenticating"
            st.session_state["garmin_email"] = new_email
            st.session_state["garmin_pw"]    = new_pw
            st.session_state["garmin_mfa"]   = None
            st.session_state["garmin_error"] = None
            _garmin_start_auth(new_email, new_pw)
            st.rerun()

    elif flow == "authenticating":
        st.info("🔄 Verbinde mit Garmin Connect…")
        time.sleep(1)
        _garmin_check_result()

    elif flow == "mfa_needed":
        st.warning("🔐 Zwei-Faktor-Authentifizierung erforderlich")
        with st.form("garmin_mfa"):
            mfa_code = st.text_input("MFA / OTP Code", placeholder="123456", key="g_mfa_code")
            submitted = st.form_submit_button("Bestätigen", use_container_width=True, type="primary")
        if submitted:
            st.session_state["garmin_mfa_input"] = mfa_code
            st.session_state["garmin_flow"] = "mfa_submitted"
            _garmin_submit_mfa(mfa_code)
            st.rerun()

    elif flow == "mfa_submitted":
        st.info("🔄 MFA wird verifiziert…")
        time.sleep(1)
        _garmin_check_result()

    elif flow == "error":
        err = st.session_state.get("garmin_error", "Unbekannter Fehler")
        st.error(f"❌ Fehler: {err}")
        if st.button("Nochmal versuchen", key="garmin_retry", use_container_width=True):
            st.session_state["garmin_flow"] = "idle"
            st.rerun()

    elif flow == "success":
        st.success("✅ Garmin erfolgreich verbunden!")
        st.cache_resource.clear()
        st.session_state["garmin_flow"] = "idle"
        time.sleep(1)
        st.rerun()


def _garmin_start_auth(email: str, password: str) -> None:
    """Start Garmin auth in a background thread; uses session_state for communication."""
    st.session_state["_garmin_thread_done"]   = False
    st.session_state["_garmin_thread_result"] = None
    mfa_event = threading.Event()
    st.session_state["_garmin_mfa_event"] = mfa_event

    def _run():
        try:
            from garminconnect import Garmin, GarminConnectAuthenticationError

            def _mfa_prompt():
                st.session_state["garmin_flow"] = "mfa_needed"
                # Signal the UI to show MFA input; then block this thread until
                # the user submits the code (or 5-minute hard timeout).
                mfa_event.clear()
                mfa_event.wait(timeout=300)
                code = st.session_state.get("garmin_mfa_input")
                if not code:
                    raise TimeoutError("MFA timeout — no code entered within 5 minutes")
                st.session_state["garmin_mfa_input"] = None
                return code

            garmin = Garmin(email=email, password=password, prompt_mfa=_mfa_prompt)
            garmin.login(tokenstore=".tokens")
            st.session_state["_garmin_thread_result"] = "success"
        except Exception as exc:
            st.session_state["_garmin_thread_result"] = f"error:{exc}"
        finally:
            st.session_state["_garmin_thread_done"] = True

    threading.Thread(target=_run, daemon=True).start()


def _garmin_submit_mfa(code: str) -> None:
    st.session_state["garmin_mfa_input"] = code
    # Unblock the waiting background thread immediately
    event = st.session_state.get("_garmin_mfa_event")
    if event is not None:
        event.set()


def _garmin_check_result() -> None:
    if not st.session_state.get("_garmin_thread_done"):
        # Still running — check if MFA is needed
        flow = st.session_state.get("garmin_flow")
        if flow not in ("mfa_needed", "mfa_submitted"):
            time.sleep(1)
            st.rerun()
        return

    result = st.session_state.get("_garmin_thread_result", "")
    if result == "success":
        st.session_state["garmin_flow"] = "success"
    else:
        st.session_state["garmin_flow"]  = "error"
        st.session_state["garmin_error"] = result.replace("error:", "")
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

    label = "🔄 API-Key aktualisieren" if is_connected else "🔑 API-Key eingeben"

    with st.form(f"apikey_{key}"):
        new_val = st.text_input(
            env_var,
            value=current,
            type="password",
            placeholder=placeholder,
            label_visibility="visible",
        )
        saved = st.form_submit_button(label, use_container_width=True, type="primary")

    if saved:
        if new_val.strip():
            _save_env(env_var, new_val.strip())
            os.environ[env_var] = new_val.strip()
            st.cache_resource.clear()
            st.success(f"✅ {env_var} gespeichert!")
            time.sleep(0.8)
            st.rerun()
        else:
            st.error("Bitte einen gültigen Key eingeben.")


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
            if st.button("🔄 Session neu erzeugen", key="tg_regen", use_container_width=True):
                _tg_reset_session()
                st.rerun()
        with col2:
            if st.button("🔌 Trennen", key="tg_disconnect", use_container_width=True):
                _tg_reset_session()
                st.rerun()
        return

    # ── Step 1: API ID + hash ─────────────────────────────────────────────────
    with st.expander("🔑 API ID & Hash", expanded=not (api_id and api_hash)):
        st.caption("Erstelle eine App unter my.telegram.org/apps")
        new_id   = st.text_input("API ID",   value=api_id,   key="tg_api_id")
        new_hash = st.text_input("API Hash", value=api_hash, type="password", key="tg_api_hash")
        if st.button("Speichern & weiter", key="tg_save_creds", use_container_width=True):
            _save_env("TELEGRAM_API_ID", new_id.strip())
            _save_env("TELEGRAM_API_HASH", new_hash.strip())
            os.environ["TELEGRAM_API_ID"]   = new_id.strip()
            os.environ["TELEGRAM_API_HASH"] = new_hash.strip()
            st.rerun()

    if not (api_id and api_hash):
        st.info("Zuerst API ID und Hash eingeben.")
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
        st.warning("`telethon` nicht installiert — In-App-Login nicht verfügbar. "
                   "Installiere es (`pip install telethon`) oder füge den String unten manuell ein.")

    with st.expander("✍️ Session-String manuell einfügen", expanded=not have_telethon):
        st.caption("Alternativ per CLI erzeugen:  "
                   "`uv run --directory external/telegram-mcp session_string_generator.py`")
        manual = st.text_input("TELEGRAM_SESSION_STRING", type="password", key="tg_manual")
        if st.button("String speichern", key="tg_manual_save", use_container_width=True):
            if manual.strip():
                _tg_save_session(manual.strip())
                st.success("✅ Session gespeichert!")
                time.sleep(0.8)
                st.rerun()
            else:
                st.error("Bitte einen Session-String eingeben.")

    st.caption("ℹ️ Nach dem Speichern den Telegram-Server (neu) starten: `python -m servers.telegram_mcp`")


def _telegram_session_flow(api_id: str, api_hash: str) -> None:
    """Phone-number login → code → (optional) 2FA password → session string."""
    flow = st.session_state.get("tg_flow", "idle")
    st.markdown("**Per Telefonnummer anmelden**")

    if flow == "idle":
        phone = st.text_input("Telefonnummer (mit Vorwahl, z. B. +49…)", key="tg_phone_in")
        if st.button("📲 Code anfordern", key="tg_send_code", use_container_width=True, type="primary"):
            if not phone.strip():
                st.error("Bitte eine Telefonnummer eingeben.")
                return
            try:
                with st.spinner("Sende Code…"):
                    inter, code_hash = _run_tg(_tg_send_code(api_id, api_hash, phone.strip()))
                st.session_state.update(
                    tg_flow="code_sent", tg_inter=inter,
                    tg_phone=phone.strip(), tg_code_hash=code_hash,
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Fehler beim Senden des Codes: {exc}")

    elif flow == "code_sent":
        st.caption(f"Code an {st.session_state.get('tg_phone')} gesendet (siehe Telegram-App).")
        code = st.text_input("Bestätigungscode", key="tg_code_in")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Anmelden", key="tg_signin", use_container_width=True, type="primary"):
                try:
                    with st.spinner("Melde an…"):
                        status, result = _run_tg(_tg_sign_in(
                            api_id, api_hash, st.session_state["tg_inter"],
                            st.session_state["tg_phone"], code.strip(),
                            st.session_state["tg_code_hash"],
                        ))
                    if status == "ok":
                        _tg_save_session(result)
                        _tg_clear_flow()
                        st.success("✅ Telegram verbunden!")
                        time.sleep(0.8)
                        st.rerun()
                    else:  # 2FA password required
                        st.session_state.update(tg_flow="password", tg_inter=result)
                        st.rerun()
                except Exception as exc:
                    st.error(f"Anmeldung fehlgeschlagen: {exc}")
        with col2:
            if st.button("↩︎ Abbrechen", key="tg_cancel", use_container_width=True):
                _tg_clear_flow()
                st.rerun()

    elif flow == "password":
        st.warning("🔐 Zwei-Faktor-Authentifizierung aktiv — Passwort erforderlich")
        pw = st.text_input("2FA-Passwort", type="password", key="tg_pw_in")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔓 Bestätigen", key="tg_pw_btn", use_container_width=True, type="primary"):
                try:
                    with st.spinner("Prüfe Passwort…"):
                        result = _run_tg(_tg_password(
                            api_id, api_hash, st.session_state["tg_inter"], pw,
                        ))
                    _tg_save_session(result)
                    _tg_clear_flow()
                    st.success("✅ Telegram verbunden!")
                    time.sleep(0.8)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Passwort falsch oder Fehler: {exc}")
        with col2:
            if st.button("↩︎ Abbrechen", key="tg_pw_cancel", use_container_width=True):
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


# ── Unknown registry servers ──────────────────────────────────────────────────

def _render_unknown_servers() -> None:
    """Placeholder — server registry removed in favour of core.config.MCP_SERVERS."""
    pass


# ── .env persistence ──────────────────────────────────────────────────────────

def _save_env(key: str, value: str) -> None:
    """Write a key=value pair to .env using python-dotenv."""
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), key, value)
    load_dotenv(override=True)
