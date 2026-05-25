#!/usr/bin/env python3
"""
FitDash — AI-powered sports analytics dashboard.

Entry point:
    streamlit run app.py
"""

import streamlit as st

from ui.shared import garmin_connected, strava_connected
from ui.styles import STRAVA_ORANGE, inject_css

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="FitDash",
    page_icon="🏃",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"# 🏃 FitDash")
    st.caption("AI-powered sports analytics")
    st.divider()

    # Connection status
    st.markdown("### Connections")
    _strava_ok = strava_connected()
    _garmin_ok = garmin_connected()

    if _strava_ok:
        st.markdown('<span class="badge-ok">✅ Strava connected</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="badge-err">❌ Strava — not authorized</span>', unsafe_allow_html=True)
        st.caption("Strava auth happens automatically on first data load.")

    if _garmin_ok:
        st.markdown('<span class="badge-ok">✅ Garmin connected</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="badge-warn">⚠️ Garmin — not configured</span>', unsafe_allow_html=True)
        st.caption("`python auth/garmin_setup.py`")

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

# ── Tab layout ────────────────────────────────────────────────────────────────
tab_dash, tab_health, tab_routes, tab_chat, tab_sync = st.tabs(
    ["📊  Dashboard", "🏥  Health", "🗺️  Routen", "💬  Chat", "🔁  Sync"]
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
