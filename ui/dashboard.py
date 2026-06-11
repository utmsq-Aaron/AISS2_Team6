"""Dashboard tab — activity map, key metrics, charts, and official Strava stats."""

import time as _time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


import folium
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import polyline as pl
import streamlit as st
from streamlit_folium import st_folium

from ui.shared import call_tool, strava_connected, wait_for_servers
from ui.styles import (
    ACTIVITY_ICONS, CHART_COLORS, DARK_MAP_ATTR, DARK_MAP_TILES,
    STRAVA_ORANGE, activity_icon, chart_style,
)

# GPS sport types that are expected to have a GPS track
_GPS_SPORTS = {"Run", "Hike", "Walk", "Ride", "TrailRun", "MountainBikeRide",
               "GravelRide", "NordicSki", "VirtualRun", "VirtualRide", "Swim",
               "BackcountrySki", "Snowshoe", "Kayaking", "Canoe", "Rowing",
               "Surfing", "Kitesurf", "Windsurf", "SUP", "EBikeRide"}


# ── Cached data loaders ───────────────────────────────────────────────────────
#
# DESIGN RATIONALE — warum hier gecacht wird und kein TTL gesetzt ist:
#
# Das Dashboard baut seine Ansicht ausschließlich aus gecachten MCP-Tool-Ergebnissen
# auf. Es wird KEIN Agent und KEIN LLM involviert — jede Strava-Anfrage zählt direkt
# gegen das Strava-API-Rate-Limit (100 Requests/15 min, 1000/Tag). Das Chat-Tab
# hingegen schickt beliebig viele Tool-Calls über den Orchestrator, was schnell einen
# Großteil des Tageskontingents verbraucht.
#
# Daher: Dashboard-Daten werden einmalig geladen und bleiben im Speicher bis der
# Nutzer explizit auf „🔄 Refresh data" klickt (löst st.cache_data.clear() aus).
# Kein automatischer Ablauf (kein TTL) — so bleibt das Kontingent für Chat-Interak-
# tionen reserviert und das Dashboard rendert ohne jede API-Latenz.
#
# Konsequenz für neue Features: Neue Dashboard-Widgets dürfen MCP-Tools nur über
# eine @st.cache_data-Funktion aufrufen — niemals direkt im Render-Pfad.

@st.cache_data(show_spinner=False)
def load_weather() -> Dict:
    """Fetch weather, pollen and UV in parallel via ToolHost.

    Raises on any failure so @st.cache_data does not cache the error — the next
    render attempt will retry, rather than serving a stale failure for 5 minutes.
    """
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed

    calls = {
        "weather": "weather__get_current_weather",
        "pollen":  "weather__get_pollen_levels",
        "uv":      "weather__get_uv_index",
    }

    def _fetch(key, tool_name):
        return key, json.loads(call_tool(tool_name, {}))

    result: Dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch, k, t): k for k, t in calls.items()}
        for fut in as_completed(futures, timeout=8):
            key, data = fut.result()
            result[key] = data

    sub_errors = [v.get("error") for v in result.values() if isinstance(v, dict) and v.get("error")]
    if sub_errors:
        raise RuntimeError(sub_errors[0])
    return result


@st.cache_data(show_spinner=False)
def load_activities(days: int = 30, _v: int = 0) -> Tuple[List[Dict], str]:
    """Returns (activities, error_message). Fetches only the given number of days.

    days=0 means no date filter (all time), capped at 500 activities.
    _v is a version counter: increment it (via session_state['_refresh_v']) to
    force a fresh API call regardless of TTL — e.g. after importing a new activity.
    """
    import json
    from datetime import date, timedelta

    if days > 0:
        limit = min(days * 3, 400)   # rough upper bound; 3 activities/day × days
        start_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        args: Dict = {"limit": limit, "start_date": start_date}
    else:
        args = {"limit": 500}        # all-time: cap at 500 to stay within rate limits

    raw = json.loads(call_tool("strava__get_activities", args))
    if isinstance(raw, dict) and raw.get("error"):
        return [], raw["error"]
    if isinstance(raw, list):
        return raw, ""
    return raw.get("activities", []), ""


@st.cache_data(show_spinner=False)
def load_activity_streams(activity_id: int) -> Dict:
    """Fetch GPS stream for a single activity — used when the list endpoint omits the polyline."""
    import json
    return json.loads(call_tool("strava__get_activity_streams", {"activity_id": activity_id}))


