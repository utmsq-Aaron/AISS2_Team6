"""Activity stream analysis — colored route overlay and per-km metric charts."""

import json
from typing import Dict, List, Optional, Tuple

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from ui.shared import get_strava_mcp, run_async
from ui.styles import (
    ACCENT, C_AMBER, C_CYAN, C_GREEN, C_INDIGO, C_ROSE,
    DARK_MAP_ATTR, DARK_MAP_TILES, TEXT_MUTED, chart_style,
)


# ── Constants ────────────────────────────────────────────────────────────────
_MAX_ROUTE_SEGMENTS     = 200   # downsample cap for Folium PolyLine performance
_MAX_PACE_OUTLIER_MIN_KM = 20   # pace values above this are GPS noise, not real effort

# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def _load_streams(activity_id: int) -> Dict:
    mcp = get_strava_mcp()
    raw = run_async(mcp._dispatch("get_activity_streams", {"activity_id": activity_id}))
    return json.loads(raw)


# ── Colour helpers ────────────────────────────────────────────────────────────

def _gradient_color(t: float) -> str:
    """Green (0.0) → Yellow (0.5) → Red (1.0)."""
    t = max(0.0, min(1.0, t))
    if t <= 0.5:
        s = t * 2
        r, g, b = int(34 + s * (252 - 34)), int(197 + s * (211 - 197)), int(94 + s * (77 - 94))
    else:
        s = (t - 0.5) * 2
        r, g, b = int(252 + s * (239 - 252)), int(211 + s * (68 - 211)), int(77 + s * (68 - 77))
    return f"#{r:02x}{g:02x}{b:02x}"


def _norm(val: float, lo: float, hi: float, invert: bool = False) -> float:
    if hi == lo:
        return 0.5
    t = (val - lo) / (hi - lo)
    return 1.0 - t if invert else t


# ── Map builders ──────────────────────────────────────────────────────────────

def _colored_route_map(
    points: List[Dict],
    metric: str,
    invert: bool = False,
) -> Optional[folium.Map]:
    """Folium map with each route segment colored by a metric (green=low/good → red=high/bad)."""
    valid = [p for p in points
             if p.get("lat") is not None and p.get("lon") is not None
             and p.get(metric) is not None]
    if len(valid) < 2:
        return None

    # Downsample for performance — keep last point to preserve route end
    if len(valid) > _MAX_ROUTE_SEGMENTS + 1:
        step = len(valid) / _MAX_ROUTE_SEGMENTS
        valid = [valid[int(i * step)] for i in range(_MAX_ROUTE_SEGMENTS)] + [valid[-1]]

    values = [float(p[metric]) for p in valid]
    lo, hi = min(values), max(values)
    center = [
        sum(p["lat"] for p in valid) / len(valid),
        sum(p["lon"] for p in valid) / len(valid),
    ]
    m = folium.Map(location=center, zoom_start=14,
                   tiles=DARK_MAP_TILES, attr=DARK_MAP_ATTR, prefer_canvas=True)

    for i in range(len(valid) - 1):
        color = _gradient_color(_norm(values[i], lo, hi, invert=invert))
        folium.PolyLine(
            [[valid[i]["lat"], valid[i]["lon"]], [valid[i+1]["lat"], valid[i+1]["lon"]]],
            color=color, weight=5, opacity=0.92,
        ).add_to(m)

    folium.CircleMarker(
        [valid[0]["lat"], valid[0]["lon"]], radius=7,
        color="#2ECC71", fill=True, fill_color="#2ECC71", fill_opacity=1, tooltip="Start",
    ).add_to(m)
    folium.CircleMarker(
        [valid[-1]["lat"], valid[-1]["lon"]], radius=7,
        color="#E74C3C", fill=True, fill_color="#E74C3C", fill_opacity=1, tooltip="Finish",
    ).add_to(m)
    return m


