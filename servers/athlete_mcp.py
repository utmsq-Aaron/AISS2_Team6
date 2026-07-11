"""Athlete — native FastMCP server (Streamable HTTP).

The structured athlete store + the DETERMINISTIC training-math layer:

  • race goal (date, distance, target time) and training preferences
  • personal timeline (injuries, illnesses, races, notes) as CRUD events
  • heart-rate and pace zones — computed from numbers the caller supplies
    (Garmin HR values, a recent Strava race), never estimated by an LLM
  • the training plan (phases → weeks → workouts) with hard guardrails:
    ramp-rate cap, taper check, injury-window check

Design rule (see docs/mcp-architecture.md and the coach concept): everything
computable lives HERE as plain arithmetic the user can re-check; the coach
agent only chooses workouts and explains them. This server calls no upstream
API — the coach feeds it data it fetched from the strava/garmin servers.

Multi-tenant: the acting user rides on the ``X-FitDash-User`` connection
header (set per request by the API adapter / the agent layer), mirroring the
calendar server's Authorization pattern — identity is NEVER a tool argument
the model could fill in. Falls back to ``ATHLETE_DEFAULT_USER`` for
single-user dev setups.

Run locally:   python -m servers.athlete_mcp
Endpoint:      http://127.0.0.1:8109/mcp   (override host/port via env)

Storage: data/user_memory/<slug>/athlete.json (same per-user tree and
cross-process lock as the goal store).
"""

from __future__ import annotations

import math
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from core import jsonstore

load_dotenv()

HOST = os.getenv("ATHLETE_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("ATHLETE_MCP_PORT", "8109"))

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "data" / "user_memory"

# Guardrails — hard-coded sport science, not model output.
MAX_WEEKLY_RAMP = 0.08          # ≤ +8 % volume week over week (non-cutback weeks)
CUTBACK_EVERY = 4               # every 4th week is a recovery week
CUTBACK_FACTOR = 0.70           # cutback week ≈ 70 % of the previous build week
RIEGEL_EXPONENT = 1.06          # Riegel (1981) endurance prediction exponent

EVENT_TYPES = ("injury", "illness", "race", "note")

mcp = FastMCP(
    "athlete",
    instructions=(
        "Structured athlete state: race goal, personal timeline (injuries/races), "
        "deterministically computed HR/pace zones, and the training plan with "
        "guardrail validation. Read the overview first; compute zones from real "
        "Garmin/Strava numbers; scaffold_plan gives the deterministic week/phase "
        "skeleton a coach fills with workouts; save_plan enforces the guardrails."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
)


# ── identity + storage ─────────────────────────────────────────────────────────

def _user() -> str:
    """Acting user from the X-FitDash-User connection header (never a tool arg)."""
    try:
        ctx = mcp.get_context()
        request = ctx.request_context.request
        u = (request.headers.get("x-fitdash-user", "") if request else "").strip().lower()
        if u:
            return u
    except Exception:  # noqa: BLE001 — no HTTP context (tests)
        pass
    return os.getenv("ATHLETE_DEFAULT_USER", "").strip().lower() or "anon"


def _path(user: str) -> Path:
    return _DATA_DIR / jsonstore.slugify(user) / "athlete.json"


def _load(user: str) -> Dict[str, Any]:
    doc = jsonstore.read_json(_path(user))
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("profile", {})
    doc.setdefault("timeline", [])
    doc.setdefault("zones", {})
    doc.setdefault("plan", None)
    return doc


def _save(user: str, doc: Dict[str, Any]) -> None:
    doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
    path = _path(user)
    with jsonstore.flock(path):
        jsonstore.atomic_write(path, doc)


def _parse_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(s).strip()[:10])
    except (ValueError, TypeError):
        return None


def _parse_hms(s: str) -> Optional[int]:
    """'1:45:00' / '45:30' / '102' (minutes) → seconds."""
    s = str(s or "").strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = (int(p) for p in parts)
            return h * 3600 + m * 60 + sec
        if len(parts) == 2:
            m, sec = (int(p) for p in parts)
            return m * 60 + sec
        return int(float(s) * 60)          # bare number = minutes
    except ValueError:
        return None


def _fmt_secs(total: Optional[float]) -> Optional[str]:
    if total is None:
        return None
    total = int(round(total))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_pace(sec_per_km: Optional[float]) -> Optional[str]:
    if sec_per_km is None:
        return None
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}:{s:02d}/km"


