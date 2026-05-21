"""Health tab — Garmin wellness: Body Battery, sleep stages, HR, steps, stress, HRV, training."""

import json
from typing import Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from ui.shared import garmin_connected, get_garmin_mcp, run_async
from ui.styles import (
    C_AMBER, C_CYAN, C_GREEN, C_INDIGO, C_PURPLE, C_ROSE, ACCENT,
    TEXT_MUTED, TEXT_PRIMARY, BORDER, BG_CARD, chart_style,
)

# ── Sleep stage colours ───────────────────────────────────────────────────────
C_SLEEP_DEEP  = "#1E40AF"
C_SLEEP_REM   = "#7C3AED"
C_SLEEP_LIGHT = "#60A5FA"
C_SLEEP_AWAKE = "#ED79D5"

# ── Sleep quality thresholds ──────────────────────────────────────────────────
_SLEEP_TOTAL_GOOD_H      = 7     # hours — below this is "too short"
_SLEEP_TOTAL_GREAT_H     = 8     # hours — above this is "great"
_SLEEP_DEEP_GOOD_PCT     = 13    # % of total sleep — minimum for "good" deep sleep
_SLEEP_DEEP_EXCELLENT_PCT = 20   # % of total sleep — "excellent" deep sleep
_SLEEP_REM_GOOD_PCT      = 15    # % of total sleep — minimum for "good" REM sleep
_SLEEP_REM_EXCELLENT_PCT = 22    # % of total sleep — "excellent" REM sleep

# ── Activity goals ────────────────────────────────────────────────────────────
_DAILY_STEPS_GOAL          = 10_000
_WEEKLY_INTENSITY_GOAL_MIN = 150   # WHO moderate-intensity minutes per week

# ── Period options ────────────────────────────────────────────────────────────
_PERIODS: Dict[str, int] = {
    "7 days":   7,
    "14 days":  14,
    "30 days":  30,
    "3 months": 90,
    "6 months": 180,
    "1 year":   365,
    "2 years":  730,
    "3 years":  1095,
}


# ── Cached data loaders ───────────────────────────────────────────────────────

def _safe_load(fn):
    try:
        return fn() or {}
    except Exception:
        return {}


@st.cache_data(ttl=1800, show_spinner=False)
def load_wellness(days: int = 14) -> Dict:
    garmin = get_garmin_mcp()
    if garmin is None:
        return {}
    return json.loads(run_async(garmin._dispatch("get_garmin_wellness_trends", {"days": days})))

@st.cache_data(ttl=600, show_spinner=False)
def load_training_metrics() -> Dict:
    garmin = get_garmin_mcp()
    if garmin is None:
        return {}
    return json.loads(run_async(garmin._dispatch("get_garmin_training_metrics", {})))

@st.cache_data(ttl=600, show_spinner=False)
def load_hrv() -> Dict:
    garmin = get_garmin_mcp()
    if garmin is None:
        return {}
    return json.loads(run_async(garmin._dispatch("get_garmin_hrv_status", {})))

@st.cache_data(ttl=300, show_spinner=False)
def load_today() -> Dict:
    garmin = get_garmin_mcp()
    if garmin is None:
        return {}
    return json.loads(run_async(garmin._dispatch("get_garmin_daily_health", {})))


# ── Chart builders ────────────────────────────────────────────────────────────

def _section(label: str, margin_top: str = "1.4rem") -> None:
    st.markdown(
        f'<p class="chart-label" style="margin-top:{margin_top};margin-bottom:4px">{label}</p>',
        unsafe_allow_html=True,
    )


