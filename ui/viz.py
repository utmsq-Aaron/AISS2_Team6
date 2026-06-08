"""Chat visualization registry.

Maps MCP tool names → compact Streamlit chart renderers.
All data arrives pre-fetched from the orchestrator's MCP tool calls — no extra
network requests happen here.

Adding a new visualization:
    @register("my_tool_name")
    def viz_my_tool(data: dict) -> None:
        ...  # render whatever Streamlit widgets make sense
"""

import json
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.styles import (
    ACCENT, C_AMBER, C_CYAN, C_GREEN, C_INDIGO, C_PURPLE, C_ROSE,
    TEXT_MUTED, chart_style, activity_icon,
)
# Reuse chart builders from health.py directly — avoids duplicating the logic
from ui.health import (
    _sleep_stages_chart, _body_battery_chart, _hr_chart,
    _steps_chart, _stress_chart, _intensity_chart, _hrv_gauge,
)


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: dict = {}


def register(tool_name: str):
    """Decorator — binds an MCP tool name to a Streamlit renderer."""
    def decorator(fn):
        _REGISTRY[tool_name] = fn
        return fn
    return decorator


def can_render(tool_name: str) -> bool:
    return tool_name in _REGISTRY


def render(tool_name: str, result_json: str, metric_focus: str = "") -> None:
    """Dispatch to the registered renderer. Silent no-op on any failure."""
    fn = _REGISTRY.get(tool_name)
    if not fn:
        return
    try:
        data = json.loads(result_json) if isinstance(result_json, str) else result_json
        if not data or (isinstance(data, dict) and data.get("error")):
            return
        if metric_focus and isinstance(data, dict):
            data["_metric_focus"] = metric_focus
        fn(data)
    except Exception:
        pass  # visualization failures must never crash the chat


# ── Shared helpers ────────────────────────────────────────────────────────────

def _chart(fig: go.Figure, height: int = 260) -> None:
    fig.update_layout(height=height, margin=dict(t=8, b=28, l=8, r=8))
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})


def _label(text: str) -> None:
    st.markdown(
        f'<p style="font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;'
        f'letter-spacing:.06em;margin:10px 0 2px">{text}</p>',
        unsafe_allow_html=True,
    )


def _two_col_charts(pairs: list) -> None:
    """Render up to N (label, fig, height) tuples in pairs of two columns."""
    for i in range(0, len(pairs), 2):
        chunk = pairs[i:i + 2]
        cols = st.columns(len(chunk))
        for col, (label, fig, h) in zip(cols, chunk):
            with col:
                if label:
                    _label(label)
                if fig:
                    _chart(fig, height=h)


# ── Garmin: Wellness Trends ───────────────────────────────────────────────────

@register("get_garmin_wellness_trends")
def viz_wellness_trends(data: dict) -> None:
    metric_focus = data.pop("_metric_focus", "")
    trend = data.get("trend") or []
    if not trend:
        return
    df = pd.DataFrame(trend)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    show_all = not metric_focus

    if show_all or metric_focus == "sleep":
        fig_sleep = _sleep_stages_chart(df)
        if fig_sleep:
            _label("Sleep Stages & Score")
            _chart(fig_sleep, height=280)

    side_pairs = []
    if show_all or metric_focus == "body_battery":
        fig_bb = _body_battery_chart(df)
        if fig_bb:
            side_pairs.append(("Body Battery", fig_bb, 200))
    if show_all or metric_focus == "heart_rate":
        fig_hr = _hr_chart(df)
        if fig_hr:
            side_pairs.append(("Heart Rate", fig_hr, 200))
    if side_pairs:
        _two_col_charts(side_pairs)

    bottom_pairs = []
    if show_all or metric_focus == "steps":
        fig_steps = _steps_chart(df)
        if fig_steps:
            bottom_pairs.append(("Daily Steps", fig_steps, 180))
    if show_all or metric_focus == "stress":
        fig_stress = _stress_chart(df)
        if fig_stress:
            bottom_pairs.append(("Stress", fig_stress, 180))
    if bottom_pairs:
        _two_col_charts(bottom_pairs)


# ── Garmin: Single-night sleep ────────────────────────────────────────────────