def _plain_route_map(points: List[Dict]) -> Optional[folium.Map]:
    valid = [p for p in points if p.get("lat") is not None and p.get("lon") is not None]
    if len(valid) < 2:
        return None
    center = [
        sum(p["lat"] for p in valid) / len(valid),
        sum(p["lon"] for p in valid) / len(valid),
    ]
    m = folium.Map(location=center, zoom_start=14,
                   tiles=DARK_MAP_TILES, attr=DARK_MAP_ATTR, prefer_canvas=True)
    coords = [[p["lat"], p["lon"]] for p in valid]
    folium.PolyLine(coords, color=ACCENT, weight=4, opacity=0.9).add_to(m)
    folium.CircleMarker(coords[0],  radius=7, color="#2ECC71", fill=True,
                        fill_color="#2ECC71", fill_opacity=1, tooltip="Start").add_to(m)
    folium.CircleMarker(coords[-1], radius=7, color="#E74C3C", fill=True,
                        fill_color="#E74C3C", fill_opacity=1, tooltip="Finish").add_to(m)
    return m


# ── Colour-scale legend ───────────────────────────────────────────────────────

def _legend_html(high_label: str, low_label: str) -> str:
    return (
        f'<div style="display:flex;flex-direction:column;align-items:center;'
        f'gap:6px;padding-top:46px">'
        f'<span style="font-size:10px;color:{TEXT_MUTED};text-align:center">{high_label}</span>'
        f'<div style="width:16px;height:110px;border-radius:4px;'
        f'background:linear-gradient(to bottom,#EF4444,#FCDA4D,#22C55E)"></div>'
        f'<span style="font-size:10px;color:{TEXT_MUTED};text-align:center">{low_label}</span>'
        f'</div>'
    )


# ── Stream charts ─────────────────────────────────────────────────────────────

