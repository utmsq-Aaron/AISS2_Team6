"""Sync tab — two-stage Garmin → Strava export.

Stage 1 (setup):   Pick a date range, click Fetch. Zero API calls until then.
Stage 2 (preview): See all activities, select/deselect, then export.
"""

import io
import math
import os
import time
import zipfile
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv

from ui.shared import garmin_connected, strava_connected
from ui.styles import ACCENT, BORDER, TEXT_MUTED, activity_icon

load_dotenv()

TOKEN_STORE = ".tokens"

# ── Preset date-range options ─────────────────────────────────────────────────

_PRESETS: Dict[str, int] = {
    "Last 7 days":   7,
    "Last 30 days":  30,
    "Last 90 days":  90,
    "Last 6 months": 182,
    "Last year":     365,
    "All time":      0,    # 0 → use 2000-01-01 as start
    "Custom range":  -1,   # -1 → show date pickers
}


# ── Backend helpers ───────────────────────────────────────────────────────────

def _garmin_client():
    from garminconnect import Garmin
    g = Garmin()
    g.login(tokenstore=TOKEN_STORE)
    return g


def _strava_token() -> str:
    cid  = os.getenv("CLIENT_ID")
    csec = os.getenv("CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("CLIENT_ID / CLIENT_SECRET not set in .env")
    from auth.strava_oauth import OAuth2Manager
    return OAuth2Manager(cid, csec).get_valid_access_token()


def _download_fit(garmin, activity_id: int) -> Optional[bytes]:
    try:
        from garminconnect import Garmin as _G
        raw = garmin.download_activity(activity_id, dl_fmt=_G.ActivityDownloadFormat.ORIGINAL)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".fit"):
                    return zf.read(name)
    except Exception:
        pass
    return None


def _upload_to_strava(token: str, fit_bytes: bytes, name: str) -> Dict:
    resp = requests.post(
        "https://www.strava.com/api/v3/uploads",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("activity.fit", fit_bytes, "application/octet-stream")},
        data={"data_type": "fit", "name": name},
        timeout=30,
    )
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not resp.ok:
        # Surface the real HTTP error so the caller can show it
        detail = (body.get("message") or body.get("error")
                  or f"HTTP {resp.status_code}")
        body["error"] = detail
    return body


def _poll_upload(token: str, upload_id: int, timeout: int = 60) -> Dict:
    """Poll GET /v3/uploads/{id} until Strava finishes processing (or timeout)."""
    resp: Dict = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"https://www.strava.com/api/v3/uploads/{upload_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            ).json()
        except Exception:
            time.sleep(4)
            continue

        # Terminal states: error set, or activity_id populated
        if resp.get("error") or resp.get("activity_id"):
            return resp
        status = (resp.get("status") or "").lower()
        if "ready" in status or "error" in status:
            return resp
        time.sleep(4)
    return resp   # return last known state on timeout