@register("get_garmin_sleep")
def viz_sleep(data: dict) -> None:
    stage_keys = ("deep_h", "rem_h", "light_h", "awake_h", "total_sleep_h", "sleep_score")
    row = {k: data.get(k) for k in stage_keys}
    if not any(row.values()):
        return

    df = pd.DataFrame([{"date": data.get("date", ""), **row}])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    fig = _sleep_stages_chart(df)
    if fig:
        _label(f"Sleep — {data.get('date', '')}")
        _chart(fig, height=200)

    c1, c2, c3, c4 = st.columns(4)
    total = data.get("total_sleep_h") or 0
    c1.metric("Total Sleep",      f"{total:.1f} h" if total else "—")
    c2.metric("Sleep Score",      str(int(data["sleep_score"])) if data.get("sleep_score") else "—")
    c3.metric("Avg SpO₂",         f"{data['avg_spo2']:.0f}%" if data.get("avg_spo2") else "—")
    c4.metric("HRV overnight",    f"{data['hrv_overnight_avg']:.0f} ms" if data.get("hrv_overnight_avg") else "—")


# ── Garmin: Body Battery (multi-day) ─────────────────────────────────────────

@register("get_garmin_body_battery")
def viz_body_battery(data: dict) -> None:
    days = data.get("days") or []
    if not days:
        return
    df = pd.DataFrame([
        {"date": d["date"], "body_battery_high": d.get("highest"), "body_battery_low": d.get("lowest")}
        for d in days
    ])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    fig = _body_battery_chart(df)
    if fig:
        _label("Body Battery")
        _chart(fig, height=220)


# ── Garmin: HRV Status ────────────────────────────────────────────────────────

@register("get_garmin_hrv_status")
def viz_hrv_status(data: dict) -> None:
    fig = _hrv_gauge(data)
    if not fig:
        return
    c_gauge, c_text = st.columns([1, 2])
    with c_gauge:
        _label("HRV Last Night")
        st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
    with c_text:
        st.markdown("")  # vertical space
        if data.get("status"):
            st.markdown(f"**Status:** {data['status']}")
        lo, hi = data.get("baseline_balanced_low"), data.get("baseline_balanced_high")
        if lo and hi:
            st.caption(f"Balanced baseline: {lo}–{hi} ms")
        if data.get("feedback"):
            st.caption(data["feedback"])


# ── Garmin: Daily Health Summary ──────────────────────────────────────────────

@register("get_garmin_daily_health")
def viz_daily_health(data: dict) -> None:
    _label(f"Daily Health — {data.get('date', '')}")
    c1, c2, c3, c4 = st.columns(4)
    steps  = data.get("steps")
    rhr    = data.get("resting_hr")
    bb     = data.get("body_battery_now")
    stress = data.get("avg_stress")
    c1.metric("Steps",         f"{steps:,}" if steps else "—",
              delta="✓ goal" if steps and steps >= 10_000 else None)
    c2.metric("Resting HR",    f"{rhr} bpm" if rhr else "—")
    c3.metric("Body Battery",  str(bb) if bb is not None else "—")
    c4.metric("Avg Stress",    f"{stress:.0f}" if stress else "—")

    kcal      = data.get("active_calories") or data.get("total_calories")
    intensity = data.get("intensity_minutes")
    if kcal or intensity:
        c5, c6, _, _ = st.columns(4)
        c5.metric("Active kcal",   f"{kcal:,}" if kcal else "—")
        c6.metric("Intensity min", str(intensity) if intensity else "—")


# ── Garmin: Intraday Heart Rate ───────────────────────────────────────────────

@register("get_garmin_heart_rate_timeline")
def viz_hr_timeline(data: dict) -> None:
    timeline = data.get("timeline") or []
    if not timeline:
        return
    df = pd.DataFrame(timeline)
    _label(f"Heart Rate — {data.get('date', '')}")
    fig = go.Figure(go.Scatter(
        x=df["time"], y=df["hr"], mode="lines",
        line=dict(color=C_ROSE, width=1.2, shape="spline"),
        fill="tozeroy", fillcolor="rgba(251,113,133,0.12)",
        hovertemplate="<b>%{x}</b>  %{y:.0f} bpm<extra></extra>",
    ))
    rhr = data.get("resting_hr")
    if rhr:
        fig.add_hline(y=rhr, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
                      annotation_text=f"resting {rhr} bpm", annotation_font_color=TEXT_MUTED,
                      annotation_position="top right")
    fig.update_layout(yaxis=dict(ticksuffix=" bpm"))
    _chart(chart_style(fig), height=220)


