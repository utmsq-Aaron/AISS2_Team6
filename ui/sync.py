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
    import pathlib
    token_file = pathlib.Path(TOKEN_STORE) / "garmin_tokens.json"
    has_tokens = token_file.exists()
    try:
        # Token-only path — no credentials needed when tokens are cached
        g = Garmin()
        g.login(tokenstore=TOKEN_STORE)
        return g
    except Exception as exc:
        msg = str(exc)
        # Fall back to credential login if tokens are absent or fully expired
        email    = os.getenv("GARMIN_EMAIL", "") or ""
        password = os.getenv("GARMIN_PASSWORD", "") or ""
        if email and password:
            try:
                g2 = Garmin(email=email, password=password)
                g2.login(tokenstore=TOKEN_STORE)
                return g2
            except Exception:
                pass
        if not has_tokens:
            raise RuntimeError(
                "Garmin not connected. Upload token files in ⚙️ Settings → Garmin."
            ) from exc
        raise RuntimeError(
            f"Garmin login failed: {msg}\n"
            "Reconnect via ⚙️ Settings → Garmin."
        ) from exc


def _strava_token() -> str:
    cid  = os.getenv("CLIENT_ID")
    csec = os.getenv("CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("CLIENT_ID / CLIENT_SECRET not set in .env")
    from auth.strava_oauth import OAuth2Manager
    return OAuth2Manager(cid, csec).get_valid_access_token()


def _fetch_strava_for_range(start_str: str, end_str: str) -> List[Dict]:
    """Fetch all Strava activities in the given date range directly via REST API."""
    try:
        token  = _strava_token()
        after  = int(datetime.strptime(start_str, "%Y-%m-%d").timestamp())
        before = int((datetime.strptime(end_str,   "%Y-%m-%d") + timedelta(days=1)).timestamp())
        collected: List[Dict] = []
        page = 1
        while True:
            resp = requests.get(
                "https://www.strava.com/api/v3/activities",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": 200, "page": page, "after": after, "before": before},
                timeout=30,
            )
            if not resp.ok:
                break
            batch = resp.json()
            if not batch:
                break
            collected.extend(batch)
            page += 1
            if len(batch) < 200:
                break
        return collected
    except Exception:
        return []


def _match_in_strava(garmin_act: Dict, strava_acts: List[Dict]) -> bool:
    """True if this Garmin activity is already present in the given Strava list.

    Matches by: same calendar date (±1 day for timezone drift) AND either
    duration within 3 minutes OR distance within 5 %.
    """
    g_date_str = (garmin_act.get("startTimeLocal") or "")[:10]
    g_dur      = garmin_act.get("duration") or 0        # seconds
    g_dist     = garmin_act.get("distance")  or 0       # meters

    if not g_date_str:
        return False
    try:
        g_date = datetime.strptime(g_date_str, "%Y-%m-%d")
    except ValueError:
        return False

    for s in strava_acts:
        s_date_str = (s.get("start_date") or s.get("start_date_local") or "")[:10]
        if not s_date_str:
            continue
        try:
            s_date = datetime.strptime(s_date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if abs((g_date - s_date).days) > 1:
            continue

        s_dur  = s.get("moving_time") or 0               # seconds
        s_dist = s.get("distance")    or 0                # meters

        dur_ok  = g_dur  and s_dur  and abs(g_dur  - s_dur)  < 180              # 3 min
        dist_ok = g_dist and s_dist and abs(g_dist - s_dist) / max(g_dist, 1) < 0.05  # 5 %

        if dur_ok or dist_ok:
            return True

    return False


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
        msg = body.get("message") or body.get("error") or f"HTTP {resp.status_code}"
        # Enrich with field-level detail from Strava's errors array
        errs = body.get("errors", [])
        if errs:
            fields = ", ".join(e.get("field", "") for e in errs if e.get("field"))
            if fields:
                msg = f"{msg} ({fields})"
        if resp.status_code == 401 or "authorization" in msg.lower():
            msg += " — reconnect Strava in ⚙️ Settings to grant activity:write scope"
        body["error"] = msg
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



@st.cache_data(show_spinner=False)
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


def _activity_card(act: Dict, in_strava: Optional[bool] = None) -> None:
    """Render one activity preview card (checkbox state stored in session_state).

    in_strava: True = already in Strava, False = not yet, None = unknown (no check done).
    """
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

    # Border color hints at Strava status
    if in_strava is True:
        badge_html = (
            '<span style="background:#22c55e22;color:#22c55e;border:1px solid #22c55e55;'
            'border-radius:10px;padding:1px 8px;font-size:0.75rem;margin-left:6px">'
            '✅ Already on Strava</span>'
        )
    elif in_strava is False:
        badge_html = (
            '<span style="background:#3b82f622;color:#60a5fa;border:1px solid #3b82f655;'
            'border-radius:10px;padding:1px 8px;font-size:0.75rem;margin-left:6px">'
            '⬆️ Not on Strava</span>'
        )
    else:
        badge_html = ""

    with st.container(border=True):
        left, right = st.columns([3, 1])

        with left:
            # key= alone is correct — session_state is pre-initialised in
            # _render_setup. Passing value= alongside key= causes Streamlit
            # to reset the state on rerenders, selecting the wrong activity.
            st.checkbox(label=f"**{icon} {name}**", key=f"chk_{aid}")
            st.markdown(
                f'<span style="font-size:0.8rem;color:#94a3b8">{atype or "Activity"}  ·  {date_str}</span>'
                f'{badge_html}',
                unsafe_allow_html=True,
            )
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

        # Fetch Strava activities for the same range to detect duplicates
        strava_status_text = st.empty()
        strava_status_text.caption("Checking which activities are already on Strava…")
        strava_acts = _fetch_strava_for_range(start_str, end_str)
        strava_status_text.empty()

        # Build match map: activityId → bool (True = already in Strava)
        matches: Dict[int, bool] = {}
        for a in acts:
            aid = a.get("activityId")
            matches[aid] = _match_in_strava(a, strava_acts)

        st.session_state.sync_activities  = acts
        st.session_state.sync_date_range  = (start_str, end_str)
        st.session_state.sync_matches     = matches
        # Default: nothing selected — user picks manually
        for a in acts:
            st.session_state[f"chk_{a.get('activityId')}"] = False
        st.rerun()


# ── Stage 2: Preview & Export ─────────────────────────────────────────────────

def _render_preview(activities: List[Dict]) -> None:
    start_str, end_str = st.session_state.get("sync_date_range", ("?", "?"))
    matches: Dict[int, bool] = st.session_state.get("sync_matches", {})

    n_in_strava  = sum(1 for a in activities if matches.get(a.get("activityId")))
    n_missing    = len(activities) - n_in_strava
    has_matches  = bool(matches)

    # Header row
    hdr, back = st.columns([5, 1])
    hdr.markdown(f"#### {len(activities)} activities  ·  {start_str} → {end_str}")
    with back:
        if st.button("← Change range", width='stretch'):
            _clear_preview_state(activities)
            st.rerun()

    # Strava status overview
    if has_matches:
        s1, s2 = st.columns(2)
        s1.markdown(
            f'<div style="background:#22c55e15;border:1px solid #22c55e44;border-radius:8px;'
            f'padding:8px 12px;font-size:0.85rem">'
            f'✅ <strong style="color:#22c55e">{n_in_strava}</strong> already on Strava</div>',
            unsafe_allow_html=True,
        )
        s2.markdown(
            f'<div style="background:#3b82f615;border:1px solid #3b82f644;border-radius:8px;'
            f'padding:8px 12px;font-size:0.85rem">'
            f'⬆️ <strong style="color:#60a5fa">{n_missing}</strong> not yet on Strava</div>',
            unsafe_allow_html=True,
        )
        st.markdown("")

    # ── View filter + search ──────────────────────────────────────────────────
    view = st.session_state.get("sync_view_filter", "all")

    def _set_view_all():
        st.session_state["sync_view_filter"] = "all"

    def _set_view_missing():
        st.session_state["sync_view_filter"] = "missing"

    def _sel_visible():
        """Select all activities currently visible (respects view filter + search)."""
        _view = st.session_state.get("sync_view_filter", "all")
        _m    = st.session_state.get("sync_matches", {})
        _q    = st.session_state.get("sync_search", "").strip().lower()
        for a in st.session_state.get("sync_activities", []):
            aid = a.get("activityId")
            if _view == "missing" and _m.get(aid, False):
                continue
            if _q:
                name  = (a.get("activityName") or "").lower()
                atype = (a.get("activityType") or {}).get("typeKey", "").lower()
                if _q not in name and _q not in atype:
                    continue
            st.session_state[f"chk_{aid}"] = True

    def _desel_all():
        for a in st.session_state.get("sync_activities", []):
            st.session_state[f"chk_{a.get('activityId')}"] = False

    # Search + view-filter buttons on one row
    c_search, c_vf1, c_vf2 = st.columns([3, 1, 1])
    with c_search:
        search_q = st.text_input(
            "🔍 Search",
            placeholder="Activity name or sport type…",
            key="sync_search",
            label_visibility="collapsed",
        )
    with c_vf1:
        st.button(
            "All", key="vf_all", width='stretch', on_click=_set_view_all,
            type="primary" if view == "all" else "secondary",
        )
    with c_vf2:
        st.button(
            "Missing only", key="vf_missing", width='stretch', on_click=_set_view_missing,
            type="primary" if view == "missing" else "secondary",
            help="Show only activities not yet on Strava",
            disabled=not has_matches,
        )
    q = search_q.strip().lower()

    # Apply view filter, then search filter
    if view == "missing" and has_matches:
        view_acts = [a for a in activities if not matches.get(a.get("activityId"), False)]
    else:
        view_acts = activities

    if q:
        visible_acts = [
            a for a in view_acts
            if q in (a.get("activityName") or "").lower()
            or q in (a.get("activityType") or {}).get("typeKey", "").lower()
        ]
    else:
        visible_acts = view_acts

    # Selection row
    n_sel = sum(1 for a in activities if st.session_state.get(f"chk_{a.get('activityId')}", False))
    sc1, sc2, sc3 = st.columns([4, 1, 1])
    sc1.caption(
        f"**{n_sel}** selected  ·  showing **{len(visible_acts)}** of {len(activities)}"
    )
    with sc2:
        st.button("Select visible", key="sel_visible_btn", width='stretch', on_click=_sel_visible)
    with sc3:
        st.button("Deselect all", key="desel_all_btn", width='stretch', on_click=_desel_all)

    st.divider()

    # Activity cards
    for act in visible_acts:
        aid = act.get("activityId")
        _activity_card(act, in_strava=matches.get(aid) if has_matches else None)

    if not visible_acts:
        if q:
            st.info(f"No activities found matching '{search_q}'.")
        elif view == "missing":
            st.success("All activities in this range are already on Strava! 🎉")

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
    st.session_state.pop("sync_matches", None)
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
        st.warning("Garmin not connected. Open ⚙️ **Settings** to connect.")
        return
    if not strava_connected():
        st.warning("Strava not connected. Open ⚙️ **Settings** to connect.")
        return

    activities: List[Dict] = st.session_state.get("sync_activities", [])

    if not activities:
        _render_setup()
    else:
        _render_preview(activities)