def _sleep_hover(row) -> str:
    deep_h  = float(row.get("deep_h")  or 0)
    light_h = float(row.get("light_h") or 0)
    rem_h   = float(row.get("rem_h")   or 0)
    awake_h = float(row.get("awake_h") or 0)
    score   = row.get("sleep_score")
    total   = deep_h + light_h + rem_h + awake_h
    if total <= 0:
        return "<b>No sleep data recorded</b>"

    deep_pct  = deep_h  / total * 100
    rem_pct   = rem_h   / total * 100
    light_pct = light_h / total * 100
    awake_pct = awake_h / total * 100

    # Per-factor quality labels and colours
    def _dur_label(h):
        if h >= _SLEEP_TOTAL_GREAT_H: return "Great",      "#22C55E"
        if h >= _SLEEP_TOTAL_GOOD_H:  return "Good",       "#60A5FA"
        if h >= 6:                    return "A bit short","#FCD34D"
        return                               "Too short",  "#FB7185"

    def _deep_label(pct):
        if pct >= _SLEEP_DEEP_EXCELLENT_PCT: return "Excellent", "#22C55E"
        if pct >= _SLEEP_DEEP_GOOD_PCT:      return "Good",      "#60A5FA"
        if pct >= 7:                         return "Low",       "#FCD34D"
        return                                      "Very low",  "#FB7185"

    def _rem_label(pct):
        if pct >= _SLEEP_REM_EXCELLENT_PCT: return "Excellent", "#22C55E"
        if pct >= _SLEEP_REM_GOOD_PCT:      return "Good",      "#60A5FA"
        if pct >= 9:                        return "Low",       "#FCD34D"
        return                                     "Very low",  "#FB7185"

    def _awake_label(h):
        if h <= 0.25: return "Minimal",    "#22C55E"
        if h <= 0.5:  return "Normal",     "#60A5FA"
        if h <= 1.0:  return "Elevated",   "#FCD34D"
        return              "Disruptive",  "#FB7185"

    def _tag(label, color):
        return f'<span style="color:{color};font-weight:600">{label}</span>'

    def _row(dot_color, label, hours, pct, tag_html=""):
        dot = f'<span style="color:{dot_color}">●</span>'
        pct_str = f" ({pct:.0f}%)" if pct is not None else ""
        return f"{dot} <b>{label}</b>  {hours:.1f} h{pct_str}  {tag_html}"

    dur_lbl,   dur_col   = _dur_label(total)
    deep_lbl,  deep_col  = _deep_label(deep_pct)
    rem_lbl,   rem_col   = _rem_label(rem_pct)
    awake_lbl, awake_col = _awake_label(awake_h)

    # ── Insight paragraph ─────────────────────────────────────────────────────
    # Open with what mattered most tonight
    deep_ok  = deep_pct  >= _SLEEP_DEEP_GOOD_PCT
    rem_ok   = rem_pct   >= _SLEEP_REM_GOOD_PCT
    awake_ok = awake_h   <= 0.5
    dur_ok   = total     >= _SLEEP_TOTAL_GOOD_H

    if not dur_ok and total < 5:
        insight = ("Very short night — your body barely had time to complete full "
                   "sleep cycles. Even one extra hour makes a noticeable difference.")
    elif deep_pct >= 20 and rem_ok:
        insight = ("You got excellent deep and REM sleep tonight — your body repaired "
                   "itself and your brain processed the day. This is what recovery "
                   "looks like.")
    elif deep_pct >= 20:
        insight = ("Strong deep sleep — your immune system and muscles got a solid "
                   "repair session. A little more REM would round things out for "
                   "mental recovery too.")
    elif rem_pct >= 22 and not deep_ok:
        insight = ("Good brain recovery tonight, but your body missed out on enough "
                   "deep sleep. Deep sleep is where physical repair and immune "
                   "strengthening happen.")
    elif not deep_ok and not rem_ok:
        insight = ("Both deep and REM sleep were on the lower side. A consistent "
                   "wind-down routine — no screens, dim lights, cool room — can "
                   "push you into deeper stages sooner.")
    elif not awake_ok:
        insight = ("Frequent wake-ups broke your sleep into fragments. Continuity "
                   "matters — each interruption cuts short a recovery cycle. Check "
                   "room temperature, hydration, and stress levels.")
    elif deep_ok and rem_ok:
        insight = ("Solid sleep overall — good balance of physical and mental "
                   "recovery. Keep the same sleep schedule to lock in these results.")
    else:
        insight = ("Decent night. Deep sleep supports your immune system and muscles; "
                   "REM looks after memory and mood. Aim to improve whichever is lower.")

    # ── Assemble tooltip ──────────────────────────────────────────────────────
    sep = "─" * 30
    header = (f"<b>Sleep Score: {int(score)}</b>" if score
              else "<b>Sleep breakdown</b>")

    lines = [
        header,
        sep,
        f'<span style="color:#9BA3C8">  Total    {total:.1f} h  </span>'
        f'{_tag(dur_lbl, dur_col)}',
        _row(C_SLEEP_DEEP,  "Deep ",  deep_h,  deep_pct,  _tag(deep_lbl,  deep_col)),
        _row(C_SLEEP_REM,   "REM  ",  rem_h,   rem_pct,   _tag(rem_lbl,   rem_col)),
        _row(C_SLEEP_LIGHT, "Light",  light_h, light_pct),
        _row(C_SLEEP_AWAKE, "Awake",  awake_h, awake_pct, _tag(awake_lbl, awake_col)),
        sep,
        f"<i>{insight}</i>",
    ]
    return "<br>".join(lines)


