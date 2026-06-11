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


def render(tool_name: str, result_json: str, metric_focus: str = "",
           viz_hints: Optional[dict] = None) -> None:
    """Dispatch to the registered renderer. Silent no-op on any failure."""
    fn = _REGISTRY.get(tool_name)
    if not fn:
        return
    try:
        data = json.loads(result_json) if isinstance(result_json, str) else result_json
        if not data or (isinstance(data, dict) and data.get("error")):
            return
        if isinstance(data, dict):
            if metric_focus:
                data["_metric_focus"] = metric_focus
            if viz_hints:
                data["_viz_hints"] = viz_hints
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
    days = [d for d in (data.get("days") or []) if d.get("date") and d.get("highest") is not None]
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
    hints = data.get("_viz_hints") or {}
    metric = hints.get("metric")
    sport_types = {a.get("type") for a in acts if a.get("type")}
    if len(sport_types) == 1:
        title = f"{next(iter(sport_types))}s — {len(acts)} activities"
    else:
        title = f"Activities — {len(acts)} retrieved"
    _render_activity_list(acts, title=title, metric=metric)


@register("get_garmin_activities")
def viz_garmin_activities(data: dict) -> None:
    acts = data.get("activities") or []
    if not acts:
        return
    for a in acts:
        if "avg_hr" in a and "avg_heart_rate" not in a:
            a["avg_heart_rate"] = a["avg_hr"]
    hints = data.get("_viz_hints") or {}
    _render_activity_list(acts, title=f"Activities — {len(acts)} retrieved",
                          metric=hints.get("metric"))


# Metric configuration: (column, y-axis label, lower-is-better, bar-color-override)
_METRIC_CFG: dict = {
    "elevation_high_m":   ("elevation_high_m",  "m above sea level", False, C_INDIGO),
    "elevation_gain_m":   ("elevation_gain_m",   "m elevation gain",  False, C_GREEN),
    "pace_min_per_km":    ("pace_min_per_km",     "min/km",            True,  C_AMBER),
    "avg_heart_rate":     ("avg_heart_rate",      "bpm",               False, C_ROSE),
    "moving_time_hours":  ("moving_time_hours",   "hours",             False, C_CYAN),
    "suffer_score":       ("suffer_score",        "suffer score",      False, C_ROSE),
    "distance_km":        ("distance_km",         "km",                False, None),
    "kilojoules":         ("kilojoules",          "kJ",                False, C_PURPLE),
    "pr_count":           ("pr_count",            "PRs",               False, C_CYAN),
    "kudos":              ("kudos",               "kudos",             False, C_AMBER),
}