@st.cache_data(show_spinner=False)
def load_athlete_and_stats() -> Tuple[Dict, Dict]:
    import json
    data = json.loads(call_tool("strava__get_athlete_profile", {}))
    return data.get("profile", {}), data.get("official_stats", {})


# ── Data helpers ──────────────────────────────────────────────────────────────

def to_df(activities: List[Dict]) -> pd.DataFrame:
    if not activities:
        return pd.DataFrame()
    rows = []
    for a in activities:
        # Tool returns "date" as YYYY-MM-DD string
        ds = a.get("date") or a.get("start_date", "")[:10]
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d") if ds else None
        except (ValueError, TypeError):
            dt = None
        rows.append({
            "id":               a.get("id"),
            "name":             a.get("name", "Unknown"),
            "type":             a.get("sport_type") or a.get("type") or "Unknown",
            "date":             dt,
            "day":              dt.strftime("%Y-%m-%d") if dt else None,
            "year":             dt.year  if dt else None,
            "month":            dt.strftime("%Y-%m") if dt else None,
            "week":             dt.strftime("%Y-W%W") if dt else None,
            "distance_km":      a.get("distance_km", 0),
            "moving_time_min":  round(a.get("moving_time_hours", 0) * 60, 1),
            "elevation_m":      round(a.get("elevation_gain_m", 0), 0),
            "avg_speed_kmh":    a.get("avg_speed_kmh", 0),
            "avg_hr":           a.get("avg_heart_rate"),
            "kudos":            a.get("kudos", 0),
        })
    return pd.DataFrame(rows)

def _fmt_totals(t: Optional[Dict]) -> Dict:
    """Normalise a totals dict — handles both raw Strava API and pre-formatted tool output."""
    if not t:
        return {}
    # Pre-formatted tool output already has distance_km, moving_time_hours, elevation_gain_m
    if "distance_km" in t:
        return {
            "count":             t.get("count", 0),
            "distance_km":       t.get("distance_km", 0),
            "moving_time_hours": t.get("moving_time_hours", 0),
            "elevation_gain_m":  t.get("elevation_gain_m", 0),
        }
    # Raw Strava API response (distance in meters, time in seconds, elevation in meters)
    return {
        "count":             t.get("count", 0),
        "distance_km":       round(t.get("distance", 0) / 1000, 1),
        "moving_time_hours": round(t.get("moving_time", 0) / 3600, 1),
        "elevation_gain_m":  round(t.get("elevation_gain", 0), 0),
    }

def pace_str(avg_speed_kmh: float) -> str:
    if avg_speed_kmh <= 0:
        return "-"
    p = 60 / avg_speed_kmh
    return f"{int(p)}:{int((p % 1) * 60):02d} /km"


# ── Map helpers ───────────────────────────────────────────────────────────────

def decode_route(activity: Dict) -> List[List[float]]:
    # Tool returns map_polyline; fall back to nested map dict for raw API data
    encoded = activity.get("map_polyline") or (activity.get("map") or {}).get("summary_polyline", "")
    if encoded:
        try:
            return [[lat, lon] for lat, lon in pl.decode(encoded)]
        except Exception:
            pass
    # Fall back to pre-fetched stream points injected by the dashboard
    return activity.get("_stream_pts") or []


def enrich_activity_from_streams(activity: Dict) -> Dict:
    """Return a copy of activity with GPS points from the streams endpoint.

    Called when the Strava list endpoint omits the polyline for older activities.
    The result is cached so only one API call is made per activity per TTL window.
    """
    act_id = activity.get("id")
    if not act_id:
        return activity
    try:
        data = load_activity_streams(act_id)
        pts = data.get("points", [])
        if pts:
            enriched = dict(activity)
            enriched["_stream_pts"] = [[p["lat"], p["lon"]] for p in pts
                                       if p.get("lat") and p.get("lon")]
            return enriched
    except Exception:
        pass
    return activity