def _sleep_stages_chart(df: pd.DataFrame) -> Optional[go.Figure]:
    needed = {"deep_h", "light_h", "rem_h", "awake_h"}
    if not needed.intersection(df.columns):
        return None
    df = df.copy()
    for col in needed:
        if col not in df.columns:
            df[col] = 0.0
    df = df.dropna(subset=["total_sleep_h"])
    if df.empty:
        return None

    # Pre-compute rich hover HTML once per row
    hover_texts = df.apply(_sleep_hover, axis=1).tolist()

    has_score = "sleep_score" in df.columns and df["sleep_score"].notna().any()
    fig = make_subplots(specs=[[{"secondary_y": has_score}]])

    for col, color, label in [
        ("deep_h",  C_SLEEP_DEEP,  "Deep"),
        ("rem_h",   C_SLEEP_REM,   "REM"),
        ("light_h", C_SLEEP_LIGHT, "Light"),
        ("awake_h", C_SLEEP_AWAKE, "Awake"),
    ]:
        fig.add_trace(go.Bar(
            x=df["date"], y=df[col], name=label,
            marker_color=color, marker_line_width=0,
            customdata=hover_texts,
            hovertemplate="<b>%{x}</b><br>%{customdata}<extra></extra>",
        ), secondary_y=False)

    fig.add_hline(
        y=8, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
        annotation_text="8 h", annotation_font_color=TEXT_MUTED,
        annotation_position="top left", secondary_y=False,
    )

    if has_score:
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["sleep_score"], name="Score",
            mode="lines+markers",
            line=dict(color=C_AMBER, width=2, shape="spline"),
            marker=dict(size=5, color=C_AMBER),
            hovertemplate="<b>%{x}</b><br>Sleep Score: %{y}<extra></extra>",
        ), secondary_y=True)
        fig.update_yaxes(
            range=[0, 100], ticksuffix="", title_text="Score",
            secondary_y=True, showgrid=False,
            color=TEXT_MUTED, tickfont=dict(size=10, color=TEXT_MUTED),
        )

    fig.update_layout(
        barmode="stack",
        yaxis=dict(ticksuffix=" h"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT_MUTED, size=10),
        ),
        hovermode="closest",
    )
    return chart_style(fig)


def _body_battery_chart(df: pd.DataFrame) -> Optional[go.Figure]:
    if df.empty or "body_battery_high" not in df.columns:
        return None
    bb_df = df.dropna(subset=["body_battery_high"]).copy()
    if bb_df.empty:
        return None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=bb_df["date"], y=bb_df["body_battery_high"], name="Peak",
        line=dict(color=C_GREEN, width=2.5, shape="spline"),
        mode="lines",
        hovertemplate="<b>%{x}</b><br>Peak: %{y}%<extra></extra>",
    ))
    if "body_battery_low" in bb_df.columns and bb_df["body_battery_low"].notna().any():
        fig.add_trace(go.Scatter(
            x=bb_df["date"], y=bb_df["body_battery_low"], name="Low",
            line=dict(color=C_ROSE, width=1.5, shape="spline"),
            fill="tonexty", fillcolor="rgba(34,197,94,0.12)",
            mode="lines",
            hovertemplate="<b>%{x}</b><br>Low: %{y}%<extra></extra>",
        ))
    fig.update_layout(yaxis=dict(range=[0, 100], ticksuffix="%"), hovermode="x unified")
    return chart_style(fig)


