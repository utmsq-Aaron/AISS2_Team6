"""Garmin → Strava export — download a Garmin activity's FIT file, upload it to Strava.

Talks to garminconnect (token store) and the Strava REST API directly. This flow
deliberately does NOT go through MCP: it is a one-off account-maintenance action,
not something an agent should be able to reach — an upload to Strava is visible to
the user's followers and is not cleanly undoable. Used by api/routers/sync.py.

Duplicate detection is the interesting part: Garmin and Strava disagree on ids, so
`match_in_strava` pairs them heuristically on date + duration/distance. It is
deliberately generous — a false "already there" only skips an upload the user can
retry, while a false "missing" creates a duplicate activity on Strava.
"""

import io
import os
import pathlib
import time
import zipfile
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

TOKEN_STORE = ".tokens"


def garmin_client():
    """Log in to Garmin via cached tokens, falling back to credentials."""
    # GARMIN_MOCK_HEALTH makes api.connections report Garmin as "connected", which
    # is right for the Health page (the mock generates that data) but wrong here:
    # there is no mock FIT file to upload. Say so instead of failing on a login.
    from api.connections import garmin_mock_mode

    if garmin_mock_mode():
        raise RuntimeError(
            "Garmin is in mock mode (GARMIN_MOCK_HEALTH) — there are no real "
            "activities to export. Disable the mock and connect a real Garmin account."
        )

    from garminconnect import Garmin

    has_tokens = (pathlib.Path(TOKEN_STORE) / "garmin_tokens.json").exists()
    try:
        g = Garmin()
        g.login(tokenstore=TOKEN_STORE)
        return g
    except Exception as exc:
        email = os.getenv("GARMIN_EMAIL", "") or ""
        password = os.getenv("GARMIN_PASSWORD", "") or ""
        if email and password:
            try:
                g2 = Garmin(email=email, password=password)
                g2.login(tokenstore=TOKEN_STORE)
                return g2
            except Exception:
                pass
        if not has_tokens:
            raise RuntimeError("Garmin not connected. Connect in Settings → Garmin.") from exc
        raise RuntimeError(f"Garmin login failed: {exc}") from exc


def strava_token() -> str:
    cid = os.getenv("CLIENT_ID")
    csec = os.getenv("CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("CLIENT_ID / CLIENT_SECRET not set in .env")
    from auth.strava_oauth import OAuth2Manager

    return OAuth2Manager(cid, csec).get_valid_access_token()


def fetch_strava_for_range(start_str: str, end_str: str) -> Optional[List[Dict]]:
    """Strava activities in the range, or None if Strava could not be read.

    The distinction matters: an empty list is a legitimate answer ("you logged
    nothing to Strava that week") and means every Garmin activity is genuinely
    missing, while None means we know nothing and must not claim anything is
    missing. Collapsing both into [] told users with an empty Strava account that
    their connection was broken.
    """
    try:
        token = strava_token()
        after = int(datetime.strptime(start_str, "%Y-%m-%d").timestamp())
        before = int((datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1)).timestamp())
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
                # A partial read is still unusable as a "what's missing" baseline:
                # the pages we did not get would look like gaps.
                return None
            batch = resp.json()
            if not batch:
                break
            collected.extend(batch)
            page += 1
            if len(batch) < 200:
                break
        return collected
    except Exception:
        return None


def match_in_strava(garmin_act: Dict, strava_acts: List[Dict]) -> bool:
    """Same calendar date (±1 day) AND (duration within 3 min OR distance within 5%)."""
    g_date_str = (garmin_act.get("startTimeLocal") or "")[:10]
    g_dur = garmin_act.get("duration") or 0
    g_dist = garmin_act.get("distance") or 0
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
        s_dur = s.get("moving_time") or 0
        s_dist = s.get("distance") or 0
        dur_ok = g_dur and s_dur and abs(g_dur - s_dur) < 180
        dist_ok = g_dist and s_dist and abs(g_dist - s_dist) / max(g_dist, 1) < 0.05
        if dur_ok or dist_ok:
            return True
    return False