def build_map(
    activities: List[Dict],
    selected_id: Optional[int] = None,
) -> Optional[folium.Map]:
    routed = [(decode_route(a), a) for a in activities]
    routed = [(r, a) for r, a in routed if r]
    if not routed:
        return None

    # Fit to the selected activity's route, or to all routes when showing overview
    if selected_id:
        sel_route = next((r for r, a in routed if a.get("id") == selected_id), None)
        fit_pts = sel_route if sel_route else [pt for r, _ in routed for pt in r]
    else:
        fit_pts = [pt for r, _ in routed for pt in r]

    lats   = [p[0] for p in fit_pts]
    lons   = [p[1] for p in fit_pts]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]
    bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]

    m = folium.Map(
        location=center,
        tiles=DARK_MAP_TILES, attr=DARK_MAP_ATTR,
        prefer_canvas=True,
    )
    m.fit_bounds(bounds, padding=(30, 30))

    n = len(routed)
    for i, (coords, activity) in enumerate(routed):
        aid    = activity.get("id")
        is_sel = selected_id == aid
        is_dim = selected_id is not None and not is_sel

        weight  = 5   if is_sel else 2
        opacity = 0.95 if is_sel else (0.10 if is_dim else max(0.25, 1.0 - i / max(n, 1) * 0.75))

        dist_km = round(activity.get("distance", 0) / 1000, 1)
        t_min   = round(activity.get("moving_time", 0) / 60)
        tooltip = folium.Tooltip(
            f"<div style='font-family:sans-serif;padding:4px'>"
            f"<b style='color:{STRAVA_ORANGE}'>{activity.get('name','?')}</b><br>"
            f"{activity.get('type','?')} &nbsp;·&nbsp; {dist_km} km &nbsp;·&nbsp; {t_min} min"
            f"</div>"
        )
        folium.PolyLine(coords, color=STRAVA_ORANGE, weight=weight, opacity=opacity, tooltip=tooltip).add_to(m)

        if is_sel:
            folium.CircleMarker(coords[0],  radius=8, color="#2ECC71", fill=True, fill_color="#2ECC71", fill_opacity=1, tooltip="Start").add_to(m)
            folium.CircleMarker(coords[-1], radius=8, color="#E74C3C", fill=True, fill_color="#E74C3C", fill_opacity=1, tooltip="Finish").add_to(m)

    return m


# ── Stats table ───────────────────────────────────────────────────────────────