def _hr_chart(df: pd.DataFrame) -> Optional[go.Figure]:
    has_resting = "resting_hr" in df.columns and df["resting_hr"].notna().any()
    has_max = "max_hr" in df.columns and df["max_hr"].notna().any()
    if not (has_resting or has_max):
        return None
    df = df.copy()
    fig = go.Figure()
    if has_resting:
        rest_df = df.dropna(subset=["resting_hr"])
        fig.add_trace(go.Scatter(
            x=rest_df["date"], y=rest_df["resting_hr"], name="Resting HR",
            mode="lines+markers",
            line=dict(color=C_ROSE, width=2.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(251,113,133,0.10)",
            marker=dict(size=5, color=C_ROSE, line=dict(color=BG_CARD, width=1.5)),
            hovertemplate="<b>%{x}</b><br>Resting HR: %{y} bpm<extra></extra>",
        ))
        avg = rest_df["resting_hr"].mean()
        fig.add_hline(
            y=avg, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
            annotation_text=f"avg {avg:.0f} bpm", annotation_font_color=TEXT_MUTED,
            annotation_position="top right",
        )
    if has_max:
        max_df = df.dropna(subset=["max_hr"])
        fig.add_trace(go.Scatter(
            x=max_df["date"], y=max_df["max_hr"], name="High HR",
            mode="lines+markers",
            line=dict(color=C_CYAN, width=2.0, shape="spline"),
            marker=dict(size=4, color=C_CYAN, line=dict(color=BG_CARD, width=1.2)),
            hovertemplate="<b>%{x}</b><br>High HR: %{y}<extra></extra>",
        ))
    fig.update_layout(yaxis=dict(ticksuffix=" bpm"))
    return chart_style(fig)


def _steps_chart(df: pd.DataFrame) -> Optional[go.Figure]:
    if "steps" not in df.columns or not df["steps"].notna().any():
        return None
    df = df.dropna(subset=["steps"]).copy()
    colors = [C_GREEN if s >= _DAILY_STEPS_GOAL else C_CYAN for s in df["steps"]]
    fig = go.Figure(go.Bar(
        x=df["date"], y=df["steps"],
        marker_color=colors, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>Steps: %{y:,}<extra></extra>",
    ))
    fig.add_hline(
        y=_DAILY_STEPS_GOAL, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
        annotation_text=f"{_DAILY_STEPS_GOAL:,} goal", annotation_font_color=TEXT_MUTED,
        annotation_position="top right",
    )
    return chart_style(fig)


def _stress_chart(df: pd.DataFrame) -> Optional[go.Figure]:
    if "avg_stress" not in df.columns or not df["avg_stress"].notna().any():
        return None
    df = df.dropna(subset=["avg_stress"]).copy()

    def _stress_color(v):
        if v < 26:  return "rgba(34,197,94,0.7)"
        if v < 51:  return "rgba(252,211,77,0.7)"
        if v < 76:  return "rgba(251,113,133,0.7)"
        return "rgba(220,38,38,0.7)"

    colors = [_stress_color(v) for v in df["avg_stress"]]
    fig = go.Figure(go.Bar(
        x=df["date"], y=df["avg_stress"],
        marker_color=colors, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>Stress: %{y:.0f}<extra></extra>",
    ))
    fig.add_hrect(y0=0,  y1=25, fillcolor="rgba(34,197,94,0.04)",   line_width=0)
    fig.add_hrect(y0=25, y1=50, fillcolor="rgba(252,211,77,0.04)",  line_width=0)
    fig.add_hrect(y0=50, y1=75, fillcolor="rgba(251,113,133,0.04)", line_width=0)
    fig.add_hrect(y0=75, y1=100,fillcolor="rgba(220,38,38,0.04)",   line_width=0)
    fig.update_layout(yaxis=dict(range=[0, 100]))
    return chart_style(fig)


def _intensity_chart(df: pd.DataFrame) -> Optional[go.Figure]:
    if "intensity_min" not in df.columns or not df["intensity_min"].notna().any():
        return None
    df = df.copy()
    # Fill missing days with 0 — prevents Plotly from stretching active-day
    # bars across the gap when some dates have no intensity data.
    df["intensity_min"] = df["intensity_min"].fillna(0)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["date"], y=df["intensity_min"], name="Total",
        marker_color=C_CYAN, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>Intensity: %{y:.0f} min<extra></extra>",
    ))
    fig.add_hline(
        y=_WEEKLY_INTENSITY_GOAL_MIN / 7, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
        annotation_text=f"~{_WEEKLY_INTENSITY_GOAL_MIN // 7} min/day goal",
        annotation_font_color=TEXT_MUTED, annotation_position="top right",
    )
    fig.update_layout(yaxis=dict(ticksuffix=" min"))
    return chart_style(fig)