# ── UI helpers ────────────────────────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    if not seconds:
        return "—"
    m = int(seconds // 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"



@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_route_coords(activity_id: int) -> List[Tuple[float, float]]:
    """Return [(lat, lon), …] for the activity route. Cached per activity_id."""
    try:
        g = _garmin_client()
        d = g.get_activity_details(str(activity_id), maxpoly=500)
        pts = (d.get("geoPolylineDTO") or {}).get("polyline") or []
        return [(p["lat"], p["lon"]) for p in pts if p.get("valid") and p.get("lat") and p.get("lon")]
    except Exception:
        return []


def _route_center_zoom(route_coords: Tuple[Tuple[float, float], ...], padding: float = 2.0) -> Tuple[float, float, float]:
    """Return (center_lat, center_lon, zoom) that fits all route points with padding."""
    lats = [c[0] for c in route_coords]
    lons = [c[1] for c in route_coords]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2
    lat_span = (max_lat - min_lat) * padding or 0.001
    lon_span = (max_lon - min_lon) * padding or 0.001
    # Mapbox zoom: higher span → lower zoom. Both axes considered, take the tighter one.
    zoom = min(math.log2(360 / lon_span), math.log2(180 / lat_span)) - 0.6
    return center_lat, center_lon, max(1.0, min(zoom, 18.0))


@st.cache_data(show_spinner=False)
def _mini_map(lat: float, lon: float, route_coords: Tuple[Tuple[float, float], ...] = ()) -> go.Figure:
    """Dark-style map — shows full route when coords available, start marker otherwise."""
    fig = go.Figure()

    if route_coords:
        route_lats = [c[0] for c in route_coords]
        route_lons = [c[1] for c in route_coords]
        fig.add_trace(go.Scattermapbox(
            lat=route_lats, lon=route_lons,
            mode="lines",
            line=dict(color=ACCENT, width=3),
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scattermapbox(
            lat=[route_lats[0]], lon=[route_lons[0]],
            mode="markers", marker=dict(size=10, color="#2ECC71"),
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scattermapbox(
            lat=[route_lats[-1]], lon=[route_lons[-1]],
            mode="markers", marker=dict(size=10, color="#E74C3C"),
            hoverinfo="skip",
        ))
        center_lat, center_lon, zoom = _route_center_zoom(route_coords)
    else:
        fig.add_trace(go.Scattermapbox(
            lat=[lat], lon=[lon],
            mode="markers",
            marker=dict(size=12, color=ACCENT),
            hoverinfo="skip",
        ))
        center_lat, center_lon, zoom = lat, lon, 12

    fig.update_layout(
        mapbox=dict(
            style="carto-darkmatter",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=zoom,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=155,
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


def _activity_card(act: Dict) -> None:
    """Render one activity preview card (checkbox state stored in session_state)."""
    aid      = act.get("activityId")
    name     = act.get("activityName") or f"Activity {aid}"
    atype    = (act.get("activityType") or {}).get("typeKey", "")
    date_str = (act.get("startTimeLocal") or "")[:10]
    dist_km  = round((act.get("distance")  or 0) / 1000, 2)
    dur_s    = act.get("duration") or 0
    avg_hr   = act.get("averageHR")
    elev     = act.get("elevationGain")
    cals     = act.get("calories")
    icon     = activity_icon(atype)
    lat      = act.get("startLatitude")
    lon      = act.get("startLongitude")
    has_poly = act.get("hasPolyline", False)

    with st.container(border=True):
        left, right = st.columns([3, 1])

        with left:
            # key= alone is correct — session_state is pre-initialised in
            # _render_setup. Passing value= alongside key= causes Streamlit
            # to reset the state on rerenders, selecting the wrong activity.
            st.checkbox(label=f"**{icon} {name}**", key=f"chk_{aid}")
            st.caption(f"{atype or 'Activity'}  ·  {date_str}")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Distance",  f"{dist_km} km"     if dist_km else "—")
            m2.metric("Duration",  _fmt_duration(dur_s))
            m3.metric("Avg HR",    f"{avg_hr:.0f} bpm" if avg_hr  else "—")
            m4.metric("Elevation", f"{elev:.0f} m"     if elev    else "—")
            m5.metric("Calories",  f"{cals:.0f} kcal"  if cals    else "—")

        with right:
            if lat and lon:
                route = tuple(_fetch_route_coords(aid)) if has_poly else ()
                st.plotly_chart(_mini_map(lat, lon, route), width='stretch')
            else:
                st.markdown(
                    f'<div style="height:155px;display:flex;align-items:center;'
                    f'justify-content:center;color:{TEXT_MUTED};font-size:12px;'
                    f'border:1px dashed {BORDER};border-radius:10px">No GPS</div>',
                    unsafe_allow_html=True,
                )


# ── Stage 1: Setup ────────────────────────────────────────────────────────────

def _render_setup() -> None:
    today = date.today()

    st.markdown("#### Select date range")
    preset = st.selectbox(
        "Range",
        list(_PRESETS.keys()),
        index=1,
        label_visibility="collapsed",
        key="sync_preset",
    )
    days = _PRESETS[preset]

    if preset == "Custom range":
        c1, c2 = st.columns(2)
        start_d = c1.date_input("From", value=today - timedelta(days=30), key="sync_from")
        end_d   = c2.date_input("To",   value=today,                      key="sync_to")
        start_str = start_d.strftime("%Y-%m-%d")
        end_str   = end_d.strftime("%Y-%m-%d")
    elif days == 0:
        start_str = "2000-01-01"
        end_str   = today.strftime("%Y-%m-%d")
    else:
        start_str = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        end_str   = today.strftime("%Y-%m-%d")

    if preset != "All time":
        st.caption(f"{start_str}  →  {end_str}")
    else:
        st.caption("All recorded activities")

    st.markdown("")
    if st.button("🔍 Fetch Activities from Garmin", type="primary", width='stretch'):
        with st.spinner("Connecting to Garmin Connect…"):
            try:
                g    = _garmin_client()
                acts = g.get_activities_by_date(start_str, end_str) or []
            except Exception as e:
                st.error(f"Garmin connection failed: {e}")
                return

        if not acts:
            st.info(f"No activities found between {start_str} and {end_str}.")
            return

        # Store fetched data and default-select everything (always reset to
        # True so stale False values from a previous fetch don't carry over)
        st.session_state.sync_activities = acts
        st.session_state.sync_date_range = (start_str, end_str)
        for a in acts:
            st.session_state[f"chk_{a.get('activityId')}"] = True
        st.rerun()


# ── Stage 2: Preview & Export ─────────────────────────────────────────────────

def _render_preview(activities: List[Dict]) -> None:
    start_str, end_str = st.session_state.get("sync_date_range", ("?", "?"))

    # Header row
    hdr, back = st.columns([5, 1])
    hdr.markdown(f"#### {len(activities)} activities  ·  {start_str} → {end_str}")
    with back:
        if st.button("← Change range", width='stretch'):
            _clear_preview_state(activities)
            st.rerun()

    # Bulk selection
    n_sel = sum(
        1 for a in activities
        if st.session_state.get(f"chk_{a.get('activityId')}", False)
    )
    cc1, cc2, cc3 = st.columns([3, 1, 1])
    cc1.caption(f"**{n_sel}** of **{len(activities)}** selected for export")
    with cc2:
        if st.button("Select all", width='stretch'):
            for a in activities:
                st.session_state[f"chk_{a.get('activityId')}"] = True
            st.rerun()
    with cc3:
        if st.button("Deselect all", width='stretch'):
            for a in activities:
                st.session_state[f"chk_{a.get('activityId')}"] = False
            st.rerun()

    st.divider()

    # Activity cards
    for act in activities:
        _activity_card(act)

    st.divider()

    # Export controls
    to_export = [
        a for a in activities
        if st.session_state.get(f"chk_{a.get('activityId')}", False)
    ]
    n_exp = len(to_export)

    if n_exp == 0:
        st.info("Select at least one activity to export.")
        return

    label = f"⬆️ Export {n_exp} activit{'y' if n_exp == 1 else 'ies'} → Strava"
    if not st.button(label, type="primary", width='stretch'):
        return

    # ── Run export ────────────────────────────────────────────────────────────
    try:
        garmin = _garmin_client()
        token  = _strava_token()
    except Exception as e:
        st.error(f"Connection error: {e}")
        return

    bar    = st.progress(0.0)
    status_area = st.empty()
    log    = st.empty()
    counts = {"ok": 0, "duplicate": 0, "skipped": 0, "error": 0}
    lines: List[str] = []

    def _render_log():
        log.markdown("\n\n".join(lines[-12:]))

    for i, act in enumerate(to_export):
        aid      = act.get("activityId")
        name     = act.get("activityName") or f"Activity {aid}"
        date_str = (act.get("startTimeLocal") or "")[:10]
        label    = f"**{name}** ({date_str})"

        bar.progress((i + 0.3) / n_exp)
        status_area.caption(f"⬇️ Downloading FIT — {label}")

        fit = _download_fit(garmin, aid)
        if fit is None:
            lines.append(f"⚠️ {label} — no FIT file, skipped")
            counts["skipped"] += 1
            bar.progress((i + 1) / n_exp)
            _render_log()
            continue

        bar.progress((i + 0.6) / n_exp)
        status_area.caption(f"⬆️ Uploading to Strava — {label}")
        resp = _upload_to_strava(token, fit, name)

        if resp.get("error"):
            # Immediate rejection (auth error, bad format, etc.)
            lines.append(f"❌ {label} — {resp['error']}")
            counts["error"] += 1
            bar.progress((i + 1) / n_exp)
            _render_log()
            continue

        upload_id = resp.get("id")
        if not upload_id:
            detail = (resp.get("message") or resp.get("error")
                      or str(resp))
            lines.append(f"❌ {label} — Strava rejected upload: {detail}")
            counts["error"] += 1
            bar.progress((i + 1) / n_exp)
            _render_log()
            continue

        # Strava processes FITs asynchronously — poll until done
        status_area.caption(f"⏳ Processing on Strava — {label}")
        final = _poll_upload(token, upload_id)

        err         = final.get("error") or ""
        activity_id = final.get("activity_id")

        if err:
            if "already exists" in err.lower():
                lines.append(f"⚠️ {label} — already exists on Strava (duplicate)")
                counts["duplicate"] += 1
            else:
                lines.append(f"❌ {label} — {err}")
                counts["error"] += 1
        elif activity_id:
            url = f"https://www.strava.com/activities/{activity_id}"
            lines.append(f"✅ {label} — [View on Strava]({url})")
            counts["ok"] += 1
        else:
            # Still processing after timeout — usually appears within a minute
            lines.append(f"⏳ {label} — still processing, check Strava in a moment")
            counts["ok"] += 1

        bar.progress((i + 1) / n_exp)
        _render_log()

    status_area.empty()
    log.markdown("\n\n".join(lines))
    bar.progress(1.0)

    summary_parts = []
    if counts["ok"]:        summary_parts.append(f"**{counts['ok']}** uploaded")
    if counts["duplicate"]: summary_parts.append(f"**{counts['duplicate']}** already on Strava")
    if counts["skipped"]:   summary_parts.append(f"**{counts['skipped']}** skipped (no FIT)")
    if counts["error"]:     summary_parts.append(f"**{counts['error']}** errors")
    st.success("Done — " + ", ".join(summary_parts) + ".")

    if counts["ok"]:
        st.info(
            "Activities uploaded — the Dashboard tab caches Strava data for 5 min. "
            "Use the **🔄 Refresh data** button in the sidebar to see them immediately.",
            icon="ℹ️",
        )


def _clear_preview_state(activities: List[Dict]) -> None:
    st.session_state.pop("sync_activities", None)
    st.session_state.pop("sync_date_range", None)
    for a in activities:
        st.session_state.pop(f"chk_{a.get('activityId')}", None)


# ── Entry point ───────────────────────────────────────────────────────────────

def render_sync() -> None:
    st.markdown("### Garmin → Strava Export")
    st.caption(
        "Download FIT files from Garmin Connect and upload them directly to Strava. "
        "Strava deduplicates by file hash — re-uploading an existing activity is safe."
    )

    if not garmin_connected():
        st.warning("Garmin not connected. Run `python auth/garmin_setup.py` first.")
        return
    if not strava_connected():
        st.warning("Strava not connected. Open the Dashboard tab to authorize.")
        return

    activities: List[Dict] = st.session_state.get("sync_activities", [])

    if not activities:
        _render_setup()
    else:
        _render_preview(activities)