# ── deterministic math ─────────────────────────────────────────────────────────

def _riegel(known_dist_km: float, known_secs: float, target_dist_km: float) -> float:
    """Riegel (1981): t2 = t1 · (d2/d1)^1.06 — race-time prediction."""
    return known_secs * (target_dist_km / known_dist_km) ** RIEGEL_EXPONENT


def _hr_zones(max_hr: Optional[int], threshold_hr: Optional[int],
              resting_hr: Optional[int]) -> Optional[Dict[str, Any]]:
    """Five HR zones. Preferred basis: lactate-threshold HR (Friel's percentages);
    fallback: %HRmax. Pure arithmetic, basis recorded for transparency."""
    def band(lo: float, hi: float, ref: int) -> List[int]:
        return [int(round(ref * lo)), int(round(ref * hi))]

    if threshold_hr:
        z = {"Z1": band(0.00, 0.85, threshold_hr), "Z2": band(0.85, 0.89, threshold_hr),
             "Z3": band(0.90, 0.94, threshold_hr), "Z4": band(0.95, 0.99, threshold_hr),
             "Z5": [threshold_hr, max_hr or int(round(threshold_hr * 1.10))]}
        basis = f"threshold_hr={threshold_hr} (Friel %LTHR)"
    elif max_hr:
        z = {"Z1": band(0.50, 0.60, max_hr), "Z2": band(0.60, 0.70, max_hr),
             "Z3": band(0.70, 0.80, max_hr), "Z4": band(0.80, 0.90, max_hr),
             "Z5": band(0.90, 1.00, max_hr)}
        basis = f"max_hr={max_hr} (%HRmax)"
    else:
        return None
    z["Z1"][0] = max(z["Z1"][0], resting_hr or 0)
    return {"bands_bpm": z, "basis": basis}


def _pace_zones(race_dist_km: Optional[float], race_secs: Optional[int]) -> Optional[Dict[str, Any]]:
    """Five pace zones from ONE recent race result.

    Threshold pace = the pace of a ~60-minute all-out race, obtained by inverting
    Riegel from the given result. Zone bands as multiples of threshold pace
    (Daniels-style approximations, documented so they can be re-checked).
    """
    if not race_dist_km or not race_secs:
        return None
    # distance runnable in 60 min: 60·60 = t1 · (d/d1)^1.06  →  d = d1·(3600/t1)^(1/1.06)
    d60 = race_dist_km * (3600.0 / race_secs) ** (1.0 / RIEGEL_EXPONENT)
    thr = 3600.0 / d60                                     # sec per km at threshold
    mult = {"Z1": (1.24, 1.40), "Z2": (1.14, 1.24), "Z3": (1.06, 1.14),
            "Z4": (0.99, 1.06), "Z5": (0.92, 0.99)}
    bands = {z: [_fmt_pace(thr * hi), _fmt_pace(thr * lo)] for z, (lo, hi) in mult.items()}
    return {
        "bands_pace": bands,                               # [slow bound, fast bound]
        "threshold_pace": _fmt_pace(thr),
        "basis": f"race {race_dist_km} km in {_fmt_secs(race_secs)} → Riegel(exp {RIEGEL_EXPONENT})",
    }


def _phase_split(n_weeks: int) -> List[str]:
    """Base/Build/Peak/Taper split over n weeks — proportions, minimums enforced."""
    if n_weeks <= 0:
        return []
    taper = 2 if n_weeks >= 10 else 1
    peak = max(1, round(n_weeks * 0.15)) if n_weeks >= 6 else (1 if n_weeks >= 4 else 0)
    build = max(1, round(n_weeks * 0.35)) if n_weeks >= 4 else max(0, n_weeks - taper - peak - 1)
    base = n_weeks - build - peak - taper
    if base < 0:                                           # very short runway
        build = max(0, build + base)
        base = 0
    return ["base"] * base + ["build"] * build + ["peak"] * peak + ["taper"] * taper