def normalize_activity(a: Dict, in_strava: Optional[bool] = None) -> Dict:
    aid = a.get("activityId")
    return {
        "id": aid,
        "name": a.get("activityName") or f"Activity {aid}",
        "type": (a.get("activityType") or {}).get("typeKey", ""),
        "date": (a.get("startTimeLocal") or "")[:10],
        "distance_km": round((a.get("distance") or 0) / 1000, 2),
        "duration_s": a.get("duration") or 0,
        "avg_hr": a.get("averageHR"),
        "elevation_m": a.get("elevationGain"),
        "calories": a.get("calories"),
        "start_lat": a.get("startLatitude"),
        "start_lon": a.get("startLongitude"),
        "has_polyline": a.get("hasPolyline", False),
        "in_strava": in_strava,
    }


def fetch_activities(start_str: str, end_str: str) -> Dict:
    """Garmin activities in range, each flagged with its Strava-duplicate status.

    `strava_readable` is False only when Strava could not be read at all — then
    `in_strava` is None everywhere and no caller may call anything "missing". An
    empty but readable Strava is the normal case for a new account and correctly
    marks every Garmin activity as missing.
    """
    g = garmin_client()
    acts = g.get_activities_by_date(start_str, end_str) or []
    strava_acts = fetch_strava_for_range(start_str, end_str)
    readable = strava_acts is not None
    out = [
        normalize_activity(a, match_in_strava(a, strava_acts) if readable else None)
        for a in acts
    ]
    return {
        "activities": out,
        "strava_readable": readable,
        "start": start_str,
        "end": end_str,
    }


def route_coords(activity_id: int) -> List[List[float]]:
    try:
        g = garmin_client()
        d = g.get_activity_details(str(activity_id), maxpoly=500)
        pts = (d.get("geoPolylineDTO") or {}).get("polyline") or []
        return [[p["lat"], p["lon"]] for p in pts if p.get("valid") and p.get("lat") and p.get("lon")]
    except Exception:
        return []


def _download_fit(garmin, activity_id: int) -> tuple[Optional[bytes], str]:
    """The activity's FIT bytes, or (None, reason).

    The reason is carried out rather than swallowed: an expired Garmin session
    and a genuinely file-less activity both used to surface as "skipped — no FIT
    file", so a batch could report forty skips without hinting that re-connecting
    Garmin would fix all of them.
    """
    try:
        from garminconnect import Garmin as _G

        raw = garmin.download_activity(activity_id, dl_fmt=_G.ActivityDownloadFormat.ORIGINAL)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".fit"):
                    return zf.read(name), ""
    except Exception as exc:  # noqa: BLE001 — reported, not silenced
        return None, f"Garmin download failed: {exc}"
    return None, "no FIT file in the Garmin export"


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
        errs = body.get("errors", [])
        if errs:
            fields = ", ".join(e.get("field", "") for e in errs if e.get("field"))
            if fields:
                msg = f"{msg} ({fields})"
        if resp.status_code == 401 or "authorization" in msg.lower():
            msg += " — reconnect Strava in Settings to grant activity:write scope"
        body["error"] = msg
    return body


def _poll_upload(token: str, upload_id: int, timeout: int = 60) -> Dict:
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
        if resp.get("error") or resp.get("activity_id"):
            return resp
        status = (resp.get("status") or "").lower()
        if "ready" in status or "error" in status:
            return resp
        time.sleep(4)
    return resp


def export_one(garmin, token: str, act: Dict) -> Dict:
    """Download one Garmin FIT and upload to Strava. Returns a result dict."""
    aid = act.get("id")
    name = act.get("name") or f"Activity {aid}"
    fit, why = _download_fit(garmin, aid)
    if fit is None:
        return {"status": "skipped", "name": name, "message": why}
    resp = _upload_to_strava(token, fit, name)
    if resp.get("error"):
        return {"status": "error", "name": name, "message": resp["error"]}
    upload_id = resp.get("id")
    if not upload_id:
        return {"status": "error", "name": name, "message": resp.get("message") or "Strava rejected upload"}
    final = _poll_upload(token, upload_id)
    err = final.get("error") or ""
    activity_id = final.get("activity_id")
    if err:
        # Strava phrases this as "<file>.fit duplicate of activity <id>" — matching
        # on "already exists" never fired, so re-running a sync reported every
        # already-uploaded activity as a hard error.
        if "duplicate" in err.lower():
            return {"status": "duplicate", "name": name, "message": "already on Strava"}
        return {"status": "error", "name": name, "message": err}
    if activity_id:
        return {"status": "ok", "name": name, "url": f"https://www.strava.com/activities/{activity_id}"}
    return {"status": "ok", "name": name, "message": "still processing — check Strava shortly"}
