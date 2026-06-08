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
    page_title="FitDash",
    page_icon="🏃",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()

# ── Config validation (once at startup) ──────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _check_config() -> list:
    return validate_config()

for _warn in _check_config():
    st.sidebar.warning(_warn, icon="⚠️")

# ── PIN gate ──────────────────────────────────────────────────────────────────

def _pin_gate() -> None:
    """Block the app until the correct PIN is entered.

    Set APP_PIN in .streamlit/secrets.toml (or as env var APP_PIN).
    If APP_PIN is not configured the gate is bypassed (local dev convenience).
    """
    try:
        expected = st.secrets.get("APP_PIN") or os.getenv("APP_PIN", "")
    except Exception:
        expected = os.getenv("APP_PIN", "")
    if not expected:
        return  # no PIN configured — open access

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
        st.markdown("## 🏃 FitDash")
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
    st.markdown(f"# 🏃 FitDash")
    st.caption("AI-powered sports analytics")
    st.divider()

    # Connection status — colored dots only
    from servers.registry import config_status
    from ui.shared import strava_connected, garmin_connected, routes_connected

    _labels = {"strava": "Strava", "garmin": "Garmin", "routes": "Routes", "weather": "Open-Meteo"}

    def _is_truly_connected(key: str) -> bool:
        if key == "weather": return True
        if key == "strava":  return strava_connected()
        if key == "garmin":  return garmin_connected()
        if key == "routes":  return routes_connected()
        return False

    dots_html = ""
    for _srv in config_status():
        _key   = _srv["key"]
        _label = _labels.get(_key, _key.capitalize())
        _color = "#22c55e" if _is_truly_connected(_key) else "#ef4444"
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
    st.caption("AISS2 Team 6  ·  v2.0")

    if st.session_state.get("authenticated"):
        if st.button("🔒  Lock", width='stretch'):
            st.session_state.authenticated = False
            st.rerun()

# ── Tab layout ────────────────────────────────────────────────────────────────
tab_dash, tab_health, tab_routes, tab_chat, tab_sync, tab_settings = st.tabs(
    ["📊  Dashboard", "🏥  Health", "🗺️  Routen", "💬  Chat", "🔁  Sync", "⚙️  Settings"]
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