def _week_targets(n_weeks: int, phases: List[str], start_km: float) -> List[float]:
    """Weekly volume targets: +6 %/week (inside the 8 % guardrail), every 4th week
    a cutback, taper weeks step down toward the race."""
    targets: List[float] = []
    vol = start_km
    peak_vol = start_km
    for i, ph in enumerate(phases):
        if ph == "taper":
            remaining = len(phases) - i
            vol = peak_vol * (0.60 if remaining >= 2 else 0.40)
            targets.append(round(vol, 1))
            continue
        if i > 0 and (i + 1) % CUTBACK_EVERY == 0:
            targets.append(round(vol * CUTBACK_FACTOR, 1))  # cutback, ramp continues after
            continue
        if i > 0:
            vol = vol * 1.06
        peak_vol = max(peak_vol, vol)
        targets.append(round(vol, 1))
    return targets


def _blocked_windows(timeline: List[dict]) -> List[dict]:
    return [e for e in timeline if e.get("type") in ("injury", "illness")]


def _week_overlaps_event(week_start: date, event: dict) -> bool:
    ev_start = _parse_date(event.get("start_date", "")) or date.min
    ev_end = _parse_date(event.get("end_date", "")) or ev_start
    week_end = week_start + timedelta(days=6)
    return ev_start <= week_end and ev_end >= week_start


def _validate_plan(plan: Dict[str, Any], timeline: List[dict]) -> List[str]:
    """Hard guardrails. Returns human-readable violations; empty = plan is legal."""
    violations: List[str] = []
    # Drop empty stub weeks (an LLM occasionally appends {}), then require the
    # structural minimum on every remaining week.
    weeks = [w for w in (plan.get("weeks") or [])
             if isinstance(w, dict) and (w.get("start_date") or w.get("phase") or w.get("workouts"))]
    plan["weeks"] = weeks
    if not weeks:
        return ["plan has no weeks"]
    for i, w in enumerate(weeks):
        for field in ("start_date", "phase", "target_km"):
            if not w.get(field):
                violations.append(f"week {i + 1}: missing '{field}'")
    if violations:
        return violations

    # Ramp baseline = the last BUILD week: a cutback week neither gets checked
    # (volume drops by design) nor lowers the baseline (the ramp resumes from
    # the pre-cutback level, it doesn't re-ramp +8% steps from the dip).
    baseline_km: Optional[float] = None
    for i, w in enumerate(weeks):
        km = float(w.get("target_km") or 0)
        phase = (w.get("phase") or "").lower()
        cutback = bool(w.get("cutback"))
        if cutback or phase == "taper":
            continue_baseline = False
        else:
            continue_baseline = True
            if baseline_km and baseline_km > 0:
                ramp = (km - baseline_km) / baseline_km
                if ramp > MAX_WEEKLY_RAMP + 1e-6:
                    violations.append(
                        f"week {i + 1}: volume ramp +{ramp * 100:.0f}% exceeds the "
                        f"+{MAX_WEEKLY_RAMP * 100:.0f}% cap ({baseline_km} → {km} km)")
        if continue_baseline:
            baseline_km = km

        ws = _parse_date(w.get("start_date", ""))
        if ws:
            for ev in _blocked_windows(timeline):
                if _week_overlaps_event(ws, ev):
                    blocked = [s.lower() for s in (ev.get("blocked_sports") or [])]
                    for wo in w.get("workouts") or []:
                        sport = (wo.get("sport") or "run").lower()
                        if not blocked or sport in blocked:
                            violations.append(
                                f"week {i + 1}: workout '{wo.get('title', '?')}' ({sport}) falls "
                                f"inside {ev.get('type')} window '{ev.get('title', '?')}' "
                                f"({ev.get('start_date')}–{ev.get('end_date') or 'open'})")

    phases = [(w.get("phase") or "").lower() for w in weeks]
    if "taper" not in phases:
        violations.append("plan has no taper phase before the race")
    else:
        taper_kms = [float(w.get("target_km") or 0) for w in weeks if (w.get("phase") or "").lower() == "taper"]
        build_max = max((float(w.get("target_km") or 0) for w in weeks
                         if (w.get("phase") or "").lower() != "taper"), default=0)
        if build_max and taper_kms and max(taper_kms) > 0.75 * build_max:
            violations.append("taper volume exceeds 75% of peak volume — not a real taper")
    return violations


# ── tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_athlete_overview() -> Dict[str, Any]:
    """The athlete's full structured state in one read — ALWAYS call this first.

    Returns the race goal (with days_to_race and current plan week), training
    preferences, the personal timeline (injuries/illnesses/races/notes), the
    computed HR/pace zones (with the formula basis), a race-time prognosis for
    the goal distance (Riegel, from the race result zones were computed from),
    and the training plan summary if one exists.
    """
    user = _user()
    doc = _load(user)
    profile = doc.get("profile") or {}
    race = profile.get("race") or {}
    out: Dict[str, Any] = {
        "user": user,
        "profile": profile,
        "timeline": doc.get("timeline") or [],
        "zones": doc.get("zones") or {},
    }
    rd = _parse_date(race.get("date", ""))
    if rd:
        out["days_to_race"] = (rd - date.today()).days
    plan = doc.get("plan")
    if plan:
        weeks = plan.get("weeks") or []
        cur = None
        for i, w in enumerate(weeks):
            ws = _parse_date(w.get("start_date", ""))
            if ws and ws <= date.today() <= ws + timedelta(days=6):
                cur = i + 1
                break
        out["plan"] = {**plan, "current_week": cur, "n_weeks": len(weeks)}
    else:
        out["plan"] = None
    # prognosis: Riegel from the race the pace zones were computed from
    z = (doc.get("zones") or {}).get("pace_source") or {}
    if rd and race.get("distance_km") and z.get("distance_km") and z.get("time_secs"):
        pred = _riegel(float(z["distance_km"]), float(z["time_secs"]), float(race["distance_km"]))
        out["prognosis"] = {
            "predicted_time": _fmt_secs(pred),
            "target_time": race.get("target_time"),
            "on_track": (_parse_hms(race.get("target_time", "")) or 0) >= pred
            if race.get("target_time") else None,
            "basis": z.get("label") or "recent race (Riegel)",
        }
    return out


@mcp.tool()
def set_race_goal(race_name: str, race_date: str, distance_km: float,
                  target_time: str = "", weekly_sessions: int = 4,
                  preferred_days: str = "") -> Dict[str, Any]:
    """Set (or replace) the athlete's structured race goal.

    Args:
        race_name: e.g. "Baden-Marathon Halbmarathon".
        race_date: ISO date "YYYY-MM-DD" — must be in the future.
        distance_km: race distance in km (21.1 for a half marathon).
        target_time: goal finish time "H:MM:SS" or "MM:SS" (optional).
        weekly_sessions: how many training sessions per week fit the athlete's life.
        preferred_days: optional comma-separated weekdays, e.g. "Tue,Thu,Sat,Sun".
    """
    rd = _parse_date(race_date)
    if not rd:
        return {"error": f"race_date '{race_date}' is not an ISO date (YYYY-MM-DD)"}
    if rd <= date.today():
        return {"error": f"race_date {race_date} is not in the future"}
    if not distance_km or distance_km <= 0:
        return {"error": "distance_km must be > 0"}
    if target_time and _parse_hms(target_time) is None:
        return {"error": f"target_time '{target_time}' is not H:MM:SS / MM:SS"}
    user = _user()
    doc = _load(user)
    doc["profile"]["race"] = {
        "name": str(race_name).strip(), "date": rd.isoformat(),
        "distance_km": float(distance_km),
        "target_time": str(target_time).strip() or None,
    }
    doc["profile"]["weekly_sessions"] = max(1, min(int(weekly_sessions or 4), 14))
    if preferred_days:
        doc["profile"]["preferred_days"] = [d.strip() for d in preferred_days.split(",") if d.strip()]
    doc["plan"] = None                                     # a new goal invalidates the old plan
    _save(user, doc)
    return {"ok": True, "race": doc["profile"]["race"],
            "days_to_race": (rd - date.today()).days,
            "note": "existing plan cleared — scaffold and save a new one"}