def _stats_table(data: Dict[str, Dict]) -> None:
    rows = [
        {
            "Sport":         sport,
            "Activities":    d.get("count", 0),
            "Distance (km)": d.get("distance_km", 0),
            "Time (h)":      d.get("moving_time_hours", 0),
            "Elevation (m)": d.get("elevation_gain_m", 0),
        }
        for sport, d in data.items() if d and d.get("count", 0) > 0
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("No data recorded yet.")


# ── Main render ───────────────────────────────────────────────────────────────

_DASH_PERIODS: Dict[str, int] = {
    "All time":   0,
    "1 year":     365,
    "6 months":   180,
    "3 months":   90,
    "30 days":    30,
    "14 days":    14,
    "7 days":     7,
}


def render_dashboard(sport_filter: Optional[str] = None) -> None:
    if not strava_connected():
        st.warning("**Strava not connected.** Open the ⚙️ Settings tab to connect your Strava account.")
        return

    if not wait_for_servers("strava", "weather"):
        return

    # Read the previously-selected period (or default "30 days") BEFORE the
    # spinner so we only fetch what the user actually wants to see.
    _v = st.session_state.get("_refresh_v", 0)
    _cur_period  = st.session_state.get("dash_period", "30 days")
    _load_days   = _DASH_PERIODS.get(_cur_period, 30)

    with st.spinner("Loading Strava data…"):
        try:
            activities, act_error = load_activities(days=_load_days, _v=_v)
            athlete, stats = load_athlete_and_stats()
        except Exception as e:
            st.error(f"Could not load Strava data: {e}")
            st.info("Make sure your `.env` has `CLIENT_ID` and `CLIENT_SECRET`, then reload.")
            return

    if not activities:
        if act_error and "activity:read_permission" in act_error:
            st.warning(
                "**Missing activity scope.** Your Strava token was authorized without "
                "`activity:read` permission.\n\n"
                "**Fix:** Go to ⚙️ **Settings → Strava → Disconnect**, then reconnect "
                "via the Strava OAuth button to grant the full activity access scope."
            )
        elif act_error:
            st.error(f"**Strava activities error:** {act_error}")
        else:
            st.info("**No Strava activities found** — your account has no recorded activities yet.")

    # Apply optional sport filter
    if sport_filter and sport_filter != "All":
        activities = [
            a for a in activities
            if (a.get("sport_type") or a.get("type")) == sport_filter
        ]

    # ── Search filter ─────────────────────────────────────────────────────────
    search_q = st.text_input(
        "🔍 Search activities",
        placeholder="Name or sport type…",
        key="dash_search",
        label_visibility="collapsed",
    )
    if search_q.strip():
        q = search_q.strip().lower()
        activities = [
            a for a in activities
            if q in (a.get("name") or "").lower()
            or q in (a.get("sport_type") or a.get("type") or "").lower()
        ]

    df = to_df(activities)

    # ── Athlete header ────────────────────────────────────────────────────────
    name  = athlete.get("name") or f"{athlete.get('firstname','')} {athlete.get('lastname','')}".strip()
    parts = [athlete.get("city"), athlete.get("state"), athlete.get("country")]
    loc   = ", ".join(p for p in parts if p)
    since = (athlete.get("member_since") or athlete.get("created_at") or "")[:4]

    c_pic, c_info = st.columns([1, 8])
    with c_pic:
        url = athlete.get("profile_url") or athlete.get("profile") or ""
        if url.startswith("http"):
            st.image(url, width=68)
    with c_info:
        st.markdown(f"## {name}")
        info_parts = []
        if loc:   info_parts.append(f"📍 {loc}")
        if since: info_parts.append(f"Member since {since}")
        if athlete.get("premium"): info_parts.append("⭐ Premium")
        st.caption("  ·  ".join(info_parts))

    # ── Weather widget ────────────────────────────────────────────────────────
    try:
        wd = load_weather()
        w  = wd.get("weather", {})
        uv = wd.get("uv", {})
        p  = wd.get("pollen", {}).get("pollen", {})

        _WMO = {
            0: "☀️ Clear", 1: "🌤️ Mainly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast",
            45: "🌫️ Foggy", 48: "🌫️ Foggy",
            51: "🌦️ Light drizzle", 53: "🌦️ Drizzle", 55: "🌧️ Dense drizzle",
            61: "🌧️ Light rain", 63: "🌧️ Rain", 65: "🌧️ Heavy rain",
            71: "🌨️ Light snow", 73: "🌨️ Snow", 75: "❄️ Heavy snow",
            80: "🌦️ Rain showers", 81: "🌧️ Rain showers", 82: "⛈️ Violent showers",
            95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm", 99: "⛈️ Thunderstorm",
        }
        _UV_RISK = {
            "low": "🟢", "moderate": "🟡", "high": "🟠", "very high": "🔴", "extreme": "🟣"
        }
        _POL_RISK = {"none": "🟢", "low": "🟡", "moderate": "🟠", "high": "🔴", "very high": "🟣"}

        condition = _WMO.get(w.get("weather_code", -1), "🌡️")
        uv_risk   = uv.get("risk", "?")
        uv_icon   = _UV_RISK.get(uv_risk, "")

        # Highest pollen type
        top_pollen = max(
            p.items(),
            key=lambda kv: kv[1].get("value_grains_m3", 0),
            default=(None, {}),
        ) if p else (None, {})
        pol_level = top_pollen[1].get("level", "none") if top_pollen[0] else "none"
        pol_icon  = _POL_RISK.get(pol_level, "")

        wc1, wc2, wc3, wc4 = st.columns(4)
        wc1.metric("Weather Karlsruhe", f"{condition}", f"{w.get('temperature_c', '?')} °C")
        wc2.metric("Wind",             f"{w.get('wind_speed_kmh', '?')} km/h")
        wc3.metric("UV Index",         f"{uv_icon} {uv.get('uv_index', '?')}", uv_risk)
        wc4.metric(
            "Pollen",
            f"{pol_icon} {pol_level.title()}",
            top_pollen[0].replace("_pollen", "").title() if top_pollen[0] else "—",
        )
    except Exception:
        _wc1, _wc2 = st.columns([10, 1])
        with _wc1:
            st.caption("⚠️ Weather unavailable — make sure the weather MCP server is running.")
        with _wc2:
            if st.button("↻", key="retry_weather", help="Wetterdaten neu laden"):
                load_weather.clear()
                st.rerun()

    st.divider()

    # ── Period selector ───────────────────────────────────────────────────────
    period = st.radio(
        "Period",
        list(_DASH_PERIODS.keys()),
        index=4,
        horizontal=True,
        key="dash_period",
        label_visibility="collapsed",
    )
    period_days = _DASH_PERIODS[period]
    if period_days > 0:
        cutoff = datetime.utcnow() - timedelta(days=period_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        # Tool returns "date" as YYYY-MM-DD; fall back to "start_date" ISO prefix
        activities = [
            a for a in activities
            if (a.get("date") or a.get("start_date", "")[:10] or "") >= cutoff_str
        ]
        df = to_df(activities)

    st.divider()

    # ── Cache / freshness indicator ───────────────────────────────────────────
    # Read the disk-cache file's mtime — the single source of truth.
    # A new mtime means the MCP server just wrote fresh data from the Strava API.
    # Unchanged mtime means this render served data from the on-disk cache.
    from pathlib import Path as _Path
    _cache_file = _Path(".cache/strava_activities.json")
    try:
        _cur_mtime = _cache_file.stat().st_mtime if _cache_file.exists() else None
    except Exception:
        _cur_mtime = None

    _prev_mtime = st.session_state.get("_cache_mtime")
    if _cur_mtime != _prev_mtime:
        st.session_state["_cache_mtime"] = _cur_mtime
        st.caption("⚡ Freshly loaded from the API")
    else:
        if _cur_mtime:
            _dt = datetime.fromtimestamp(_cur_mtime).strftime('%H:%M:%S')
            st.caption(
                f"💾 Cached data · last updated {_dt} — 🔄 Refresh to fetch new data"
            )
    # ── Key metrics ───────────────────────────────────────────────────────────
    total_dist = df["distance_km"].sum()          if not df.empty else 0
    total_h    = df["moving_time_min"].sum() / 60 if not df.empty else 0
    total_elev = df["elevation_m"].sum()          if not df.empty else 0
    avg_hr     = df["avg_hr"].dropna().mean()     if not df.empty else None

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Activities",      f"{len(df):,}")
    m2.metric("Total Distance",  f"{total_dist:,.1f} km")
    m3.metric("Total Time",      f"{total_h:,.0f} h")
    m4.metric("Total Elevation", f"{total_elev:,.0f} m")
    m5.metric("Avg Heart Rate",  f"{avg_hr:.0f} bpm" if avg_hr else "—")

    st.divider()

    # ── Activity map ──────────────────────────────────────────────────────────
    st.markdown("### Activity Map")

    # Enrich any GPS activities the list endpoint omitted polylines for.
    # load_activity_streams is @st.cache_data so API calls only happen once per TTL.
    _missing = [
        a for a in activities
        if a.get("id") and not decode_route(a)
        and (a.get("sport_type") or a.get("type")) in _GPS_SPORTS
    ]
    if _missing:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as _ex:
            _enriched_list = list(_ex.map(enrich_activity_from_streams, _missing))
        _enriched_map = {e["id"]: e for e in _enriched_list if e.get("_stream_pts")}
        if _enriched_map:
            activities = [_enriched_map.get(a.get("id"), a) for a in activities]

    routed = [a for a in activities if decode_route(a)]

    col_ctrl, col_map = st.columns([1, 3])

    with col_ctrl:
        options: Dict[str, Optional[int]] = {"All activities": None}
        for a in sorted(activities, key=lambda a: a.get("start_date", ""), reverse=True):
            label = f"{activity_icon(a.get('type',''))} {a.get('name','?')}  ({a.get('start_date','')[:10]})"
            options[label] = a.get("id")

        selected_label = st.selectbox("Focus activity", list(options.keys()), label_visibility="collapsed")
        selected_id    = options[selected_label]

        if not routed:
            st.info("No GPS routes found.")

        if selected_id:
            sel = next((a for a in activities if a.get("id") == selected_id), None)
            # If the list endpoint omitted the polyline, enrich from streams
            if sel and not decode_route(sel) and sel.get("type", "") in _GPS_SPORTS:
                with st.spinner("Loading GPS track…"):
                    sel = enrich_activity_from_streams(sel)
                # Write the enriched activity back so build_map sees the GPS points
                activities = [sel if a.get("id") == selected_id else a for a in activities]
            if sel:
                st.markdown("---")
                sport = sel.get("type", "")
                st.markdown(f"**{activity_icon(sport)} {sel.get('name','')}**")
                st.caption(f"{sport}  ·  {sel.get('start_date','')[:10]}")

                dist_km = sel.get("distance_km", 0)
                t_min   = round(sel.get("moving_time_hours", 0) * 60)
                elev    = round(sel.get("elevation_gain_m", 0))
                spd     = sel.get("avg_speed_kmh", 0)
                hr      = sel.get("avg_heart_rate")

                st.metric("Distance",  f"{dist_km} km")
                st.metric("Duration",  f"{int(t_min//60)}h {int(t_min%60)}min" if t_min >= 60 else f"{int(t_min)} min")
                if sport in ("Run", "Hike", "Walk"):
                    st.metric("Avg Pace",  pace_str(spd))
                elif spd > 0:
                    st.metric("Avg Speed", f"{spd:.1f} km/h")
                st.metric("Elevation", f"{elev} m")
                if hr:
                    st.metric("Avg HR", f"{hr:.0f} bpm")

                if decode_route(sel):  # only show when GPS data actually loaded
                    st.markdown("")
                    if st.button("🎥 3D Flythrough", key="flythrough_btn", type="primary", width='stretch'):
                        st.session_state["flythrough_id"]   = selected_id
                        st.session_state["flythrough_name"] = sel.get("name", "")

                # ── Delete ────────────────────────────────────────────────────
                st.markdown("")
                _del_key = f"_del_confirm_{selected_id}"
                if not st.session_state.get(_del_key):
                    if st.button("🗑️ Delete activity", key=f"del_btn_{selected_id}", width='stretch'):
                        st.session_state[_del_key] = True
                        st.rerun()
                else:
                    st.warning(f"Really delete **{sel.get('name','')}**? This cannot be undone.")
                    c_yes, c_no = st.columns(2)
                    if c_yes.button("✓ Yes, delete", key=f"del_yes_{selected_id}", type="primary", width='stretch'):
                        import json as _j
                        result = _j.loads(call_tool("strava__delete_activity", {"activity_id": selected_id}))
                        st.session_state.pop(_del_key, None)
                        if result.get("success"):
                            from pathlib import Path
                            Path(".cache/strava_activities.json").unlink(missing_ok=True)
                            st.session_state["_refresh_v"] = st.session_state.get("_refresh_v", 0) + 1
                            st.cache_data.clear()
                            st.success("Activity deleted.")
                            st.rerun()
                        else:
                            err = result.get("error", "Unknown error")
                            if "unknown tool" in err.lower():
                                st.warning(
                                    f"**Tool not found** — the Strava MCP server is running an older version.  \n"
                                    "Go to ⚙️ **Settings → Developer → Restart MCP Servers** and try again."
                                )
                            else:
                                st.error(err)
                    if c_no.button("✗ Cancel", key=f"del_no_{selected_id}", width='stretch'):
                        st.session_state.pop(_del_key, None)
                        st.rerun()

        else:
            st.markdown("---")
            st.caption(f"**{len(routed)}** of {len(activities)} activities have GPS routes.")

    with col_map:
        fmap = build_map(activities, selected_id=selected_id)
        if fmap:
            st_folium(fmap, height=500, width='stretch', returned_objects=[])
        else:
            st.info("No GPS route data available.")

    # ── Activity stream analysis ──────────────────────────────────────────────
    if selected_id and sel:
        st.divider()
        from ui.activity_analysis import show_analysis
        show_analysis(selected_id, sel.get("name", ""))

    # ── 3D Flythrough panel ───────────────────────────────────────────────────
    if st.session_state.get("flythrough_id"):
        fid   = st.session_state["flythrough_id"]
        fname = st.session_state.get("flythrough_name", "")
        c_hdr, c_close = st.columns([9, 1])
        c_hdr.markdown(f"#### 🎥 3D Flythrough — {fname}")
        if c_close.button("✕ Close", key="flythrough_close"):
            del st.session_state["flythrough_id"]
            del st.session_state["flythrough_name"]
            st.rerun()
        else:
            from ui.flythrough_3d import show_flythrough
            show_flythrough(fid, fname)

    st.divider()

    # ── Recent activity cards ─────────────────────────────────────────────────
    if not df.empty:
        st.markdown("### Recent Activities")
        for i, (_, row) in enumerate(df.head(9).iterrows()):
            if i % 3 == 0:
                cols = st.columns(3)
            icon = activity_icon(row["type"])
            date = row["date"].strftime("%d %b %Y") if row["date"] is not None else ""
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"{icon} **{row['name']}**")
                    st.caption(f"{row['type']} · {date}")
                    sc1, sc2 = st.columns(2)
                    sc1.metric("Distance", f"{row['distance_km']} km")
                    sc2.metric("Time",     f"{int(row['moving_time_min'])} min")
                    if row["elevation_m"] > 0:
                        sc3, sc4 = st.columns(2)
                        sc3.metric("Elevation", f"{int(row['elevation_m'])} m")
                        if row["avg_speed_kmh"] > 0:
                            if row["type"] in ("Run", "Hike", "Walk"):
                                sc4.metric("Pace", pace_str(row["avg_speed_kmh"]))
                            else:
                                sc4.metric("Speed", f"{row['avg_speed_kmh']} km/h")

        st.divider()

    # ── Training charts ───────────────────────────────────────────────────────
    if not df.empty:
        st.markdown("### Training Overview")

        df_typed = df.dropna(subset=["type"])
        df_typed = df_typed[df_typed["type"].str.strip().astype(bool)]

        # Adaptive aggregation: day for ≤30 d, week for ≤180 d, month for longer
        if 0 < period_days <= 30:
            agg_col, agg_label = "day", "Day"
        elif 0 < period_days <= 180:
            agg_col, agg_label = "week", "Week"
        else:
            agg_col, agg_label = "month", "Month"

        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f'<p class="chart-label">Distance per {agg_label}</p>', unsafe_allow_html=True)
            agg_dist = (df_typed.dropna(subset=[agg_col])
                        .groupby(agg_col)["distance_km"].sum()
                        .reset_index().sort_values(agg_col))
            fig = px.bar(agg_dist, x=agg_col, y="distance_km",
                         labels={agg_col: "", "distance_km": "km"},
                         color_discrete_sequence=[STRAVA_ORANGE])
            fig.update_traces(marker_line_width=0)
            st.plotly_chart(chart_style(fig), width='stretch')

        with c2:
            n_sport_types = df_typed["type"].nunique()
            if n_sport_types > 1:
                st.markdown('<p class="chart-label">Sport Breakdown</p>', unsafe_allow_html=True)
                tdf = df_typed.groupby("type").agg(count=("id", "count")).reset_index()
                fig = px.pie(tdf, values="count", names="type",
                             color_discrete_sequence=CHART_COLORS, hole=0.5)
                fig.update_traces(textposition="inside", textinfo="percent+label",
                                  textfont_size=11)
                st.plotly_chart(chart_style(fig), width='stretch')
            else:
                # Single sport — show the most diagnostic per-activity metric
                sport = df_typed["type"].iloc[0] if not df_typed.empty else ""
                recent = df_typed.sort_values("day").tail(50).copy()
                recent["elevation_gain_m"] = recent["elevation_m"]
                recent["pace_min_per_km"] = recent["avg_speed_kmh"].apply(
                    lambda s: round(60 / s, 2) if s and s > 0 else None
                )
                running_like = sport in ("Run", "TrailRun", "VirtualRun", "Hike", "Walk")
                cycling_like = sport in ("Ride", "MountainBikeRide", "GravelRide",
                                         "EBikeRide", "VirtualRide")
                has_pace  = running_like and recent["pace_min_per_km"].notna().any()
                has_speed = cycling_like and recent["avg_speed_kmh"].gt(0).any()
                has_elev  = recent["elevation_gain_m"].gt(0).any()

                if has_pace:
                    st.markdown('<p class="chart-label">Pace per Activity</p>', unsafe_allow_html=True)
                    fig = px.scatter(recent, x="date", y="pace_min_per_km",
                                     size="distance_km",
                                     labels={"date": "", "pace_min_per_km": "min/km"},
                                     color_discrete_sequence=[STRAVA_ORANGE],
                                     trendline="ols")
                    fig.update_traces(marker_line_width=0)
                elif has_speed:
                    st.markdown('<p class="chart-label">Speed per Activity</p>', unsafe_allow_html=True)
                    fig = px.scatter(recent, x="date", y="avg_speed_kmh",
                                     size="distance_km",
                                     labels={"date": "", "avg_speed_kmh": "km/h"},
                                     color_discrete_sequence=[STRAVA_ORANGE],
                                     trendline="ols")
                    fig.update_traces(marker_line_width=0)
                elif has_elev:
                    st.markdown('<p class="chart-label">Elevation per Activity</p>', unsafe_allow_html=True)
                    fig = px.bar(recent, x="date", y="elevation_gain_m",
                                 labels={"date": "", "elevation_gain_m": "m"},
                                 color_discrete_sequence=[CHART_COLORS[2]])
                    fig.update_traces(marker_line_width=0)
                else:
                    st.markdown('<p class="chart-label">Distance per Activity</p>', unsafe_allow_html=True)
                    fig = px.bar(recent, x="date", y="distance_km",
                                 labels={"date": "", "distance_km": "km"},
                                 color_discrete_sequence=[STRAVA_ORANGE])
                    fig.update_traces(marker_line_width=0)
                st.plotly_chart(chart_style(fig), width='stretch')

        c3, c4 = st.columns(2)
        with c3:
            st.markdown(f'<p class="chart-label">Training Time per {agg_label}</p>', unsafe_allow_html=True)
            agg_time = (df_typed.dropna(subset=[agg_col])
                        .groupby(agg_col)["moving_time_min"].sum()
                        .reset_index().sort_values(agg_col))
            agg_time["hours"] = (agg_time["moving_time_min"] / 60).round(2)
            fig = px.area(agg_time, x=agg_col, y="hours",
                          labels={agg_col: "", "hours": "h"},
                          color_discrete_sequence=[STRAVA_ORANGE])
            fig.update_traces(fill="tozeroy", line_width=2)
            st.plotly_chart(chart_style(fig), width='stretch')

        with c4:
            if period_days == 0 or period_days > 90:
                st.markdown('<p class="chart-label">Year-over-Year Distance</p>', unsafe_allow_html=True)
                yearly = (df_typed.dropna(subset=["year"])
                          .groupby(["year", "type"])["distance_km"].sum()
                          .reset_index())
                yearly["year"] = yearly["year"].astype(str)
                fig = px.bar(yearly, x="year", y="distance_km", color="type", barmode="stack",
                             labels={"year": "", "distance_km": "km", "type": "Sport"},
                             color_discrete_sequence=CHART_COLORS)
                fig.update_traces(marker_line_width=0)
            else:
                st.markdown(f'<p class="chart-label">Elevation per {agg_label}</p>', unsafe_allow_html=True)
                agg_elev = (df_typed.dropna(subset=[agg_col])
                            .groupby(agg_col)["elevation_m"].sum()
                            .reset_index().sort_values(agg_col))
                fig = px.bar(agg_elev, x=agg_col, y="elevation_m",
                             labels={agg_col: "", "elevation_m": "m"},
                             color_discrete_sequence=["#FCD34D"])
                fig.update_traces(marker_line_width=0)
            st.plotly_chart(chart_style(fig), width='stretch')

        st.divider()

    # ── Official Strava stats ─────────────────────────────────────────────────
    st.markdown("### Official Strava Stats")
    tab_ytd, tab_4w, tab_all = st.tabs(["Year to Date", "Last 4 Weeks", "All Time"])

    # stats is the official_stats dict from get_athlete_profile tool
    ytd = stats.get("year_to_date", {})
    lfw = stats.get("last_4_weeks", {})
    alt = stats.get("all_time", {})

    with tab_ytd:
        _stats_table({
            "Run":  _fmt_totals(ytd.get("run") or stats.get("ytd_run_totals")),
            "Ride": _fmt_totals(ytd.get("ride") or stats.get("ytd_ride_totals")),
            "Swim": _fmt_totals(ytd.get("swim") or stats.get("ytd_swim_totals")),
        })
    with tab_4w:
        _stats_table({
            "Run":  _fmt_totals(lfw.get("run") or stats.get("recent_run_totals")),
            "Ride": _fmt_totals(lfw.get("ride") or stats.get("recent_ride_totals")),
            "Swim": _fmt_totals(lfw.get("swim") or stats.get("recent_swim_totals")),
        })
    with tab_all:
        _stats_table({
            "Run":  _fmt_totals(alt.get("run") or stats.get("all_run_totals")),
            "Ride": _fmt_totals(alt.get("ride") or stats.get("all_ride_totals")),
            "Swim": _fmt_totals(alt.get("swim") or stats.get("all_swim_totals")),
        })
        br = stats.get("biggest_ride_distance_km") or round(stats.get("biggest_ride_distance", 0) / 1000, 1)
        bc = stats.get("biggest_climb_elevation_gain_m") or stats.get("biggest_climb_elevation_gain", 0)
        if br or bc:
            b1, b2 = st.columns(2)
            b1.metric("Biggest Ride",  f"{br} km")
            b2.metric("Biggest Climb", f"{bc} m")
