#!/usr/bin/env python3
"""
FitDash — AI-powered sports analytics dashboard.

Entry point:
    streamlit run app.py
"""

import os
import socket
import urllib.parse as _urlparse

import streamlit as st

from ui.shared import garmin_connected, garmin_mock_mode, strava_connected, validate_config
from ui.styles import STRAVA_ORANGE, inject_css

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Training Copilot",
    page_icon="🏋️",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()

# ── Auto-start MCP servers (once per process) ─────────────────────────────────
@st.cache_resource(show_spinner="⚙️ Starting services…")
def _ensure_mcp_servers() -> list:
    """Kill any stale MCP servers, then launch fresh ones. Cleans up on exit."""
    import atexit
    import signal
    import subprocess
    import sys
    import time
    import urllib.parse
    from core.config import MCP_SERVERS

    _optional = {"telegram"}  # requires manual setup; skip silently

    def _kill_port(port: int) -> None:
        """Terminate whatever process is listening on `port`.

        Uses `lsof` rather than psutil.net_connections(): on macOS the latter
        inspects every process and raises AccessDenied for any we don't own,
        aborting the whole scan. `lsof -ti` only reports PIDs we can see and
        needs no elevated privileges for our own listeners.
        """
        try:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
        except (FileNotFoundError, OSError):
            return  # lsof unavailable; nothing we can do safely
        for pid_str in out.split():
            try:
                os.kill(int(pid_str), signal.SIGTERM)
            except (ValueError, ProcessLookupError, PermissionError):
                pass

    # Kill any existing MCP servers so we always start fresh
    for name, url in MCP_SERVERS.items():
        if name in _optional:
            continue
        port = urllib.parse.urlparse(url).port
        if port:
            _kill_port(port)

    time.sleep(0.5)  # let terminated processes release their ports

    # Start fresh subprocesses
    procs: list[subprocess.Popen] = []
    for name, url in MCP_SERVERS.items():
        if name in _optional:
            continue
        proc = subprocess.Popen(
            [sys.executable, "-m", f"servers.{name}_mcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(proc)

    time.sleep(2.5)  # give servers time to bind their ports

    # Kill children when Streamlit exits
    def _cleanup() -> None:
        for p in procs:
            try:
                p.terminate()
            except OSError:
                pass

    atexit.register(_cleanup)
    return [p.pid for p in procs]

_ensure_mcp_servers()

# ── Config validation (once at startup) ──────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _check_config() -> list:
    return validate_config()

for _warn in _check_config():
    st.sidebar.warning(_warn, icon="⚠️")

# ── PIN gate ──────────────────────────────────────────────────────────────────

def _pin_gate() -> None:
    """Block the app until the correct PIN is entered.

    Requires DO_LOCK=true in .env AND APP_PIN set (in .env or .streamlit/secrets.toml).
    When DO_LOCK is false (the default) the gate is always bypassed.
    """
    if os.getenv("DO_LOCK", "false").lower() not in ("1", "true"):
        return  # locking disabled — open access

    try:
        expected = st.secrets.get("APP_PIN") or os.getenv("APP_PIN", "")
    except Exception:
        expected = os.getenv("APP_PIN", "")
    if not expected:
        return  # lock enabled but no PIN set — still open access

    if st.session_state.get("authenticated"):
        return

    # ── Login screen ──────────────────────────────────────────────────────────
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {display: none}
        </style>
        """,
        unsafe_allow_html=True,
    )

    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown("## 🏋️ Training Copilot")
        st.caption("Enter your PIN to continue")
        pin = st.text_input(
            "PIN",
            type="password",
            placeholder="••••",
            label_visibility="collapsed",
            key="pin_input",
        )
        if st.button("Unlock", type="primary", width='stretch'):
            if pin == expected:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect PIN")

    st.stop()


_pin_gate()


def _check_server(url: str) -> bool:
    """Single TCP probe — no cache, called in parallel threads."""
    port = _urlparse.urlparse(url).port
    if not port:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        _s.settimeout(0.3)
        return _s.connect_ex(("127.0.0.1", port)) == 0


@st.fragment(run_every=5)
def _status_dots() -> None:
    """Auto-refreshes every 5 s — checks all server ports in parallel."""
    from concurrent.futures import ThreadPoolExecutor
    from core.config import MCP_SERVERS
    from ui.shared import strava_connected, garmin_connected, routes_connected, telegram_connected

    _labels = {
        "strava": "Strava", "garmin": "Garmin", "routes": "Routes",
        "weather": "Open-Meteo", "calendar": "Calendar",
        "telegram": "Telegram", "flythrough": "Flythrough",
    }

    def _svc_ok(key: str) -> bool:
        if key in ("weather", "calendar", "flythrough"): return True
        if key == "strava":   return strava_connected()
        if key == "garmin":   return garmin_connected()
        if key == "routes":   return routes_connected()
        if key == "telegram": return telegram_connected()
        return False

    # All port checks run in parallel — total latency ≤ 0.3 s
    with ThreadPoolExecutor(max_workers=len(MCP_SERVERS)) as _ex:
        _srv_up = dict(zip(MCP_SERVERS.keys(),
                           _ex.map(_check_server, MCP_SERVERS.values())))

    _DOT   = 'width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0;'
    _GREEN, _RED = "#22c55e", "#ef4444"
    _is_mock = garmin_mock_mode()

    html = (
        '<div style="font-size:0.7rem;color:#666;margin-bottom:6px;display:flex;'
        'gap:10px;align-items:center">'
        '<span>🔑 Service</span>'
        '<span>🖥️ Server</span>'
        '</div>'
    )
    for _key in MCP_SERVERS:
        _label   = _labels.get(_key, _key.capitalize())
        _svc_col = _GREEN if _svc_ok(_key) else _RED
        _srv_col = _GREEN if _srv_up.get(_key) else _RED
        _mock    = (
            ' <span style="font-size:0.7rem;color:#f59e0b;font-weight:600">(Mock)</span>'
            if _key == "garmin" and _is_mock else ""
        )
        html += (
            f'<div style="display:flex;align-items:center;gap:5px;margin:3px 0">'
            f'<span title="Service connected">🔑</span>'
            f'<span style="{_DOT}background:{_svc_col}"></span>'
            f'<span title="MCP server running" style="margin-left:4px">🖥️</span>'
            f'<span style="{_DOT}background:{_srv_col}"></span>'
            f'<span style="font-size:0.85rem;color:#ccc;margin-left:2px">{_label}{_mock}</span>'
            f'</div>'
        )
    st.markdown(html, unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("# 🏋️ Training Copilot")
    st.caption("AI-powered sports analytics")
    st.divider()

    _status_dots()   # auto-refreshes every 5 s via st.fragment

    st.divider()

    # Sport filter (applies to Dashboard tab)
    st.markdown("### Filter")
    sport_filter = st.selectbox(
        "Sport type",
        ["All", "Run", "Ride", "Hike", "Walk", "Swim", "Workout", "WeightTraining",
         "EBikeRide", "VirtualRide", "NordicSki", "AlpineSki"],
        label_visibility="collapsed",
    )

    st.divider()

    # Refresh — clears Streamlit's in-memory cache AND the Strava disk cache
    # so the next render fetches fresh data from the Strava API.
    if st.button("🔄  Refresh data", width='stretch'):
        from pathlib import Path
        Path(".cache/strava_activities.json").unlink(missing_ok=True)
        st.session_state["_refresh_v"] = st.session_state.get("_refresh_v", 0) + 1
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Training Copilot  ·  AISS2 Team 6")

    if st.session_state.get("authenticated"):
        if st.button("🔒  Lock", width='stretch'):
            st.session_state.authenticated = False
            st.rerun()

# ── Tab layout ────────────────────────────────────────────────────────────────
tab_dash, tab_health, tab_routes, tab_analyse, tab_chat, tab_sync, tab_settings = st.tabs(
    ["📊  Dashboard", "🏥  Health", "🗺️  Routes", "📈  Analysis", "💬  Chat", "🔁  Sync", "⚙️  Settings"]
)

with tab_dash:
    from ui.dashboard import render_dashboard
    render_dashboard(sport_filter=sport_filter if sport_filter != "All" else None)

with tab_health:
    from ui.health import render_health
    render_health()

with tab_routes:
    from ui.routes_explorer import render_routes_explorer
    render_routes_explorer()

with tab_analyse:
    from ui.analytics import render_analytics
    render_analytics()

with tab_chat:
    from ui.chat import render_chat
    render_chat()

with tab_sync:
    from ui.sync import render_sync
    render_sync()

with tab_settings:
    from ui.settings import render_settings
    render_settings()