# ── Garmin: Intraday Steps ────────────────────────────────────────────────────

@register("get_garmin_steps_timeline")
def viz_steps_timeline(data: dict) -> None:
    buckets = data.get("buckets_15min") or []
    if not buckets:
        return
    df = pd.DataFrame(buckets)
    _label(f"Steps Timeline — {data.get('date', '')}")
    fig = go.Figure(go.Bar(
        x=df["time"], y=df["steps"],
        marker_color=C_GREEN, marker_line_width=0,
        hovertemplate="<b>%{x}</b>  %{y:,} steps<extra></extra>",
    ))
    _chart(chart_style(fig), height=180)


# ── Garmin: Intraday Stress ───────────────────────────────────────────────────

@register("get_garmin_stress_timeline")
def viz_stress_timeline(data: dict) -> None:
    timeline = data.get("timeline") or []
    if not timeline:
        return
    df = pd.DataFrame(timeline)
    _label(f"Stress — {data.get('date', '')}")

    fig = go.Figure(go.Scatter(
        x=df["time"], y=df["stress"], mode="lines",
        line=dict(color=C_PURPLE, width=1.5, shape="spline"),
        fill="tozeroy", fillcolor="rgba(192,132,252,0.12)",
        hovertemplate="<b>%{x}</b>  %{y:.0f}<extra></extra>",
    ))
    fig.add_hline(y=25, line_dash="dot", line_color=TEXT_MUTED, line_width=0.8,
                  annotation_text="low", annotation_font_color=TEXT_MUTED,
                  annotation_position="top right")
    fig.add_hline(y=50, line_dash="dot", line_color=TEXT_MUTED, line_width=0.8,
                  annotation_text="medium", annotation_font_color=TEXT_MUTED,
                  annotation_position="top right")
    fig.add_hline(y=75, line_dash="dot", line_color=TEXT_MUTED, line_width=0.8,
                  annotation_text="high", annotation_font_color=TEXT_MUTED,
                  annotation_position="top right")
    fig.update_layout(yaxis=dict(range=[0, 105]))
    _chart(chart_style(fig), height=220)

    c1, c2, c3 = st.columns(3)
    c1.metric("Avg Stress",  str(data.get("avg_stress") or "—"))
    c2.metric("Peak Stress", str(data.get("max_stress") or "—"))
    c3.metric("Peak Time",   data.get("max_stress_time") or "—")


# ── Garmin: Body Composition ──────────────────────────────────────────────────

@register("get_garmin_body_composition")
def viz_body_composition(data: dict) -> None:
    measurements = data.get("measurements") or []
    if not measurements:
        st.caption(data.get("message") or "No body composition data found.")
        return

    _label(f"Weight — {data.get('start_date', '')} to {data.get('end_date', '')}")
    df = pd.DataFrame(measurements)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["weight_kg"], name="Weight (kg)",
        line=dict(color=ACCENT, width=2, shape="spline"),
        mode="lines+markers", marker=dict(size=4),
        hovertemplate="<b>%{x|%Y-%m-%d}</b>  %{y:.1f} kg<extra></extra>",
    ))
    if "body_fat_pct" in df.columns and df["body_fat_pct"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["body_fat_pct"], name="Body Fat (%)",
            line=dict(color=C_CYAN, width=1.5, shape="spline", dash="dot"),
            mode="lines", yaxis="y2",
            hovertemplate="<b>%{x|%Y-%m-%d}</b>  %{y:.1f}%<extra></extra>",
        ))
        fig.update_layout(
            yaxis2=dict(
                overlaying="y", side="right", ticksuffix="%",
                color=TEXT_MUTED, gridcolor="rgba(0,0,0,0)",
                tickfont=dict(size=10, color=TEXT_MUTED),
            ),
        )
    fig.update_layout(yaxis_title="kg")
    _chart(chart_style(fig), height=240)

    latest = data.get("latest") or {}
    if latest:
        c1, c2, c3, c4 = st.columns(4)
        w = latest.get("weight_kg")
        b = latest.get("bmi")
        f = latest.get("body_fat_pct")
        trend = data.get("trend_kg")
        c1.metric("Weight",    f"{w:.1f} kg" if w else "—")
        c2.metric("BMI",       f"{b:.1f}" if b else "—")
        c3.metric("Body Fat",  f"{f:.1f}%" if f else "—")
        c4.metric("Trend",     f"{trend:+.1f} kg" if trend is not None else "—")