def _render_activity_list(acts: list, title: str = "",
                          metric: Optional[str] = None) -> None:
    df = pd.DataFrame(acts)
    if df.empty:
        return

    # ── Choose which metric to visualise ─────────────────────────────────────
    # If the model supplied a hint and the column exists, use it.
    # Fallback: distance_km (original default).
    cfg = _METRIC_CFG.get(metric) if metric else None
    col, ylab, lower_better, color_override = cfg if cfg else ("distance_km", "km", False, None)

    # Silently drop the hint if the column doesn't exist in this result set
    if col not in df.columns or df[col].dropna().empty:
        col, ylab, lower_better, color_override = ("distance_km", "km", False, None)

    focused = col != "distance_km"  # True when model requested a specific metric

    if title:
        _label(title)

    # ── Bar chart ─────────────────────────────────────────────────────────────
    if col in df.columns and len(df) >= 2:
        sort_df = df.dropna(subset=[col])
        if focused:
            # Model asked for a specific metric (e.g. pace, elevation) → sort by
            # that metric so the best/worst stand out clearly.
            top = sort_df.sort_values(col, ascending=lower_better).head(20).copy()
        else:
            # Default: chronological timeline — most useful for most questions.
            # "date" column is a datetime from to_df(); fall back to arrival order.
            if "date" in sort_df.columns:
                top = sort_df.sort_values("date", ascending=True).tail(20).copy()
            else:
                top = sort_df.tail(20).copy()

        sport_types = top.get("type", pd.Series([""] * len(top)))
        if color_override:
            colors = [color_override] * len(top)
        else:
            colors = [ACCENT if t in ("Run", "TrailRun", "VirtualRun") else C_CYAN
                      for t in sport_types]

        icons = [activity_icon(t) for t in sport_types]
        names = (top["name"] if "name" in top.columns else top.index.astype(str)).tolist()

        if "date" in top.columns:
            from collections import Counter
            cnt = Counter(names)
            dates = top["date"].tolist()
            names = [f"{n} ({str(d)[:10]})" if cnt[n] > 1 else n
                     for n, d in zip(names, dates)]

        labels = [f"{ic} {n}" for ic, n in zip(icons, names)]

        hover_parts = [f"<b>%{{x}}</b>", f"{ylab}: %{{y:.1f}}"]
        custom = None
        if "date" in top.columns:
            custom = list(zip(
                top["date"].tolist(),
                top.get("avg_heart_rate", pd.Series([None] * len(top))).tolist(),
            ))
            hover_parts += ["Date: %{customdata[0]}", "Avg HR: %{customdata[1]:.0f} bpm"]

        fig = go.Figure(go.Bar(
            x=labels, y=top[col],
            marker_color=colors, marker_line_width=0,
            customdata=custom,
            hovertemplate="<br>".join(hover_parts) + "<extra></extra>",
        ))
        fig.update_layout(yaxis_title=ylab, xaxis_tickangle=-30)
        _chart(chart_style(fig), height=260)

    # ── Summary table — skip when model focused on a specific metric ──────────
    if not focused:
        want = ["name", "type", "date", "distance_km", "pace_min_per_km",
                "avg_heart_rate", "elevation_gain_m"]
        cols_present = [c for c in want if c in df.columns]
        if cols_present:
            shown = df[cols_present].head(10).copy()
            shown.columns = [c.replace("_", " ").title() for c in shown.columns]
            st.dataframe(shown, width='stretch', hide_index=True)


# ── Strava: Activity Streams (GPS map with HR/elevation coloring + charts) ────