def _calories_chart(df: pd.DataFrame) -> Optional[go.Figure]:
    has_total  = "total_cal"  in df.columns and df["total_cal"].notna().any()
    has_active = "active_cal" in df.columns and df["active_cal"].notna().any()
    if not has_total and not has_active:
        return None

    fig = go.Figure()
    if has_total:
        t = df.dropna(subset=["total_cal"])
        fig.add_trace(go.Bar(
            x=t["date"], y=t["total_cal"], name="Total",
            marker_color=C_AMBER, marker_line_width=0,
            hovertemplate="<b>%{x}</b><br>Total: %{y:,} kcal<extra></extra>",
        ))
    if has_active:
        a = df.dropna(subset=["active_cal"])
        fig.add_trace(go.Bar(
            x=a["date"], y=a["active_cal"], name="Active",
            marker_color=ACCENT, marker_line_width=0,
            hovertemplate="<b>%{x}</b><br>Active: %{y:,} kcal<extra></extra>",
        ))
    fig.update_layout(barmode="group", yaxis=dict(ticksuffix=" kcal"))
    return chart_style(fig)


def _hrv_gauge(hrv: Dict) -> Optional[go.Figure]:
    val = hrv.get("last_night_hrv")
    lo  = hrv.get("baseline_balanced_low")  or 0
    hi  = hrv.get("baseline_balanced_high") or 0
    if not val:
        return None
    max_val = max(120, val + 20)
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=val,
        number=dict(suffix=" ms", font=dict(color=TEXT_PRIMARY, size=32)),
        gauge=dict(
            axis=dict(range=[0, max_val], tickcolor=TEXT_MUTED, tickfont=dict(size=9)),
            bar=dict(color=C_AMBER, thickness=0.3),
            bgcolor="rgba(0,0,0,0)",
            bordercolor=BORDER,
            steps=[
                dict(range=[0, lo],       color="rgba(251,113,133,0.18)"),
                dict(range=[lo, hi],      color="rgba(252,211,77,0.18)"),
                dict(range=[hi, max_val], color="rgba(34,197,94,0.18)"),
            ],
        ),
    ))
    fig.update_layout(
        height=200,
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=16, r=16, t=10, b=0),
        font=dict(color=TEXT_MUTED, size=11),
    )
    return fig


# ── Main render ───────────────────────────────────────────────────────────────