# ── Garmin: Training Metrics ──────────────────────────────────────────────────

@register("get_garmin_training_metrics")
def viz_training_metrics(data: dict) -> None:
    _label("Training Status")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("VO₂max run",     data.get("vo2max_running") or "—")
    c2.metric("VO₂max cycle",   data.get("vo2max_cycling") or "—")
    c3.metric("Load 7 d",       data.get("training_load_7d") or "—")
    c4.metric("Readiness",      data.get("training_readiness_score") or "—")

    status = data.get("training_status")
    if status:
        st.caption(f"Training status: **{status}**")

    preds = data.get("race_predictions") or {}
    if preds:
        _label("Race Predictions")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("5 K",       preds.get("5k")            or "—")
        p2.metric("10 K",      preds.get("10k")           or "—")
        p3.metric("Half",      preds.get("half_marathon") or "—")
        p4.metric("Marathon",  preds.get("marathon")      or "—")


# ── Strava: Activities List ───────────────────────────────────────────────────

@register("get_activities")
def viz_activities(data: dict) -> None:
    acts = data.get("activities") or []
    if not acts:
        return
    _render_activity_list(acts, title=f"Activities — {len(acts)} retrieved")


@register("get_garmin_activities")
def viz_garmin_activities(data: dict) -> None:
    acts = data.get("activities") or []
    if not acts:
        return
    # Normalise field name difference between Strava and Garmin
    for a in acts:
        if "avg_hr" in a and "avg_heart_rate" not in a:
            a["avg_heart_rate"] = a["avg_hr"]
    _render_activity_list(acts, title=f"Activities — {len(acts)} retrieved")


def _render_activity_list(acts: list, title: str = "") -> None:
    df = pd.DataFrame(acts)
    if title:
        _label(title)

    # Bar chart: top 20 by distance — only meaningful when comparing multiple activities
    if "distance_km" in df.columns and len(df) >= 2:
        top = df.nlargest(min(len(df), 20), "distance_km").copy()
        sport_types = top.get("type", pd.Series([""] * len(top)))
        colors = [ACCENT if t in ("Run", "TrailRun", "VirtualRun") else C_CYAN for t in sport_types]
        icons   = [activity_icon(t) for t in sport_types]
        names   = (top["name"] if "name" in top.columns else top.index.astype(str)).tolist()

        # Append date to disambiguate activities that share the same name
        if "date" in top.columns:
            from collections import Counter
            name_counts = Counter(names)
            dates = top["date"].tolist()
            names = [
                f"{nm} ({str(dt)[:10]})" if name_counts[nm] > 1 else nm
                for nm, dt in zip(names, dates)
            ]

        labels  = [f"{ic} {n}" for ic, n in zip(icons, names)]

        hover_parts = ["<b>%{x}</b>", "Distance: %{y:.1f} km"]
        custom = None
        if "date" in top.columns:
            custom = list(zip(top["date"].tolist(),
                              top.get("avg_heart_rate", [None]*len(top)).tolist()))
            hover_parts += ["Date: %{customdata[0]}", "Avg HR: %{customdata[1]:.0f} bpm"]

        fig = go.Figure(go.Bar(
            x=labels, y=top["distance_km"],
            marker_color=colors, marker_line_width=0,
            customdata=custom,
            hovertemplate="<br>".join(hover_parts) + "<extra></extra>",
        ))
        fig.update_layout(yaxis_title="km", xaxis_tickangle=-30)
        _chart(chart_style(fig), height=260)

    # Summary table
    want = ["name", "type", "date", "distance_km", "pace_min_per_km", "avg_heart_rate", "elevation_gain_m"]
    cols = [c for c in want if c in df.columns]
    if cols:
        shown = df[cols].head(10).copy()
        shown.columns = [c.replace("_", " ").title() for c in shown.columns]
        st.dataframe(shown, width='stretch', hide_index=True)


# ── Strava: Activity Streams (GPS map + charts) ───────────────────────────────