@register("get_activity_streams")
def viz_activity_streams(data: dict) -> None:
    points = data.get("points") or []
    if len(points) < 2:
        return

    try:
        from ui.activity_analysis import _colored_route_map, _plain_route_map
        from streamlit_folium import st_folium
    except ImportError:
        return

    has_hr  = data.get("has_hr")  or any(p.get("hr")  for p in points[:50])
    has_ele = any(p.get("ele") is not None for p in points[:50])

    if has_hr:
        m = _colored_route_map(points, "hr", invert=False)
        label_text = "Route — colored by Heart Rate  (green=low → red=high)"
    elif has_ele:
        m = _colored_route_map(points, "ele", invert=False)
        label_text = "Route — colored by Elevation  (green=low → red=high)"
    else:
        m = _plain_route_map(points)
        label_text = "Route"

    if not m:
        m = _plain_route_map(points)
        label_text = "Route"

    if m:
        _label(label_text)
        st_folium(m, width='stretch', height=340, returned_objects=[])

    # Elevation profile + HR timeline side by side when data is available
    if has_ele or has_hr:
        step = max(1, len(points) // 300)
        pts_dn = points[::step]

        # x-axis: use dist_m when available (Strava-measured), else compute from lat/lon
        if pts_dn[0].get("dist_m") is not None:
            xs = [p.get("dist_m", 0) / 1000 for p in pts_dn]
        else:
            import math
            dists, cum_d = [], 0.0
            for i, p in enumerate(pts_dn):
                if i > 0:
                    prev = pts_dn[i - 1]
                    dlat = (p.get("lat", 0) - prev.get("lat", 0)) * 111000
                    dlon = (p.get("lon", 0) - prev.get("lon", 0)) * 111000 * 0.85
                    cum_d += math.sqrt(dlat**2 + dlon**2) / 1000
                dists.append(round(cum_d, 3))
            xs = dists

        pairs = []

        if has_ele:
            eles = [p.get("ele") for p in pts_dn]
            fig_ele = go.Figure(go.Scatter(
                x=xs, y=eles, mode="lines", fill="tozeroy",
                line=dict(color=C_AMBER, width=1.5),
                fillcolor="rgba(245,158,11,0.15)",
                hovertemplate="<b>%{x:.2f} km</b>  %{y:.0f} m<extra></extra>",
            ))
            fig_ele.update_layout(yaxis_title="Elevation (m)", xaxis_title="Distance (km)")
            pairs.append(("Elevation Profile", fig_ele, 180))

        if has_hr:
            hrs = [p.get("hr") for p in pts_dn]
            fig_hr = go.Figure(go.Scatter(
                x=xs, y=hrs, mode="lines",
                line=dict(color=C_ROSE, width=1.5),
                fill="tozeroy", fillcolor="rgba(251,113,133,0.12)",
                hovertemplate="<b>%{x:.2f} km</b>  %{y:.0f} bpm<extra></extra>",
            ))
            fig_hr.update_layout(yaxis_title="Heart Rate (bpm)", xaxis_title="Distance (km)")
            pairs.append(("Heart Rate over Distance", fig_hr, 180))

        if pairs:
            _two_col_charts(pairs)


# ── Garmin: GPS Track ─────────────────────────────────────────────────────────

@register("get_activity_gps_track")
def viz_gps_track(data: dict) -> None:
    points = data.get("points") or []
    if len(points) < 2:
        return

    try:
        import folium
        from streamlit_folium import st_folium

        lats = [p["lat"] for p in points if p.get("lat") and p.get("lon")]
        lons = [p["lon"] for p in points if p.get("lat") and p.get("lon")]
        if not lats:
            return

        center = [sum(lats) / len(lats), sum(lons) / len(lons)]
        m = folium.Map(location=center, zoom_start=14,
                       tiles="CartoDB dark_matter")

        # Sample for performance
        step = max(1, len(points) // 500)
        coords = [[p["lat"], p["lon"]] for p in points[::step]
                  if p.get("lat") and p.get("lon")]

        has_ele = any(p.get("ele") is not None for p in points)
        if has_ele:
            eles = [p.get("ele") or 0 for p in points[::step]
                    if p.get("lat") and p.get("lon")]
            ele_min = min(eles); ele_max = max(eles) or (ele_min + 1)

            def _ele_color(ele):
                t = max(0.0, min(1.0, (ele - ele_min) / (ele_max - ele_min)))
                r = int(59 + t * (239 - 59))
                g = int(130 - t * 130)
                b = int(246 - t * (246 - 68))
                return f"#{r:02x}{g:02x}{b:02x}"

            for i in range(len(coords) - 1):
                folium.PolyLine(
                    [coords[i], coords[i + 1]],
                    color=_ele_color(eles[i]), weight=3, opacity=0.9,
                ).add_to(m)
        else:
            folium.PolyLine(coords, color="#3b82f6", weight=3, opacity=0.9).add_to(m)

        folium.Marker(coords[0],  icon=folium.Icon(color="green", icon="play")).add_to(m)
        folium.Marker(coords[-1], icon=folium.Icon(color="red",   icon="stop")).add_to(m)

        _label(f"GPS Track — {data.get('activity_id', '')}  ({data.get('total_points', len(points))} pts)")
        st_folium(m, width='stretch', height=360, returned_objects=[])

    except ImportError:
        pass


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
    top_elev = data.get("top_5_by_elevation") or []
    top_fast = data.get("top_5_fastest") or []
    if not (top_dist or top_elev or top_fast):
        return
    _label("Personal Bests")
    c1, c2 = st.columns(2)
    with c1:
        if top_dist:
            rows = [{"Activity": a.get("name","")[:25], "Date": (a.get("date",""))[:10],
                     "Distance (km)": a.get("distance_km", 0)} for a in top_dist]
            st.caption("Top 5 by Distance")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        if top_fast:
            paced = [a for a in top_fast if a.get("pace_min_per_km")]
            if paced:
                rows2 = [{"Activity": a.get("name","")[:25], "Date": (a.get("date",""))[:10],
                          "Pace (min/km)": a.get("pace_min_per_km", 0)} for a in paced]
                st.caption("Top 5 Fastest")
                st.dataframe(pd.DataFrame(rows2), hide_index=True, width='stretch')
    with c2:
        if top_elev:
            rows3 = [{"Activity": a.get("name","")[:25], "Date": (a.get("date",""))[:10],
                      "Elevation (m)": a.get("elevation_gain_m", 0)} for a in top_elev]
            st.caption("Top 5 by Elevation")
            st.dataframe(pd.DataFrame(rows3), hide_index=True, width='stretch')
        bw = data.get("biggest_week")
        streak = data.get("longest_streak_days")
        if bw or streak:
            if bw:
                st.metric("Best Week", f"{bw.get('distance_km', 0):.0f} km", help=bw.get("week",""))
            if streak:
                st.metric("Longest Streak", f"{streak} days")


# ── Strava: Performance Trends ────────────────────────────────────────────────

@register("analyze_performance_trends")
def viz_performance_trends(data: dict) -> None:
    series = data.get("series") or []
    if not series:
        return

    sport   = data.get("sport_type", "")
    trends  = data.get("trends") or {}
    avgs    = data.get("averages") or {}
    dates   = [s["date"] for s in series]
    paces   = [s.get("pace_min_per_km") for s in series]
    hrs     = [s.get("avg_hr") for s in series]

    has_pace = any(p for p in paces if p)
    has_hr   = any(h for h in hrs   if h)
    if not has_pace and not has_hr:
        return

    _trend_icon = {"improving": "📈", "declining": "📉", "stable": "➡️"}

    fig = go.Figure()

    if has_pace:
        fig.add_trace(go.Scatter(
            x=dates, y=paces, name="Pace (min/km)",
            mode="lines+markers", line=dict(color=ACCENT, width=2),
            marker=dict(size=5), connectgaps=True,
            hovertemplate="<b>%{x}</b>  %{y:.2f} min/km<extra></extra>",
        ))
        # linear trend line
        valid_idx = [i for i, p in enumerate(paces) if p]
        if len(valid_idx) >= 4:
            import numpy as _np
            xi = _np.array(valid_idx, dtype=float)
            yi = _np.array([paces[i] for i in valid_idx], dtype=float)
            z  = _np.polyfit(xi, yi, 1)
            trend_y = (_np.poly1d(z))(_np.arange(len(dates)))
            fig.add_trace(go.Scatter(
                x=dates, y=list(trend_y), name="Pace trend",
                mode="lines", line=dict(color=ACCENT, width=1, dash="dot"),
                showlegend=False, hoverinfo="skip",
            ))

    if has_hr:
        fig.add_trace(go.Scatter(
            x=dates, y=hrs, name="Avg HR (bpm)",
            mode="lines+markers", line=dict(color=C_ROSE, width=1.5),
            marker=dict(size=4), connectgaps=True, yaxis="y2",
            hovertemplate="<b>%{x}</b>  %{y:.0f} bpm<extra></extra>",
        ))

    pace_trend = _trend_icon.get(trends.get("pace", ""), "")
    hr_trend   = _trend_icon.get(trends.get("heart_rate", ""), "")
    title_parts = [f"{sport} — {len(series)} activities"]
    if pace_trend: title_parts.append(f"pace {pace_trend}")
    if hr_trend:   title_parts.append(f"HR {hr_trend}")

    layout = dict(
        title="  ".join(title_parts),
        legend=dict(orientation="h", y=1.12),
        xaxis=dict(showgrid=False),
    )
    if has_pace:
        layout["yaxis"]  = dict(title="min/km", autorange="reversed", ticksuffix=" min")
    if has_hr:
        layout["yaxis2"] = dict(title="bpm", overlaying="y", side="right", showgrid=False)

    fig.update_layout(**layout)
    _chart(chart_style(fig), height=300)

    # Summary metrics row
    avg_p = avgs.get("pace_min_per_km")
    avg_h = avgs.get("avg_hr_bpm")
    avg_d = avgs.get("distance_km")
    if any([avg_p, avg_h, avg_d]):
        c1, c2, c3 = st.columns(3)
        c1.metric("Avg Pace",  f"{avg_p:.2f} min/km" if avg_p else "—")
        c2.metric("Avg HR",    f"{avg_h:.0f} bpm"    if avg_h else "—")
        c3.metric("Avg Dist",  f"{avg_d:.1f} km"     if avg_d else "—")


# ── Strava: Training Load (ATL / CTL / TSB) ──────────────────────────────────

@register("get_training_load")
def viz_training_load(data: dict) -> None:
    weeks   = data.get("weeks") or []
    current = data.get("current") or {}
    if not weeks and not current:
        return

    # Current-state pills
    if current:
        atl  = current.get("atl",  0)
        ctl  = current.get("ctl",  0)
        tsb  = current.get("tsb",  0)
        form = current.get("form", "")
        tsb_color = C_GREEN if tsb >= 0 else C_ROSE
        _label("Current Training Load")
        c1, c2, c3 = st.columns(3)
        c1.metric("ATL — Fatigue",  f"{atl:.0f}")
        c2.metric("CTL — Fitness",  f"{ctl:.0f}")
        c3.metric("TSB — Form",     f"{tsb:+.0f}")
        if form:
            st.caption(f"Status: **{form}**")

    if not weeks:
        return

    # Trim to last 16 weeks (already sorted oldest → newest from the MCP tool)
    weeks = weeks[-16:]
    week_labels = [w["week_start"] for w in weeks]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=week_labels, y=[w["avg_atl"] for w in weeks],
        name="ATL (fatigue)", mode="lines+markers",
        line=dict(color=C_ROSE, width=2), marker=dict(size=4),
        hovertemplate="<b>%{x}</b>  ATL %{y:.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=week_labels, y=[w["avg_ctl"] for w in weeks],
        name="CTL (fitness)", mode="lines+markers",
        line=dict(color=C_INDIGO, width=2), marker=dict(size=4),
        hovertemplate="<b>%{x}</b>  CTL %{y:.0f}<extra></extra>",
    ))
    tsb_vals = [w["avg_tsb"] for w in weeks]
    fig.add_trace(go.Bar(
        x=week_labels, y=tsb_vals, name="TSB (form)", yaxis="y2",
        marker_color=[C_GREEN if t >= 0 else C_ROSE for t in tsb_vals],
        opacity=0.5,
        hovertemplate="<b>%{x}</b>  TSB %{y:+.0f}<extra></extra>",
    ))
    fig.update_layout(
        title="ATL / CTL / TSB — Weekly",
        barmode="overlay",
        legend=dict(orientation="h", y=1.12),
        xaxis=dict(showgrid=False),
        yaxis=dict(title="Load"),
        yaxis2=dict(overlaying="y", side="right", title="TSB", showgrid=False,
                    zeroline=True, zerolinecolor=TEXT_MUTED, zerolinewidth=1),
    )
    _chart(chart_style(fig), height=300)


