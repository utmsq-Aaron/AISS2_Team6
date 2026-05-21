"""Visual constants, activity icons, and CSS injection for FitDash."""

from typing import Dict

import plotly.graph_objects as go
import streamlit as st

# ── Colour palette ────────────────────────────────────────────────────────────

ACCENT        = "#FC4C02"
STRAVA_ORANGE = ACCENT

# Dark navy base — not flat grey
BG_CARD    = "#0F0F1E"
BG_SURFACE = "#16162A"
BORDER     = "#2A2A45"

# Readable text on dark navy
TEXT_PRIMARY = "#EEEEFF"
TEXT_MUTED   = "#9BA3C8"

# Per-metric colours
C_GREEN   = "#22C55E"   # body battery / good
C_ROSE    = "#FB7185"   # heart rate
C_INDIGO  = "#818CF8"   # sleep
C_CYAN    = "#22D3EE"   # steps
C_PURPLE  = "#C084FC"   # stress
C_AMBER   = "#FCD34D"   # HRV / warnings
C_ORANGE  = ACCENT      # Strava / activities

# Sport breakdown palette — visually distinct
CHART_COLORS = [
    C_ORANGE, "#3B82F6", C_GREEN, "#8B5CF6",
    C_AMBER,  "#EC4899", C_CYAN,  "#84CC16",
]

# ── Map tiles ─────────────────────────────────────────────────────────────────

DARK_MAP_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
DARK_MAP_ATTR  = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    ' &copy; <a href="https://carto.com/attributions">CARTO</a>'
)

# ── Activity icons ────────────────────────────────────────────────────────────

ACTIVITY_ICONS: Dict[str, str] = {
    "Run": "🏃", "Ride": "🚴", "Hike": "🥾", "Walk": "🚶",
    "Swim": "🏊", "Workout": "💪", "WeightTraining": "🏋️",
    "Yoga": "🧘", "EBikeRide": "⚡", "VirtualRide": "🖥️",
    "VirtualRun": "🖥️", "NordicSki": "⛷️", "AlpineSki": "⛷️",
    "BackcountrySki": "⛷️", "IceSkate": "⛸️", "Rowing": "🚣",
    "Kayaking": "🛶", "StandUpPaddling": "🏄", "Soccer": "⚽",
    "Tennis": "🎾", "RockClimbing": "🧗", "Crossfit": "💪",
}


def activity_icon(sport_type: str) -> str:
    return ACTIVITY_ICONS.get(sport_type, "🏅")


# ── Global CSS ────────────────────────────────────────────────────────────────

