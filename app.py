#!/usr/bin/env python3
"""
FitDash — AI-powered sports analytics dashboard.

Entry point:
    streamlit run app.py
"""

import os

import streamlit as st

from ui.shared import garmin_connected, strava_connected, validate_config
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
    """Launch any MCP server that isn't already listening on its port."""
    import socket
    import subprocess
    import sys
    import time
    import urllib.parse
    from core.config import MCP_SERVERS

    _optional = {"telegram"}  # requires manual setup; skip silently
    started = []
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
            started.append(name)
    if started:
        time.sleep(2.5)  # give freshly-launched servers time to bind their ports
    return started

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

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("# 🏋️ Training Copilot")
    st.caption("AI-powered sports analytics")
    st.divider()

    # Connection status — colored dots from core.config.MCP_SERVERS
    from core.config import MCP_SERVERS
    from ui.shared import strava_connected, garmin_connected, routes_connected, telegram_connected

    _labels = {
        "strava": "Strava", "garmin": "Garmin", "routes": "Routes",
        "weather": "Open-Meteo", "calendar": "Calendar",
        "telegram": "Telegram", "flythrough": "Flythrough",
    }

    def _is_connected(key: str) -> bool:
        if key in ("weather", "calendar", "flythrough"): return True
        if key == "strava":   return strava_connected()
        if key == "garmin":   return garmin_connected()
        if key == "routes":   return routes_connected()
        if key == "telegram": return telegram_connected()
        return False

    dots_html = ""
    for _key in MCP_SERVERS:
        _label = _labels.get(_key, _key.capitalize())
        _color = "#22c55e" if _is_connected(_key) else "#ef4444"
        dots_html += (
            f'<div style="display:flex;align-items:center;gap:8px;margin:4px 0">'
            f'<span style="width:10px;height:10px;border-radius:50%;'
            f'background:{_color};display:inline-block;flex-shrink:0"></span>'
            f'<span style="font-size:0.85rem;color:#ccc">{_label}</span>'
            f'</div>'
        )
    st.markdown(dots_html, unsafe_allow_html=True)
    st.caption("⚙️ Settings")

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

    # Refresh
    if st.button("🔄  Refresh data", width='stretch'):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Training Copilot  ·  AISS2 Team 6")

    if st.session_state.get("authenticated"):
        if st.button("🔒  Lock", width='stretch'):
            st.session_state.authenticated = False
            st.rerun()

# ── Tab layout ────────────────────────────────────────────────────────────────
tab_dash, tab_health, tab_routes, tab_chat, tab_sync, tab_settings = st.tabs(
    ["📊  Dashboard", "🏥  Health", "🗺️  Routes", "💬  Chat", "🔁  Sync", "⚙️  Settings"]
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

with tab_chat:
    from ui.chat import render_chat
    render_chat()

with tab_sync:
    from ui.sync import render_sync
    render_sync()

with tab_settings:
    from ui.settings import render_settings
    render_settings()