# ── Garmin: Activity Detail (lap splits + HR zones) ─────────────────────────

@register("get_garmin_activity_detail")
def viz_activity_detail(data: dict) -> None:
    laps     = data.get("laps") or []
    hr_zones = data.get("hr_zones") or []
    name     = data.get("name", "Activity")
    date_str = data.get("date", "")

    if not laps and not hr_zones:
        return

    _label(f"{name} — {date_str}")

    if laps:
        lap_nums = [l.get("lap", i + 1) for i, l in enumerate(laps)]
        paces    = [l.get("pace_min_per_km") for l in laps]
        lap_hr   = [l.get("avg_hr") for l in laps]
        has_pace = any(p for p in paces if p and p < 20)
        has_hr   = any(h for h in lap_hr if h)
        xs       = [f"Lap {n}" for n in lap_nums]

        fig = go.Figure()
        if has_pace:
            valid_p = [p if p and p < 20 else None for p in paces]
            fig.add_trace(go.Bar(
                x=xs, y=valid_p, name="Pace (min/km)", marker_color=C_AMBER,
                hovertemplate="<b>%{x}</b>  %{y:.2f} min/km<extra></extra>",
            ))
            fig.update_layout(yaxis=dict(autorange="reversed", title="min/km  (lower = faster)"))
            if has_hr:
                fig.add_trace(go.Scatter(
                    x=xs, y=lap_hr, name="Avg HR", mode="lines+markers",
                    line=dict(color=C_ROSE, width=2), marker=dict(size=5),
                    yaxis="y2",
                    hovertemplate="<b>%{x}</b>  %{y:.0f} bpm<extra></extra>",
                ))
                fig.update_layout(
                    yaxis2=dict(title="bpm", overlaying="y", side="right",
                                showgrid=False, tickfont=dict(color=C_ROSE)),
                )
        else:
            dists = [l.get("distance_km", 0) for l in laps]
            fig.add_trace(go.Bar(
                x=xs, y=dists, name="Distance", marker_color=ACCENT,
                hovertemplate="<b>%{x}</b>  %{y:.2f} km<extra></extra>",
            ))
            fig.update_layout(yaxis=dict(title="km"))
        fig.update_layout(title="Lap Splits", legend=dict(orientation="h", y=1.12))
        _chart(chart_style(fig), height=240)

    if hr_zones:
        zones  = [f"Z{z.get('zone', i + 1)}" for i, z in enumerate(hr_zones)]
        times  = [z.get("time_min", 0) for z in hr_zones]
        z_cols = [C_INDIGO, C_GREEN, C_AMBER, ACCENT, C_ROSE][:len(zones)]
        fig2 = go.Figure(go.Bar(
            x=times, y=zones, orientation="h",
            marker_color=z_cols, marker_line_width=0,
            text=[f"{t:.0f} min" for t in times], textposition="outside",
            hovertemplate="<b>%{y}</b>  %{x:.0f} min<extra></extra>",
        ))
        fig2.update_layout(title="HR Zone Distribution", xaxis_title="Minutes")
        _chart(chart_style(fig2), height=200)