def render_health() -> None:
    if not garmin_connected():
        st.markdown("### Health & Wellness")
        st.warning(
            "**Garmin not connected.**\n\n"
            "1. Add `GARMIN_EMAIL` and `GARMIN_PASSWORD` to `.env`\n"
            "2. Run: `python auth/garmin_setup.py`\n"
            "3. Refresh this page"
        )
        return

    # ── Period selector ───────────────────────────────────────────────────────
    period = st.radio(
        "Period",
        list(_PERIODS.keys()),
        index=1,
        horizontal=True,
        key="health_period",
        label_visibility="collapsed",
    )
    days = _PERIODS[period]

    # ── Load data ─────────────────────────────────────────────────────────────
    if days > 30:
        st.caption(
            f"⏳ {days} days of data — fetching in parallel, first load takes "
            f"~{max(5, days // 20)} – {max(10, days // 10)} seconds. Cached for 30 min."
        )

    with st.spinner("Loading Garmin data…"):
        today   = _safe_load(load_today)
        metrics = _safe_load(load_training_metrics)
        hrv     = _safe_load(load_hrv)

    with st.spinner(f"Loading {period} health trends…"):
        wellness = _safe_load(lambda: load_wellness(days))

    trend_days_list: List[Dict] = wellness.get("trend", [])
    df = pd.DataFrame(trend_days_list) if trend_days_list else pd.DataFrame()

    # ── Today's snapshot ──────────────────────────────────────────────────────
    st.markdown("### Today")
    if today:
        tc = st.columns(5)
        bb_val = today.get("body_battery_now") or today.get("body_battery_min")
        tc[0].metric("Body Battery", f"{bb_val}%" if bb_val is not None else "—")
        tc[1].metric("Steps",        f"{today.get('steps') or 0:,}")
        tc[2].metric("Resting HR",   f"{today.get('resting_hr') or '—'} bpm")
        tc[3].metric("Active Cal",   f"{today.get('active_calories') or '—'} kcal")
        tc[4].metric("Avg Stress",   f"{today.get('avg_stress') or '—'}")

        # Second row — intensity & calories (only when available)
        int_total = today.get("intensity_minutes")
        int_mod   = today.get("moderate_intensity_min")
        int_vig   = today.get("vigorous_intensity_min")
        total_cal = today.get("total_calories")
        floors    = today.get("floors_climbed")
        row2 = [(v, k) for v, k in [
            (int_total, "Intensity Min"),
            (int_mod,   "Moderate Min"),
            (int_vig,   "Vigorous Min"),
            (total_cal, "Total Cal"),
            (floors,    "Floors"),
        ] if v is not None]
        if row2:
            rc = st.columns(len(row2))
            for col, (val, label) in zip(rc, row2):
                suffix = " kcal" if "Cal" in label else (" min" if "Min" in label else "")
                col.metric(label, f"{val:,}{suffix}" if isinstance(val, int) else f"{val}{suffix}")
    else:
        st.caption("Today's data unavailable.")
    st.divider()

    # ── Training Status ───────────────────────────────────────────────────────
    if metrics:
        vo2 = metrics.get("vo2max_running") or metrics.get("vo2max_cycling")
        ts  = (metrics.get("training_status") or "").replace("_", " ").title()
        r   = metrics.get("training_readiness_score")
        l7  = metrics.get("training_load_7d")
        l28 = metrics.get("training_load_28d")

        if any([vo2, ts, r is not None, l7, l28]):
            st.markdown("### Training Status")
            tc = st.columns(5)
            tc[0].metric("VO₂max",          f"{vo2:.1f}" if vo2 else "—")
            tc[1].metric("Training Status",  ts or "—")
            tc[2].metric("Readiness Score",  str(r) if r is not None else "—")
            tc[3].metric("Load 7 d",         f"{l7:.0f}" if l7 else "—")
            tc[4].metric("Load 28 d",        f"{l28:.0f}" if l28 else "—")

            rp = metrics.get("race_predictions") or {}
            if any(v for v in rp.values() if v):
                _section("Race Predictions", margin_top=".8rem")
                rc = st.columns(4)
                rc[0].metric("5 K",           rp.get("5k")            or "—")
                rc[1].metric("10 K",          rp.get("10k")           or "—")
                rc[2].metric("Half Marathon", rp.get("half_marathon") or "—")
                rc[3].metric("Marathon",      rp.get("marathon")      or "—")
            st.divider()

    # ── HRV ───────────────────────────────────────────────────────────────────
    if hrv and hrv.get("last_night_hrv"):
        st.markdown("### HRV Status")
        hc1, hc2 = st.columns([1, 2])
        with hc1:
            fig = _hrv_gauge(hrv)
            if fig:
                st.plotly_chart(fig, width='stretch')
        with hc2:
            lo  = hrv.get("baseline_balanced_low")
            hi  = hrv.get("baseline_balanced_high")
            st.metric("Last Night HRV",  f"{hrv['last_night_hrv']} ms")
            st.metric("Baseline Range",  f"{lo} – {hi} ms" if lo and hi else "—")
            status = (hrv.get("status") or "—").replace("_", " ").title()
            color  = C_GREEN if "balanced" in status.lower() else C_ROSE
            st.markdown(
                f'<span style="color:{color};font-weight:700;font-size:15px">{status}</span>',
                unsafe_allow_html=True,
            )
            if hrv.get("feedback"):
                st.caption(hrv["feedback"])
        st.divider()

    # ── Trend charts ──────────────────────────────────────────────────────────
    st.markdown(f"### {period} Trends")

    if df.empty:
        st.info("No wellness trend data. Make sure `auth/garmin_setup.py` has been run.")
        return

    _section("Sleep")
    fig = _sleep_stages_chart(df)
    if fig:
        st.plotly_chart(fig, width='stretch')
    else:
        st.caption("No sleep stage data available.")

    c1, c2 = st.columns(2)
    with c1:
        _section("Body Battery")
        fig = _body_battery_chart(df)
        if fig:
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No Body Battery data.")
    with c2:
        _section("Heart Rate")
        fig = _hr_chart(df)
        if fig:
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No resting HR data.")

    c3, c4 = st.columns(2)
    with c3:
        _section("Daily Steps")
        fig = _steps_chart(df)
        if fig:
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No step data.")
    with c4:
        _section("Average Stress")
        fig = _stress_chart(df)
        if fig:
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No stress data.")

    int_fig = _intensity_chart(df)
    cal_fig = _calories_chart(df)
    if int_fig or cal_fig:
        c5, c6 = st.columns(2)
        with c5:
            _section("Intensity Minutes")
            if int_fig:
                st.plotly_chart(int_fig, width='stretch')
            else:
                st.caption("No intensity data.")
        with c6:
            _section("Calories")
            if cal_fig:
                st.plotly_chart(cal_fig, width='stretch')
            else:
                st.caption("No calorie data.")
