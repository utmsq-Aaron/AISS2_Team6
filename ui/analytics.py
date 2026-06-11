"""Analytics tab — Training load, performance trends, activity comparison.

Visualises the three Strava analysis tools as interactive UI components:
  • get_training_load           → ATL/CTL/TSB model
  • analyze_performance_trends  → pace/HR time series
  • compare_activity_to_baseline → activity vs. personal baseline
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import streamlit as st

from ui.shared import call_tool, wait_for_servers

_STRAVA_ORANGE = "#FC4C02"
_CHART_TEMPLATE = "plotly_dark"
_CHART_MARGIN   = dict(l=0, r=10, t=30, b=0)
_CHART_BG       = "rgba(0,0,0,0)"


def _show_tool_error(error: str, tool: str) -> None:
    if "Unknown tool" in error or "unknown tool" in error:
        st.warning(
            f"**Tool `{tool}` not found** — the Strava MCP server is running an older version.  \n"
            "Go to ⚙️ **Settings → Developer → Restart MCP Servers** and try again."
        )
    else:
        st.error(error)


# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _load_training_load(weeks: int, _v: int = 0) -> Dict:
    try:
        return json.loads(call_tool("strava__get_training_load", {"weeks": weeks}))
    except Exception as e:
        return {"error": str(e)}


@st.cache_data(show_spinner=False)
def _load_trends(sport_type: str, limit: int, _v: int = 0) -> Dict:
    try:
        return json.loads(call_tool("strava__analyze_performance_trends",
                                    {"sport_type": sport_type, "limit": limit}))
    except Exception as e:
        return {"error": str(e)}


@st.cache_data(show_spinner=False)
def _load_all_activities() -> List[Any]:
    """Load up to 500 recent activities for client-side search."""
    try:
        raw = json.loads(call_tool("strava__get_activities", {"limit": 500}))
    except Exception:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and not raw.get("error"):
        return raw.get("activities", [])
    return []


def _load_comparison_by_id(activity_id: int, baseline_count: int) -> Dict:
    try:
        return json.loads(call_tool("strava__compare_activity_to_baseline",
                                    {"activity_id": activity_id,
                                     "baseline_count": baseline_count}))
    except Exception as e:
        return {"error": str(e)}


# ── Small helpers ─────────────────────────────────────────────────────────────

def _trend_pill(direction: str) -> str:
    _MAP = {
        "improving":         ("#22c55e", "📈 Improving"),
        "declining":         ("#ef4444", "📉 Declining"),
        "stable":            ("#94a3b8", "➡️ Stable"),
        "insufficient data": ("#64748b", "❓ Insufficient data"),
    }
    color, label = _MAP.get(direction, ("#64748b", direction))
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}55;'
        f'border-radius:12px;padding:2px 10px;font-size:0.78rem;white-space:nowrap">'
        f'{label}</span>'
    )


def _pct_bar(pct: int, value: str, mean: str, unit: str, label: str) -> str:
    clamp = min(max(pct, 0), 100)
    if clamp >= 75:   bar_col = "#ef4444"
    elif clamp >= 50: bar_col = "#f97316"
    elif clamp <= 25: bar_col = "#22c55e"
    else:             bar_col = "#94a3b8"
    return (
        f'<div style="margin:5px 0 8px">'
        f'  <div style="display:flex;justify-content:space-between;font-size:0.82rem;color:#ccc">'
        f'    <span>{label}</span>'
        f'    <span><strong>{value}</strong> {unit} &nbsp;·&nbsp; avg&nbsp;{mean} {unit}</span>'
        f'  </div>'
        f'  <div style="background:#1e293b;border-radius:4px;height:7px;margin-top:3px">'
        f'    <div style="background:{bar_col};width:{clamp}%;height:7px;border-radius:4px"></div>'
        f'  </div>'
        f'  <div style="font-size:0.72rem;color:#64748b">harder than {pct}% of baseline</div>'
        f'</div>'
    )


# ── Section renderers ─────────────────────────────────────────────────────────

def _render_training_load(v: int) -> None:
    st.subheader("🏋️ Training Load")
    st.caption("Tracks your training stress over time using the classic ATL/CTL/TSB model from exercise science.")

    with st.expander("💡 How to read ATL · CTL · TSB"):
        st.markdown(
            "**ATL — Acute Training Load** *(7-day window)*  \n"
            "How hard you have trained this week. A high ATL means your body is currently under stress — "
            "you will feel tired, but you are also becoming fitter.\n\n"
            "**CTL — Chronic Training Load** *(42-day window)*  \n"
            "Your fitness base, built up over the last six weeks. Think of it as the \"engine size\" you have "
            "developed through consistent training. CTL rises slowly and is hard to fake.\n\n"
            "**TSB — Training Stress Balance** *(CTL − ATL)*  \n"
            "The gap between your fitness and your current fatigue.\n"
            "- **Positive TSB** → you are rested and race-ready (fitness > fatigue)\n"
            "- **Near zero** → balanced; good for steady training blocks\n"
            "- **Negative TSB** → you are accumulating productive fatigue (normal mid-block)\n"
            "- **Very negative (< −30)** → risk of overtraining; consider an easy day\n\n"
            "**Weekly Load bar chart** — each bar is the total training impulse for that week. "
            "Larger bars with rising CTL over time = you are building fitness."
        )

    weeks = st.select_slider(
        "Time range (weeks)",
        options=[4, 8, 12, 16, 24, 32, 52],
        value=16,
        key="load_weeks",
    )

    with st.spinner("Loading training data…"):
        data = _load_training_load(weeks, _v=v)

    if "error" in data:
        _show_tool_error(data["error"], "get_training_load"); return

    cur = data.get("current", {})
    atl, ctl, tsb = cur.get("atl", 0), cur.get("ctl", 0), cur.get("tsb", 0)
    form = cur.get("form", "")

    c1, c2, c3 = st.columns(3)
    c1.metric("ATL (7d)",  f"{atl:.0f}", help="Acute Training Load — short-term fatigue")
    c2.metric("CTL (42d)", f"{ctl:.0f}", help="Chronic Training Load — fitness base")
    c3.metric("TSB",       f"{tsb:+.0f}",
              delta=f"{tsb:+.0f}",
              delta_color="normal" if tsb >= 0 else "inverse",
              help="Training Stress Balance = CTL − ATL. Positive = rested, negative = training stress.")
    st.info(f"**Current form:** {form}")

    weeks_rows = data.get("weeks", [])
    if not weeks_rows:
        return

    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    df = pd.DataFrame(weeks_rows[-min(len(weeks_rows), 20):])

    tab_bar, tab_atl = st.tabs(["📊 Weekly Load", "📈 ATL / CTL Trend"])

    with tab_bar:
        fig = go.Figure(go.Bar(
            x=df["week_start"], y=df["total_load"],
            marker_color=_STRAVA_ORANGE, name="Training load",
        ))
        fig.update_layout(template=_CHART_TEMPLATE, margin=_CHART_MARGIN,
                          paper_bgcolor=_CHART_BG, plot_bgcolor=_CHART_BG,
                          height=280, xaxis_title="Week", yaxis_title="Load (a.u.)")
        st.plotly_chart(fig, width='stretch')

    with tab_atl:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=df["week_start"], y=df["avg_atl"],
                                  name="ATL (7d)", line=dict(color="#ef4444", width=2)))
        fig2.add_trace(go.Scatter(x=df["week_start"], y=df["avg_ctl"],
                                  name="CTL (42d)", line=dict(color="#3b82f6", width=2)))
        fig2.add_trace(go.Bar(x=df["week_start"], y=df["avg_tsb"],
                              name="TSB", marker_color=[
                                  "#22c55e" if v >= 0 else "#f97316"
                                  for v in df["avg_tsb"]
                              ], opacity=0.5))
        fig2.update_layout(template=_CHART_TEMPLATE, margin=_CHART_MARGIN,
                           paper_bgcolor=_CHART_BG, plot_bgcolor=_CHART_BG,
                           height=300, barmode="overlay",
                           legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig2, width='stretch')


def _render_trends(v: int) -> None:
    st.subheader("📈 Performance Trend")
    st.caption(
        "Shows how your key metrics have evolved across your last N activities. "
        "Use this to spot genuine improvement — or catch early signs of overtraining."
    )

    with st.expander("💡 How to read the charts"):
        st.markdown(
            "**Pace** — lower numbers (min/km) mean *faster*. The Y-axis is inverted so "
            "improvement always looks like a line going *up*. A dotted line marks your average. "
            "The trend badge tells you whether a linear fit through your data is going up or down.\n\n"
            "**Heart Rate** — a *declining* average HR at the same effort is a classic sign of "
            "improving aerobic efficiency. Rising HR can indicate fatigue or heat stress.\n\n"
            "**Distance** — how far each activity was. Useful to check whether your pace trend "
            "is driven by effort or by running shorter / longer distances.\n\n"
            "**Elevation/km** — vertical gain per kilometre. A pace trend that coincides with "
            "rising elevation/km is likely terrain-driven, not fitness-driven.\n\n"
            "🟠 **Improving** · ⚪ **Stable** · 🔴 **Declining** — linear regression over the selected window."
        )

    col_sport, col_limit = st.columns([2, 1])
    sport = col_sport.selectbox(
        "Sport type",
        ["Run", "Ride", "Hike", "Walk", "TrailRun", "MountainBikeRide",
         "Swim", "WeightTraining"],
        key="trend_sport",
    )
    limit = col_limit.slider("Activities", 10, 100, 30, key="trend_limit")

    with st.spinner(f"Analysing {sport} activities…"):
        data = _load_trends(sport, limit, _v=v)

    if "error" in data:
        _show_tool_error(data["error"], "analyze_performance_trends"); return
    if not data.get("series"):
        st.info(f"No {sport} activities found."); return

    series  = data["series"]
    trends  = data.get("trends", {})
    avgs    = data.get("averages", {})
    hi      = data.get("highlights", {})
    dr      = data.get("date_range", {})
    n       = data.get("activity_count", 0)

    # Trend badges
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"Pace: {_trend_pill(trends.get('pace', 'insufficient data'))}", unsafe_allow_html=True)
    c2.markdown(f"HR: {_trend_pill(trends.get('heart_rate', 'insufficient data'))}", unsafe_allow_html=True)
    c3.caption(f"{n} activities · {dr.get('from', '')} – {dr.get('to', '')}")

    import pandas as pd
    import plotly.graph_objects as go

    df = pd.DataFrame(series)
    df["date"] = pd.to_datetime(df["date"])

    tab_pace, tab_hr, tab_dist, tab_elev = st.tabs(
        ["⏱️ Pace", "❤️ Heart Rate", "📏 Distance", "⛰️ Elevation/km"]
    )

    with tab_pace:
        pace_df = df[df["pace_min_per_km"].notna()]
        if pace_df.empty:
            st.info("No pace data available."); return
        avg_p = avgs.get("pace_min_per_km")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=pace_df["date"], y=pace_df["pace_min_per_km"],
            mode="lines+markers", name="Pace",
            line=dict(color=_STRAVA_ORANGE, width=2),
            marker=dict(size=5),
            text=pace_df["name"], hovertemplate="%{text}<br>%{y:.2f} min/km<extra></extra>",
        ))
        if avg_p:
            fig.add_hline(y=avg_p, line_dash="dot", line_color="#94a3b8",
                          annotation_text=f"avg {avg_p:.2f}")
        fig.update_yaxes(autorange="reversed")  # lower pace = faster = top
        fig.update_layout(template=_CHART_TEMPLATE, margin=_CHART_MARGIN,
                          paper_bgcolor=_CHART_BG, plot_bgcolor=_CHART_BG,
                          height=300, yaxis_title="min/km")
        st.plotly_chart(fig, width='stretch')
        st.caption("⬆ Y-axis inverted — higher = faster")
        b, w = hi.get("fastest"), hi.get("slowest")
        if b or w:
            bc, wc = st.columns(2)
            if b: bc.success(f"🏆 Fastest: **{b['pace']}** — {b['name']} ({b['date']})")
            if w: wc.warning(f"🐢 Slowest: **{w['pace']}** — {w['name']} ({w['date']})")

    with tab_hr:
        hr_df = df[df["avg_hr"].notna()]
        if hr_df.empty:
            st.info("No HR data (no heart rate monitor used?)"); return
        avg_hr = avgs.get("avg_hr_bpm")
        fig = go.Figure(go.Scatter(
            x=hr_df["date"], y=hr_df["avg_hr"],
            mode="lines+markers", name="Ø HR",
            line=dict(color="#ef4444", width=2), marker=dict(size=5),
            text=hr_df["name"], hovertemplate="%{text}<br>%{y:.0f} bpm<extra></extra>",
        ))
        if avg_hr:
            fig.add_hline(y=avg_hr, line_dash="dot", line_color="#94a3b8",
                          annotation_text=f"avg {avg_hr:.0f} bpm")
        fig.update_layout(template=_CHART_TEMPLATE, margin=_CHART_MARGIN,
                          paper_bgcolor=_CHART_BG, plot_bgcolor=_CHART_BG,
                          height=300, yaxis_title="bpm")
        st.plotly_chart(fig, width='stretch')

    with tab_dist:
        dist_df = df[df["distance_km"].notna()]
        avg_d = avgs.get("distance_km")
        fig = go.Figure(go.Bar(
            x=dist_df["date"], y=dist_df["distance_km"],
            marker_color="#3b82f6", name="Distance",
            text=dist_df["name"], hovertemplate="%{text}<br>%{y:.1f} km<extra></extra>",
        ))
        if avg_d:
            fig.add_hline(y=avg_d, line_dash="dot", line_color="#94a3b8",
                          annotation_text=f"avg {avg_d:.1f} km")
        fig.update_layout(template=_CHART_TEMPLATE, margin=_CHART_MARGIN,
                          paper_bgcolor=_CHART_BG, plot_bgcolor=_CHART_BG,
                          height=300, yaxis_title="km")
        st.plotly_chart(fig, width='stretch')

    with tab_elev:
        elev_df = df[df["elevation_per_km"].notna()]
        if elev_df.empty:
            st.info("No elevation data available."); return
        avg_e = avgs.get("elevation_per_km")
        fig = go.Figure(go.Bar(
            x=elev_df["date"], y=elev_df["elevation_per_km"],
            marker_color="#22c55e", name="m/km",
            text=elev_df["name"], hovertemplate="%{text}<br>%{y:.1f} m/km<extra></extra>",
        ))
        if avg_e:
            fig.add_hline(y=avg_e, line_dash="dot", line_color="#94a3b8",
                          annotation_text=f"avg {avg_e:.1f} m/km")
        fig.update_layout(template=_CHART_TEMPLATE, margin=_CHART_MARGIN,
                          paper_bgcolor=_CHART_BG, plot_bgcolor=_CHART_BG,
                          height=300, yaxis_title="m/km")
        st.plotly_chart(fig, width='stretch')


def _act_label(a: Dict) -> str:
    date = (a.get("date") or a.get("start_date", ""))[:10]
    dist = a.get("distance_km") or round((a.get("distance") or 0) / 1000, 1)
    return f"{a.get('name', '?')}  ·  {date}  ·  {dist} km"


def _render_comparison() -> None:
    st.subheader("🔍 Activity vs. Personal Baseline")
    st.caption(
        "Pick any activity and compare it against your recent history of the same sport. "
        "Was today's run actually hard — or just felt that way?"
    )

    with st.expander("💡 How to use this"):
        st.markdown(
            "**Search** for an activity by name (partial match works — try 'run', 'trail', 'wandern').  \n"
            "If multiple results come up, pick one from the drop-down.  \n"
            "Then hit **Compare** to see how it stacks up against your recent baseline.\n\n"
            "**Baseline size** — how many of your most recent same-sport activities are used as "
            "the reference. 30 is a good default; raise it for a longer-term comparison.\n\n"
            "**Percentile bars** — each metric (pace, HR, elevation…) is ranked against the "
            "baseline. A bar at 80% means this activity was harder than 80% of those baseline runs.  \n"
            "- 🟢 Green (0–25 %) → easier than usual  \n"
            "- ⚪ Grey (25–50 %) → typical effort  \n"
            "- 🟠 Orange (50–75 %) → harder than usual  \n"
            "- 🔴 Red (75–100 %) → one of your hardest\n\n"
            "**Overall assessment** is derived from the combined difficulty percentile across all available metrics."
        )

    col_in, col_base = st.columns([3, 1])
    act_name = col_in.text_input(
        "Search activity",
        placeholder="e.g. 'wandern', 'morning run', 'trail'",
        key="cmp_name",
    )
    baseline_n = col_base.number_input(
        "Baseline size", min_value=5, max_value=100, value=30, key="cmp_base",
        help="Number of recent same-sport activities used as the reference baseline",
    )

    selected_id: Optional[int] = None

    if act_name.strip():
        keyword = act_name.strip().lower()
        all_acts = _load_all_activities()
        matches = [a for a in all_acts if keyword in (a.get("name") or "").lower()]

        if not matches:
            st.caption("No activities found matching this search.")
            return

        if len(matches) == 1:
            selected_id = int(matches[0]["id"])
            st.caption(f"Found: {_act_label(matches[0])}")
        else:
            labels = [_act_label(a) for a in matches]
            idx = st.selectbox(
                f"{len(matches)} matching activities — select one:",
                range(len(matches)),
                format_func=lambda i: labels[i],
                key="cmp_select",
            )
            selected_id = int(matches[idx]["id"])

        # Clear stale result when selected activity changes
        if st.session_state.get("_cmp_id") != selected_id:
            st.session_state.pop("_cmp_result", None)

        if st.button("Compare", type="primary", key="cmp_btn"):
            with st.spinner("Comparing…"):
                st.session_state["_cmp_result"] = _load_comparison_by_id(
                    selected_id, int(baseline_n)
                )
                st.session_state["_cmp_id"] = selected_id
    else:
        st.caption("Enter an activity name to search, then click **Compare**.")
        return

    result: Optional[Dict] = st.session_state.get("_cmp_result")
    if result is None:
        return

    if "error" in result:
        _show_tool_error(result["error"], "compare_activity_to_baseline"); return

    act         = result.get("activity", {})
    assessment  = result.get("assessment", "")
    overall_pct = result.get("overall_difficulty_percentile")
    comparisons = result.get("comparisons", {})
    n_base      = result.get("baseline_activity_count", "?")

    # Activity header
    parts = [f"**{act.get('name', '')}**", act.get("date", ""),
             f"{act.get('distance_km', 0):.1f} km",
             f"{act.get('elevation_m', 0):.0f} m elevation"]
    if act.get("pace_display"):  parts.append(f"{act['pace_display']} /km")
    if act.get("avg_hr"):        parts.append(f"❤️ {act['avg_hr']:.0f} bpm")
    st.markdown(" · ".join(parts))

    # Overall assessment banner
    _ICONS   = {"one of your hardest": "🔥", "harder than usual": "💪",
                "typical": "👌", "easier than usual": "😌", "one of your easiest": "🛋️"}
    _COLORS  = {"one of your hardest": "#ef4444", "harder than usual": "#f97316",
                "typical": "#94a3b8", "easier than usual": "#22c55e", "one of your easiest": "#16a34a"}
    _LABELS  = {"one of your hardest": "One of your hardest",
                "harder than usual": "Harder than usual",
                "typical": "Typical effort",
                "easier than usual": "Easier than usual",
                "one of your easiest": "One of your easiest"}
    icon   = _ICONS.get(assessment, "📊")
    color  = _COLORS.get(assessment, "#94a3b8")
    label  = _LABELS.get(assessment, assessment)
    sport  = act.get("sport_type", "")

    if overall_pct is not None:
        st.markdown(
            f'<div style="background:{color}18;border-left:3px solid {color};'
            f'border-radius:0 6px 6px 0;padding:10px 16px;margin:10px 0">'
            f'<span style="font-size:1.5rem">{icon}</span>&nbsp;'
            f'<strong style="color:{color};font-size:1.05rem">{label}</strong>'
            f'<span style="color:#94a3b8;font-size:0.85rem"> — harder than '
            f'<strong style="color:#e2e8f0">{overall_pct}%</strong> of your last '
            f'{n_base} {sport} activities</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Per-metric bars
    if comparisons:
        _METRIC_LABELS = {
            "distance_km":      ("📏 Distance",        "km"),
            "elevation_m":      ("⛰️ Elevation",        "m"),
            "elevation_per_km": ("📐 Elevation/km",     "m/km"),
            "pace_min_per_km":  ("⏱️ Pace",             "min/km"),
            "avg_hr_bpm":       ("❤️ Heart rate",       "bpm"),
        }
        bars_html = ""
        for key, cdata in comparisons.items():
            if not cdata:
                continue
            label_m, unit = _METRIC_LABELS.get(key, (key, ""))
            pct   = cdata.get("difficulty_percentile", 0)
            tgt   = cdata.get("target", 0)
            mean  = cdata.get("baseline_mean", 0)
            bars_html += _pct_bar(pct, f"{tgt:.1f}", f"{mean:.1f}", unit, label_m)
        if bars_html:
            st.caption("Difficulty percentile per metric (how many of your baseline activities were easier):")
            st.markdown(bars_html, unsafe_allow_html=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def render_analytics() -> None:
    if not wait_for_servers("strava"):
        return

    st.markdown(
        "Dig deeper into your training data. "
        "**Training Load** quantifies fitness and fatigue. "
        "**Performance Trend** shows whether you are getting faster or stronger over time. "
        "**Activity Comparison** puts any single workout in context against your personal history."
    )

    _v = st.session_state.get("_refresh_v", 0)

    _render_training_load(_v)
    st.divider()
    _render_trends(_v)
    st.divider()
    _render_comparison()