# ── Strava: Activity vs Baseline ─────────────────────────────────────────────

@register("compare_activity_to_baseline")
def viz_activity_comparison(data: dict) -> None:
    comparisons = data.get("comparisons") or {}
    activity    = data.get("activity") or {}
    if not comparisons:
        return

    assessment = data.get("assessment", "")
    overall    = data.get("overall_difficulty_percentile")
    _ASSESS_COLOR = {
        "one of your hardest":  C_ROSE,
        "harder than usual":    C_AMBER,
        "typical":              C_CYAN,
        "easier than usual":    C_GREEN,
        "one of your easiest":  C_GREEN,
    }
    assess_color = _ASSESS_COLOR.get(assessment, TEXT_MUTED)

    act_name = activity.get("name", "Activity")
    act_date = activity.get("date", "")
    _label(f"{act_name} — {act_date}")
    if assessment:
        st.markdown(
            f'<span style="background:{assess_color}22;color:{assess_color};'
            f'padding:3px 10px;border-radius:12px;font-size:0.9rem;font-weight:600">'
            f'{assessment.title()}'
            + (f" (pct {overall})" if overall is not None else "")
            + "</span>",
            unsafe_allow_html=True,
        )
        st.markdown("")

    # Horizontal bar chart showing difficulty percentile per metric
    _METRIC_LABELS = {
        "pace_min_per_km":  "Pace",
        "avg_hr_bpm":       "Heart Rate",
        "distance_km":      "Distance",
        "elevation_m":      "Elevation",
        "elevation_per_km": "Elev / km",
    }
    metrics = []
    pcts    = []
    for key, label in _METRIC_LABELS.items():
        v = comparisons.get(key)
        if v and v.get("difficulty_percentile") is not None:
            metrics.append(label)
            pcts.append(v["difficulty_percentile"])

    if not metrics:
        return

    bar_colors = [
        C_ROSE if p >= 85 else C_AMBER if p >= 65 else C_CYAN if p >= 35 else C_GREEN
        for p in pcts
    ]
    fig = go.Figure(go.Bar(
        x=pcts, y=metrics, orientation="h",
        marker_color=bar_colors, marker_line_width=0,
        text=[f"{p}th pct" for p in pcts], textposition="outside",
        hovertemplate="<b>%{y}</b>  difficulty percentile: %{x}<extra></extra>",
    ))
    fig.add_vline(x=50, line_dash="dot", line_color=TEXT_MUTED, line_width=1,
                  annotation_text="50th", annotation_font_color=TEXT_MUTED,
                  annotation_position="top")
    fig.update_layout(
        xaxis=dict(range=[0, 110], title="Difficulty percentile"),
        showlegend=False,
    )
    _chart(chart_style(fig), height=max(180, len(metrics) * 45))