@mcp.tool()
def add_timeline_event(event_type: str, title: str, start_date: str,
                       end_date: str = "", severity: str = "",
                       blocked_sports: str = "") -> Dict[str, Any]:
    """Record a timeline event: an injury, illness, past/upcoming race, or note.

    Injuries and illnesses become CONSTRAINTS: save_plan rejects workouts of the
    blocked sports inside the event window, and the coach must plan around them.

    Args:
        event_type: one of "injury", "illness", "race", "note".
        title: short label, e.g. "Sprunggelenk verstaucht".
        start_date: ISO date the window starts.
        end_date: ISO date it ends — empty = open-ended (still active).
        severity: optional free label ("mild", "severe", …).
        blocked_sports: comma-separated sports the event rules out, e.g.
            "run,hike" — empty means ALL sports are blocked while active.
    """
    if event_type not in EVENT_TYPES:
        return {"error": f"event_type must be one of {EVENT_TYPES}"}
    if not _parse_date(start_date):
        return {"error": f"start_date '{start_date}' is not an ISO date"}
    if end_date and not _parse_date(end_date):
        return {"error": f"end_date '{end_date}' is not an ISO date"}
    user = _user()
    doc = _load(user)
    ev = {
        "id": uuid.uuid4().hex[:12], "type": event_type, "title": str(title).strip(),
        "start_date": start_date[:10], "end_date": (end_date or "")[:10] or None,
        "severity": str(severity).strip() or None,
        "blocked_sports": [s.strip().lower() for s in blocked_sports.split(",") if s.strip()],
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    doc["timeline"].append(ev)
    doc["timeline"].sort(key=lambda e: e.get("start_date") or "")
    _save(user, doc)
    return {"ok": True, "event": ev}


@mcp.tool()
def delete_timeline_event(event_id: str) -> Dict[str, Any]:
    """Remove a timeline event by its id (from get_athlete_overview)."""
    user = _user()
    doc = _load(user)
    before = len(doc["timeline"])
    doc["timeline"] = [e for e in doc["timeline"] if e.get("id") != event_id]
    if len(doc["timeline"]) == before:
        return {"error": f"no timeline event with id '{event_id}'"}
    _save(user, doc)
    return {"ok": True, "deleted": event_id}


@mcp.tool()
def compute_zones(max_hr: int = 0, threshold_hr: int = 0, resting_hr: int = 0,
                  race_distance_km: float = 0, race_time: str = "",
                  race_label: str = "") -> Dict[str, Any]:
    """Compute and store HR + pace zones DETERMINISTICALLY from real measurements.

    Fetch the inputs from the garmin/strava servers first (max HR, resting HR,
    lactate threshold HR if available; the athlete's best recent race or PR) and
    pass them here — this tool only does the arithmetic (Friel %LTHR or %HRmax
    bands; Riegel-derived threshold pace with Daniels-style multiples) and
    records the basis so every number is re-checkable. Never guess the inputs.

    Args:
        max_hr: maximum heart rate in bpm (Garmin profile / observed max).
        threshold_hr: lactate-threshold HR in bpm, if Garmin provides it (preferred).
        resting_hr: resting HR in bpm (optional, floors Z1).
        race_distance_km: distance of a recent ALL-OUT race/PR effort in km.
        race_time: its finish time "H:MM:SS" or "MM:SS".
        race_label: where the result came from, e.g. "Strava 10k PR 2026-05-03".
    """
    hr = _hr_zones(int(max_hr) or None, int(threshold_hr) or None, int(resting_hr) or None)
    secs = _parse_hms(race_time)
    pace = _pace_zones(float(race_distance_km) or None, secs)
    if not hr and not pace:
        return {"error": "need max_hr/threshold_hr for HR zones and/or "
                         "race_distance_km+race_time for pace zones"}
    user = _user()
    doc = _load(user)
    zones: Dict[str, Any] = {"computed_at": datetime.utcnow().isoformat() + "Z"}
    if hr:
        zones["hr"] = hr
    if pace:
        zones["pace"] = pace
        zones["pace_source"] = {"distance_km": float(race_distance_km),
                                "time_secs": secs, "label": race_label or None}
    doc["zones"] = zones
    _save(user, doc)
    return {"ok": True, "zones": zones}


@mcp.tool()
def scaffold_plan(current_weekly_km: float) -> Dict[str, Any]:
    """The DETERMINISTIC plan skeleton the coach fills with workouts.

    From the stored race goal and today's date it derives: number of full
    training weeks, the base/build/peak/taper phase of each week, each week's
    target volume (+6 %/week ramp inside the +8 % guardrail, every 4th week a
    cutback at 70 %, taper stepping down to the race) and the athlete's
    sessions-per-week. Fill each week's ``workouts`` and pass the result to
    save_plan — do NOT invent your own week structure or volumes.

    Args:
        current_weekly_km: the athlete's CURRENT typical weekly volume in km
            (from recent Strava weeks) — the ramp starts here, never above it.
    """
    user = _user()
    doc = _load(user)
    race = (doc.get("profile") or {}).get("race") or {}
    rd = _parse_date(race.get("date", ""))
    if not rd:
        return {"error": "no race goal set — call set_race_goal first"}
    if not current_weekly_km or current_weekly_km <= 0:
        return {"error": "current_weekly_km must be > 0 (read it from recent Strava weeks)"}

    next_monday = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
    n_weeks = max(0, (rd - next_monday).days // 7)
    if n_weeks < 2:
        return {"error": f"only {n_weeks} full week(s) until the race — too short to plan"}
    phases = _phase_split(n_weeks)
    targets = _week_targets(n_weeks, phases, float(current_weekly_km))
    weeks = []
    for i, (ph, km) in enumerate(zip(phases, targets)):
        ws = next_monday + timedelta(weeks=i)
        weeks.append({
            "week": i + 1, "phase": ph, "start_date": ws.isoformat(),
            "target_km": km, "cutback": (i > 0 and (i + 1) % CUTBACK_EVERY == 0 and ph not in ("taper",)),
            "sessions": (doc.get("profile") or {}).get("weekly_sessions", 4),
            "workouts": [],
        })
    return {
        "race": race, "n_weeks": n_weeks, "weeks": weeks,
        "guardrails": {"max_weekly_ramp_pct": MAX_WEEKLY_RAMP * 100,
                       "cutback_every": CUTBACK_EVERY},
        "timeline_constraints": _blocked_windows(doc.get("timeline") or []),
        "zones": doc.get("zones") or {},
    }


@mcp.tool()
def save_plan(plan: dict) -> Dict[str, Any]:
    """Validate a filled plan against the hard guardrails and store it.

    Pass the scaffold_plan result with each week's ``workouts`` filled in:
    every workout needs {day, title, sport, zone, and either duration_min or
    distance_km}; give intensity as the athlete's OWN zone bands (from the
    stored zones), add a one-sentence ``why`` and, where it came from the
    literature, a ``source``. Rejected plans return the exact violations —
    fix them and call again; nothing is stored on rejection.
    """
    user = _user()
    doc = _load(user)
    if not isinstance(plan, dict):
        return {"error": "plan must be an object"}
    violations = _validate_plan(plan, doc.get("timeline") or [])
    if violations:
        return {"error": "plan violates guardrails", "violations": violations}
    plan = {k: v for k, v in plan.items() if k in
            ("race", "weeks", "n_weeks", "guardrails", "notes", "status")}
    plan["status"] = "active"
    plan["saved_at"] = datetime.utcnow().isoformat() + "Z"
    doc["plan"] = plan
    _save(user, doc)
    n_workouts = sum(len(w.get("workouts") or []) for w in plan.get("weeks") or [])
    return {"ok": True, "n_weeks": len(plan.get("weeks") or []), "n_workouts": n_workouts}


@mcp.tool()
def get_plan() -> Dict[str, Any]:
    """The stored training plan (phases → weeks → workouts), or an error if none."""
    doc = _load(_user())
    return doc.get("plan") or {"error": "no plan stored — scaffold_plan + save_plan first"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