@register("get_activity_streams")
def viz_activity_streams(data: dict) -> None:
    points = data.get("points") or []
    if len(points) < 2:
        return

    # Lazy import — folium / streamlit_folium are optional heavy deps
    try:
        from ui.activity_analysis import (
            _colored_route_map, _plain_route_map, _stream_charts, _legend_html,
        )
        from streamlit_folium import st_folium
    except ImportError:
        return

    has_hr  = any(p.get("hr")       for p in points)
    has_vel = any(p.get("velocity") for p in points)
    metric, invert, hi_lbl, lo_lbl = (
        ("hr",       False, "High HR", "Low HR")  if has_hr  else
        ("velocity", True,  "Fast",    "Slow")     if has_vel else
        ("ele",      False, "High",    "Low")
    )

    m = _colored_route_map(points, metric, invert=invert) or _plain_route_map(points)
    if m:
        _label("Route")
        map_col, leg_col = st.columns([9, 1])
        with map_col:
            st_folium(m, width='stretch', height=320, returned_objects=[])
        with leg_col:
            st.markdown(_legend_html(hi_lbl, lo_lbl), unsafe_allow_html=True)

    df = pd.DataFrame(points)
    if "dist_m" in df.columns:
        df["dist_km"] = df["dist_m"] / 1000
    _stream_charts(df)


# ── Strava: Activity Stats Summary ───────────────────────────────────────────

@register("get_activity_stats")
def viz_activity_stats(data: dict) -> None:
    _label("All-Time Stats")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Activities",     f"{data.get('total_activities', 0):,}")
    c2.metric("Total Distance", f"{data.get('total_distance_km', 0):,.0f} km")
    c3.metric("Total Time",     f"{data.get('total_time_hours', 0):,.0f} h")
    c4.metric("Total Elevation",f"{data.get('total_elevation_gain_m', 0):,.0f} m")

    breakdown = data.get("sport_breakdown") or {}
    if breakdown:
        rows = [{"Sport": k, **v} for k, v in breakdown.items()]
        df   = pd.DataFrame(rows)
        if "distance_km" in df.columns:
            fig = go.Figure(go.Bar(
                x=df["Sport"], y=df["distance_km"],
                marker_color=ACCENT, marker_line_width=0,
                hovertemplate="<b>%{x}</b>  %{y:.0f} km<extra></extra>",
            ))
            fig.update_layout(yaxis_title="km")
            _chart(chart_style(fig), height=200)


# ── Strava: Training Trends (weekly) ─────────────────────────────────────────

@register("get_training_trends")
def viz_training_trends(data: dict) -> None:
    weeks = data.get("weeks") or []
    if not weeks:
        return
    df = pd.DataFrame(weeks)
    _label("Weekly Training Volume")
    x_col = "week_start" if "week_start" in df.columns else df.index
    fig   = go.Figure(go.Bar(
        x=df[x_col] if isinstance(x_col, str) else x_col,
        y=df["distance_km"],
        marker_color=ACCENT, marker_line_width=0,
        hovertemplate="<b>%{x}</b>  %{y:.1f} km<extra></extra>",
    ))
    fig.update_layout(yaxis_title="km")
    _chart(chart_style(fig), height=220)


# ── Strava: Yearly Breakdown ──────────────────────────────────────────────────

@register("get_yearly_breakdown")
def viz_yearly_breakdown(data: dict) -> None:
    years = data.get("years") or []
    if not years:
        return
    df = pd.DataFrame(years)
    _label("Year-over-Year Distance")
    fig = go.Figure(go.Bar(
        x=df["year"].astype(str), y=df["total_distance_km"],
        marker_color=ACCENT, marker_line_width=0,
        hovertemplate="<b>%{x}</b>  %{y:.0f} km<extra></extra>",
    ))
    fig.update_layout(yaxis_title="km")
    _chart(chart_style(fig), height=220)


# ── Strava: Personal Bests ────────────────────────────────────────────────────

@register("get_personal_bests")
def viz_personal_bests(data: dict) -> None:
    top_dist = data.get("top_5_by_distance") or []
    top_fast = data.get("top_5_fastest")     or []
    if not top_dist and not top_fast:
        return
    _label("Personal Bests")
    if top_dist:
        st.markdown("**Longest activities**")
        df   = pd.DataFrame(top_dist)
        show = [c for c in ["name", "type", "date", "distance_km", "elevation_gain_m"] if c in df.columns]
        st.dataframe(df[show].head(5), width='stretch', hide_index=True)
    if top_fast:
        st.markdown("**Fastest activities**")
        df   = pd.DataFrame(top_fast)
        show = [c for c in ["name", "type", "date", "distance_km", "pace_min_per_km", "avg_heart_rate"] if c in df.columns]
        st.dataframe(df[show].head(5), width='stretch', hide_index=True)