# ── Weather: Forecast ─────────────────────────────────────────────────────────

@register("get_weather_forecast")
def viz_weather_forecast(data: dict) -> None:
    forecast = data.get("forecast") or []
    if not forecast:
        return
    df = pd.DataFrame(forecast)
    df["date"] = pd.to_datetime(df["date"])
    _label(f"Weather Forecast — {data.get('location', '')}")
    c1, c2 = st.columns(2)
    with c1:
        if "temp_max_c" in df.columns:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df["date"], y=df["temp_max_c"], name="Max",
                line=dict(color=C_AMBER, width=2), mode="lines+markers",
            ))
            fig.add_trace(go.Scatter(
                x=df["date"], y=df["temp_min_c"], name="Min",
                line=dict(color=ACCENT, width=2, dash="dash"), mode="lines+markers",
                fill="tonexty", fillcolor="rgba(252,76,2,0.1)",
            ))
            fig.update_layout(yaxis_title="°C", legend=dict(orientation="h", y=1.1))
            _chart(chart_style(fig), height=200)
    with c2:
        if "precip_probability_pct" in df.columns:
            rain_colors = [
                "rgb(59,130,246)" if p < 30 else ("rgb(245,158,11)" if p < 60 else "rgb(239,68,68)")
                for p in df["precip_probability_pct"]
            ]
            fig2 = go.Figure(go.Bar(
                x=df["date"], y=df["precip_probability_pct"],
                marker_color=rain_colors, marker_line_width=0,
                hovertemplate="<b>%{x|%a %d}</b>  %{y}%<extra></extra>",
            ))
            fig2.update_layout(yaxis=dict(title="%", range=[0, 100]))
            _chart(chart_style(fig2), height=200)