def inject_css() -> None:
    st.markdown(f"""
<style>
/* ── Layout ───────────────────────────────────────────────── */
.main .block-container {{
    padding-top   : 1.6rem;
    padding-bottom: 3rem;
}}

/* ── Metric cards ─────────────────────────────────────────── */
[data-testid="metric-container"] {{
    background   : {BG_CARD};
    border       : 1px solid {BORDER};
    border-radius: 14px;
    padding      : 16px 20px;
    transition   : border-color .2s, box-shadow .2s;
}}
[data-testid="metric-container"]:hover {{
    border-color: rgba(252,76,2,.5);
    box-shadow  : 0 0 0 1px rgba(252,76,2,.18);
}}
[data-testid="metric-container"] label {{
    color         : {TEXT_MUTED} !important;
    font-size     : 11px !important;
    text-transform: uppercase;
    letter-spacing: .7px;
    font-weight   : 500 !important;
}}
[data-testid="metric-container"] [data-testid="stMetricValue"] {{
    font-size  : 1.5rem !important;
    font-weight: 700 !important;
    color      : {TEXT_PRIMARY} !important;
}}

/* ── Tabs ─────────────────────────────────────────────────── */
div[data-testid="stTabs"] button {{
    font-size  : 13px;
    font-weight: 500;
    color      : {TEXT_MUTED};
}}
div[data-testid="stTabs"] button[aria-selected="true"] {{
    color              : {ACCENT} !important;
    border-bottom-color: {ACCENT} !important;
    font-weight        : 700;
}}

/* ── Sidebar ──────────────────────────────────────────────── */
[data-testid="stSidebar"] {{
    background  : #0A0A18;
    border-right: 1px solid {BORDER};
}}
[data-testid="stSidebar"] .stMarkdown h3 {{
    color         : {TEXT_MUTED};
    font-size     : 10px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
}}

/* ── Activity / content cards ─────────────────────────────── */
[data-testid="stVerticalBlockBorderWrapper"] {{
    background   : {BG_CARD} !important;
    border-color : {BORDER} !important;
    border-radius: 14px !important;
    transition   : border-color .2s;
}}
[data-testid="stVerticalBlockBorderWrapper"]:hover {{
    border-color: rgba(252,76,2,.4) !important;
}}

/* ── Expanders ────────────────────────────────────────────── */
[data-testid="stExpander"] {{
    background   : {BG_CARD};
    border       : 1px solid {BORDER} !important;
    border-radius: 10px !important;
}}
[data-testid="stExpander"] summary {{
    color      : {TEXT_MUTED} !important;
    font-size  : 12px;
    font-weight: 500;
}}

/* ── Badges ───────────────────────────────────────────────── */
.badge-ok   {{ color: {C_GREEN};  font-weight: 600; }}
.badge-warn {{ color: {C_AMBER};  font-weight: 600; }}
.badge-err  {{ color: #EF4444;    font-weight: 600; }}

/* ── Chart section labels ─────────────────────────────────── */
.chart-label {{
    font-size     : 11px;
    font-weight   : 600;
    color         : {TEXT_MUTED};
    text-transform: uppercase;
    letter-spacing: .8px;
    margin-bottom : 2px;
}}

/* ── Map rounded corners ──────────────────────────────────── */
iframe {{ border-radius: 14px !important; }}

/* ── Divider ──────────────────────────────────────────────── */
hr {{ border-color: {BORDER} !important; margin: 1.2rem 0 !important; }}

/* ── Primary button ───────────────────────────────────────── */
[data-testid="baseButton-primary"] {{
    background   : {ACCENT} !important;
    border-color : {ACCENT} !important;
    font-weight  : 600 !important;
    border-radius: 8px !important;
    color        : #fff !important;
}}
[data-testid="baseButton-primary"]:hover {{
    background  : #e04400 !important;
    border-color: #e04400 !important;
}}

/* ── Checkbox labels ──────────────────────────────────────── */
[data-testid="stCheckbox"] label {{
    color    : {TEXT_PRIMARY} !important;
    font-size: 14px;
}}

/* ── Caption / muted text ─────────────────────────────────── */
[data-testid="stCaptionContainer"] p,
[data-testid="stCaptionContainer"] span {{ color: {TEXT_MUTED} !important; }}
caption, small {{ color: {TEXT_MUTED} !important; }}
</style>
""", unsafe_allow_html=True)


# ── Plotly chart theme ────────────────────────────────────────────────────────

def chart_style(fig: go.Figure, title: str = "") -> go.Figure:
    """Apply FitDash dark theme to a Plotly figure."""
    fig.update_layout(
        title        = dict(text=title, font=dict(size=13, color=TEXT_MUTED), pad=dict(t=0)),
        plot_bgcolor = "rgba(0,0,0,0)",
        paper_bgcolor= "rgba(0,0,0,0)",
        margin       = dict(l=4, r=4, t=28 if title else 8, b=4),
        font         = dict(color=TEXT_MUTED, size=11, family="system-ui, sans-serif"),
        legend       = dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT_MUTED, size=11),
        ),
        hoverlabel   = dict(bgcolor="#1C1C32", font_color=TEXT_PRIMARY, bordercolor=BORDER),
        colorway     = CHART_COLORS,
    )
    fig.update_xaxes(
        showgrid=False, zeroline=False,
        color=TEXT_MUTED, linecolor=BORDER,
        tickfont=dict(size=10, color=TEXT_MUTED),
    )
    fig.update_yaxes(
        gridcolor="rgba(155,163,200,0.08)", zeroline=False,
        color=TEXT_MUTED, linecolor=BORDER,
        tickfont=dict(size=10, color=TEXT_MUTED),
    )
    return fig