def _stream_charts(df: pd.DataFrame) -> None:
    charts: List[Tuple[str, go.Figure]] = []

    if "hr" in df.columns and df["hr"].notna().any():
        fig = go.Figure(go.Scatter(
            x=df["dist_km"], y=df["hr"], mode="lines",
            line=dict(color=C_ROSE, width=1.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(251,113,133,0.12)",
            hovertemplate="<b>%{x:.2f} km</b><br>HR: %{y:.0f} bpm<extra></extra>",
        ))
        avg = df["hr"].dropna().mean()
        fig.add_hline(y=avg, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
                      annotation_text=f"avg {avg:.0f}", annotation_font_color=TEXT_MUTED,
                      annotation_position="top right")
        fig.update_layout(yaxis=dict(ticksuffix=" bpm"))
        charts.append(("Heart Rate", chart_style(fig)))

    if "velocity" in df.columns and df["velocity"].notna().any():
        dv = df[df["velocity"] > 0.5].copy()
        dv["pace"] = 1000 / (dv["velocity"] * 60)
        dv = dv[dv["pace"] < _MAX_PACE_OUTLIER_MIN_KM]
        fig = go.Figure(go.Scatter(
            x=dv["dist_km"], y=dv["pace"], mode="lines",
            line=dict(color=C_CYAN, width=1.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(34,211,238,0.10)",
            hovertemplate="<b>%{x:.2f} km</b><br>Pace: %{y:.2f} min/km<extra></extra>",
        ))
        fig.update_layout(yaxis=dict(ticksuffix=" /km", autorange="reversed"))
        charts.append(("Pace", chart_style(fig)))

    if "ele" in df.columns and df["ele"].notna().any():
        fig = go.Figure(go.Scatter(
            x=df["dist_km"], y=df["ele"], mode="lines",
            line=dict(color=C_AMBER, width=1.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(252,211,77,0.10)",
            hovertemplate="<b>%{x:.2f} km</b><br>Elevation: %{y:.0f} m<extra></extra>",
        ))
        fig.update_layout(yaxis=dict(ticksuffix=" m"))
        charts.append(("Elevation", chart_style(fig)))

    if "cadence" in df.columns and df["cadence"].notna().any():
        fig = go.Figure(go.Scatter(
            x=df["dist_km"], y=df["cadence"], mode="lines",
            line=dict(color=C_GREEN, width=1.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(34,197,94,0.10)",
            hovertemplate="<b>%{x:.2f} km</b><br>Cadence: %{y:.0f} spm<extra></extra>",
        ))
        avg = df["cadence"].dropna().mean()
        fig.add_hline(y=avg, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
                      annotation_text=f"avg {avg:.0f}", annotation_font_color=TEXT_MUTED,
                      annotation_position="top right")
        charts.append(("Cadence", chart_style(fig)))

    if "watts" in df.columns and df["watts"].notna().any():
        fig = go.Figure(go.Scatter(
            x=df["dist_km"], y=df["watts"], mode="lines",
            line=dict(color=C_INDIGO, width=1.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(129,140,248,0.10)",
            hovertemplate="<b>%{x:.2f} km</b><br>Power: %{y:.0f} W<extra></extra>",
        ))
        avg = df["watts"].dropna().mean()
        fig.add_hline(y=avg, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
                      annotation_text=f"avg {avg:.0f} W", annotation_font_color=TEXT_MUTED,
                      annotation_position="top right")
        fig.update_layout(yaxis=dict(ticksuffix=" W"))
        charts.append(("Power", chart_style(fig)))

    if not charts:
        st.caption("No metric streams available for this activity (outdoor GPS required).")
        return

    for i in range(0, len(charts), 2):
        pair = charts[i:i + 2]
        cols = st.columns(len(pair))
        for col, (title, fig) in zip(cols, pair):
            with col:
                st.markdown(f'<p class="chart-label">{title}</p>', unsafe_allow_html=True)
                st.plotly_chart(fig, width='stretch')


# ── Public entry point ────────────────────────────────────────────────────────

# Metrics: key → (label, invert, high_label, low_label)
# high is always red (top of legend), low is always green (bottom) — no exceptions
_METRIC_DEFS = {
    "hr":       ("Heart Rate", False, "High HR",       "Low HR"),
    "velocity": ("Pace",       True,  "Slow",          "Fast"),   # invert: fast (high vel) = green
    "ele":      ("Elevation",  False, "High Elev.",    "Low Elev."),
    "cadence":  ("Cadence",    False, "High Cadence",  "Low Cadence"),
    "watts":    ("Power",      False, "High Power",    "Low Power"),
}


def show_analysis(activity_id: int, activity_name: str = "") -> None:
    """Render stream analysis: colored route map + metric charts."""
    with st.spinner("Loading GPS streams…"):
        try:
            data = _load_streams(activity_id)
        except Exception as e:
            st.error(f"Stream data unavailable: {e}")
            return

    if data.get("error"):
        st.warning(f"No stream data: {data['error']}")
        return

    points = data.get("points", [])
    if not points:
        st.info("No GPS stream data for this activity.")
        return

    # Build DataFrame
    df = pd.DataFrame(points)
    df["dist_km"] = df["dist_m"].fillna(pd.Series(range(len(df)))) / 1000

    # Determine which overlay metrics are available
    available: Dict[str, Tuple] = {}
    if data.get("has_hr"):      available["hr"]       = _METRIC_DEFS["hr"]
    if data.get("has_velocity"): available["velocity"] = _METRIC_DEFS["velocity"]
    if "ele" in df.columns and df["ele"].notna().any():
        available["ele"] = _METRIC_DEFS["ele"]
    if data.get("has_cadence"): available["cadence"]  = _METRIC_DEFS["cadence"]
    if data.get("has_watts"):   available["watts"]    = _METRIC_DEFS["watts"]

    st.markdown("### Activity Analysis")

    # ── Metric selector + map ─────────────────────────────────────────────────
    map_col, legend_col = st.columns([11, 1])

    with map_col:
        if available:
            choice = st.radio(
                "Color route by",
                [v[0] for v in available.values()],
                horizontal=True,
                key=f"analysis_metric_{activity_id}",
                label_visibility="collapsed",
            )
            chosen_key = next(k for k, v in available.items() if v[0] == choice)
            _, invert, high_lbl, low_lbl = available[chosen_key]
            fmap = _colored_route_map(points, chosen_key, invert=invert)
        else:
            fmap = _plain_route_map(points)
            high_lbl, low_lbl = "", ""

        if fmap:
            st_folium(fmap, height=440, width='stretch', returned_objects=[])
        else:
            st.info("Not enough GPS points for route visualization.")

    with legend_col:
        if available and fmap:
            st.markdown(_legend_html(high_lbl, low_lbl), unsafe_allow_html=True)

    # ── Stream charts ─────────────────────────────────────────────────────────
    st.divider()
    _stream_charts(df)