# ── Strava: Gear Mileage ─────────────────────────────────────────────────────

@register("get_gear_info")
def viz_gear_info(data: dict) -> None:
    shoes = data.get("shoes") or []
    bikes = data.get("bikes") or []
    all_items = [("shoe", s) for s in shoes] + [("bike", b) for b in bikes]
    if not all_items:
        return
    _label("Gear Mileage")
    rows = []
    for kind, item in all_items:
        brand = item.get("brand") or item.get("model") or ""
        rows.append({
            "Type": "👟 Shoe" if kind == "shoe" else "🚴 Bike",
            "Name": item.get("name", "Unknown"),
            "Brand": brand,
            "Distance (km)": item.get("distance_km", 0),
            "Primary": "★" if item.get("primary") else "",
        })
    rows.sort(key=lambda r: r["Distance (km)"], reverse=True)
    df = pd.DataFrame(rows)
    fig = go.Figure(go.Bar(
        x=df["Distance (km)"],
        y=df["Name"],
        orientation="h",
        marker_color=[C_AMBER if "Shoe" in t else ACCENT for t in df["Type"]],
        marker_line_width=0,
        text=[f"{d:,.0f} km {p}" for d, p in zip(df["Distance (km)"], df["Primary"])],
        textposition="outside",
        hovertemplate="<b>%{y}</b>  %{x:.0f} km<extra></extra>",
    ))
    fig.update_layout(xaxis_title="km")
    _chart(chart_style(fig), height=max(200, len(all_items) * 50))


# ── Strava: Activity Detail (laps + splits) ──────────────────────────────────

@register("get_activity_detail")
def viz_activity_detail_strava(data: dict) -> None:
    laps   = data.get("laps") or []
    splits = data.get("splits_per_km") or []
    name   = data.get("name", "Activity")
    date_s = data.get("date", "")

    if not laps and not splits:
        return

    _label(f"{name} — {date_s}")

    if splits and len(splits) >= 2:
        kms   = [s.get("km") for s in splits]
        paces = [s.get("pace_min_per_km") for s in splits]
        hrs   = [s.get("avg_hr") for s in splits]
        valid_p = [p if p and p < 20 else None for p in paces]
        has_pace = any(p for p in valid_p if p)
        has_hr   = any(h for h in hrs if h)

        fig = go.Figure()
        if has_pace:
            fig.add_trace(go.Bar(
                x=[f"km {k}" for k in kms], y=valid_p, name="Pace (min/km)",
                marker_color=C_AMBER,
                hovertemplate="<b>%{x}</b>  %{y:.2f} min/km<extra></extra>",
            ))
            fig.update_layout(yaxis=dict(autorange="reversed", title="min/km  (lower=faster)"))
        if has_hr:
            fig.add_trace(go.Scatter(
                x=[f"km {k}" for k in kms], y=hrs, name="Avg HR",
                mode="lines+markers", line=dict(color=C_ROSE, width=2),
                yaxis="y2",
                hovertemplate="<b>%{x}</b>  %{y:.0f} bpm<extra></extra>",
            ))
            fig.update_layout(
                yaxis2=dict(title="bpm", overlaying="y", side="right",
                            showgrid=False, tickfont=dict(color=C_ROSE)),
            )
        fig.update_layout(title="km Splits", legend=dict(orientation="h", y=1.12))
        _chart(chart_style(fig), height=240)

    elif laps and len(laps) >= 2:
        xs = [f"Lap {l.get('lap', i+1)}" for i, l in enumerate(laps)]
        paces = [l.get("pace_min_per_km") for l in laps]
        hrs   = [l.get("avg_hr") for l in laps]
        valid_p = [p if p and p < 20 else None for p in paces]
        fig = go.Figure()
        if any(p for p in valid_p if p):
            fig.add_trace(go.Bar(
                x=xs, y=valid_p, name="Pace (min/km)", marker_color=C_AMBER,
                hovertemplate="<b>%{x}</b>  %{y:.2f} min/km<extra></extra>",
            ))
            fig.update_layout(yaxis=dict(autorange="reversed", title="min/km"))
        if any(h for h in hrs if h):
            fig.add_trace(go.Scatter(
                x=xs, y=hrs, name="Avg HR", mode="lines+markers",
                line=dict(color=C_ROSE, width=2), yaxis="y2",
                hovertemplate="<b>%{x}</b>  %{y:.0f} bpm<extra></extra>",
            ))
            fig.update_layout(
                yaxis2=dict(title="bpm", overlaying="y", side="right",
                            showgrid=False, tickfont=dict(color=C_ROSE)),
            )
        fig.update_layout(title="Lap Splits", legend=dict(orientation="h", y=1.12))
        _chart(chart_style(fig), height=240)


# ── Strava: Athlete Profile ───────────────────────────────────────────────────

@register("get_athlete_profile")
def viz_athlete_profile(data: dict) -> None:
    profile = data.get("profile") or {}
    stats   = data.get("official_stats") or {}
    if not profile:
        return

    _label(f"Athlete: {profile.get('name', '')}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Weight",   f"{profile['weight_kg']:.1f} kg" if profile.get("weight_kg") else "—")
    c2.metric("FTP",      f"{profile['ftp']} W" if profile.get("ftp") else "—")
    c3.metric("Follower", str(profile.get("follower_count", "—")))
    c4.metric("Member since", profile.get("member_since", "—")[:4] if profile.get("member_since") else "—")

    all_time = (stats.get("all_time") or {})
    ytd      = (stats.get("year_to_date") or {})
    labels   = ["Run", "Ride", "Swim"]
    keys     = ["run", "ride", "swim"]

    at_rows = [{"Sport": k.title(),
                "All-Time km": round((all_time.get(k) or {}).get("distance_km", 0), 0),
                "YTD km":      round((ytd.get(k)      or {}).get("distance_km", 0), 0),
                "All-Time h":  round((all_time.get(k) or {}).get("moving_time_hours", 0), 1),
               } for k in keys]
    at_df = pd.DataFrame(at_rows)
    at_df = at_df[at_df["All-Time km"] > 0]
    if not at_df.empty:
        _label("Official Strava Totals")
        st.dataframe(at_df, hide_index=True, width='stretch')
