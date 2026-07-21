"""Athlete — native FastMCP server (Streamable HTTP).

The structured athlete store + the DETERMINISTIC training-math layer:

  • race goal (sport run/ride, date, distance, target time) and training preferences
  • personal timeline (injuries, illnesses, races, notes) as CRUD events
  • heart-rate and pace zones — computed from numbers the caller supplies
    (Garmin HR values, a recent Strava race), never estimated by an LLM
  • the GOAL-DRIVEN training plan (phases with content prescriptions → weeks →
    workouts, plus a goal-vs-data feasibility fact block) with hard guardrails:
    ramp-rate cap, taper check, injury-window check, cross-training rules
  • the adaptation loop: record_week_actual (plan-vs-actual monitoring) and
    rescaffold_plan (re-baseline remaining weeks on demonstrated volume)

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

# Guardrails + zone definitions — hard-coded sport science from the German
# textbook corpus, NOT model output. Every number is sourced in docs/trainingsregeln.md.
MAX_WEEKLY_RAMP = 0.08          # ≤ +8 %/Woche — konservative "allmähliche progressive
                               # Belastungssteigerung" (Güllich S.631; Roux-Reizstufenregel).
                               # Weiche Guardrail, kein exakter Buchwert.
CUTBACK_EVERY = 4              # 4-Wochen-Zyklus, Entlastung am Zyklusende (Ferrauti S.295)
CUTBACK_FACTOR = 0.70          # Entlastungswoche ≈ 70 % der vorangehenden Aufbauwoche
HFMAX_AGE_A = 208.0           # HFmax-Schätzung 208 − 0,7·Alter (Tanaka 2001, via Güllich S.771)
HFMAX_AGE_B = 0.7             # NUR Fallback — echte Messung immer bevorzugt

# HF-Zonen (deutsche Bereiche ReKom/GA1/GA2/WSA) als %HFmax und %HFR/Karvonen — Ferrauti S.459.
HR_ZONES_PCT_MAX = {"ReKom": (0.50, 0.60), "GA1": (0.60, 0.80),
                    "GA2": (0.80, 0.90), "WSA": (0.90, 1.00)}
HR_ZONES_PCT_HFR = {"ReKom": (0.35, 0.50), "GA1": (0.50, 0.70),
                    "GA2": (0.70, 0.85), "WSA": (0.85, 1.00)}
# Pace-Zonen als (langsam, schnell)-Faktor × 10-km-Renntempo — Ferrauti Tab. 7.7 (Joch 2004).
PACE_ZONES_FACTOR = {"ReKom": (1.43, 1.33), "GA1": (1.33, 1.18),
                     "GA2": (1.11, 1.05), "WSA": (1.00, 0.95)}

EVENT_TYPES = ("injury", "illness", "race", "note")
MILESTONE_KINDS = ("race", "checkpoint")
MILESTONE_STATUSES = ("pending", "achieved")
GOAL_SPORTS = ("run", "ride")           # main-goal sport, chosen when the goal is set

# Long-run line — the plan's backbone is the weekly KEY SESSION approaching the
# race distance (backward-planned from race day), not a weekly aggregate. The
# athlete runs the race demand at least once before race day. Documented
# engineering conventions (docs/trainingsregeln.md §11), not book constants:
LONG_RUN_FULL_DIST_MAX = 25.0   # up to here the peak long run = the FULL race distance
LONG_RUN_CAP_FACTOR = 0.75      # beyond (marathon+): peak long run ≈ 75 % of the distance
LONG_RUN_START_FACTOR = 0.5     # the line starts at half the anchor and builds to it
SUPPORT_RUN_FACTOR = 0.4        # each supporting run ≈ 40 % of that week's long run
TAPER_LONG_FACTORS = (0.5, 0.3) # taper long runs: 50 % then 30 % of the peak

# Phase content per Ferrauti Tab. 7.10/7.11 (Marathon-Jahres-/Wochenplan): the plan
# is built FOR the goal — each phase prescribes what kind of work serves it, incl.
# where unspecific cross-training (bike/swim/aqua jogging) is appropriate.
PHASE_FOCUS = {
    "base": {
        "label": "Allgemeine Vorbereitung",
        "focus": ("Volume first: raise frequency, then duration (Ferrauti S.188). "
                  "GA1 endurance in the goal sport; unspecific cross-training "
                  "(bike/MTB, swimming, aqua jogging) welcome as GA1/ReKom sessions "
                  "(Ferrauti Tab. 7.10)."),
    },
    "build": {
        "label": "Spezielle Vorbereitung",
        "focus": ("Sport-specific endurance GA1/GA2 in the goal sport (Ferrauti "
                  "Tab. 7.10); cross-training only as ReKom recovery sessions."),
    },
    "peak": {
        "label": "Unmittelbare Wettkampfvorbereitung",
        "focus": ("Race-pace work GA2/WSA — interval protocols per Ferrauti S.58-59 "
                  "and Tab. 7.11; cross-training only as ReKom recovery sessions."),
    },
    "taper": {
        "label": "Taper",
        "focus": "Volume sharply down, intensity kept (Ferrauti S.117).",
    },
}

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


def _migrate_race(profile: Dict[str, Any]) -> None:
    """One-time, transparent migration: the pre-hierarchy schema stored a single
    ``profile.race`` object. Fold it into ``profile.races`` (priority "A") so every
    reader can rely on the list — in memory only; persisted the next time anything
    calls _save (e.g. set_race_goal), never requiring an explicit migration step."""
    old_race = profile.get("race")
    if isinstance(old_race, dict) and old_race and not profile.get("races"):
        migrated = dict(old_race)
        migrated.setdefault("id", uuid.uuid4().hex[:12])
        migrated["priority"] = "A"
        profile["races"] = [migrated]
    profile.pop("race", None)  # always recomputed on read (see get_athlete_overview)
    # Pre-sport-field main goals default to running (in-memory, persisted on next _save).
    for r in profile.get("races") or []:
        if r.get("priority") == "A":
            r.setdefault("sport", "run")


def _load(user: str) -> Dict[str, Any]:
    doc = jsonstore.read_json(_path(user))
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("profile", {})
    doc.setdefault("timeline", [])
    doc.setdefault("zones", {})
    doc.setdefault("plan", None)
    _migrate_race(doc["profile"])
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

def _estimate_hfmax(age: Optional[int]) -> Optional[int]:
    """Fallback HFmax = 208 − 0,7·Alter (Tanaka 2001, via Güllich S.771). Nur wenn keine
    gemessene HFmax vorliegt — Messung ist laut Buch immer genauer."""
    if not age or age <= 0:
        return None
    return int(round(HFMAX_AGE_A - HFMAX_AGE_B * int(age)))


def _hr_zones(max_hr: Optional[int], resting_hr: Optional[int]) -> Optional[Dict[str, Any]]:
    """Deutsche HF-Zonen ReKom/GA1/GA2/WSA als %HFmax; zusätzlich %HFR/Karvonen, sobald eine
    Ruhe-HF vorliegt (Ferrauti S.459 — HFR bei niedriger/moderater Intensität präziser).
    Reine Arithmetik, Basis protokolliert; KEIN Laktat/v4 (nicht messbar aus Garmin/Strava)."""
    if not max_hr:
        return None

    def band_max(lo: float, hi: float) -> List[int]:
        return [int(round(max_hr * lo)), int(round(max_hr * hi))]

    out: Dict[str, Any] = {
        "bands_bpm": {z: band_max(lo, hi) for z, (lo, hi) in HR_ZONES_PCT_MAX.items()},
        "method": "%HFmax",
        "basis": f"max_hr={max_hr} — %HFmax-Bänder ReKom/GA1/GA2/WSA (Ferrauti S.459)",
    }
    if resting_hr and resting_hr < max_hr:
        hrr = max_hr - resting_hr                           # Herzfrequenzreserve

        def band_hfr(lo: float, hi: float) -> List[int]:    # Karvonen: rest + %HFR·HFR
            return [int(round(resting_hr + hrr * lo)), int(round(resting_hr + hrr * hi))]

        out["bands_bpm_hfr"] = {z: band_hfr(lo, hi) for z, (lo, hi) in HR_ZONES_PCT_HFR.items()}
        out["hfr_basis"] = (f"resting_hr={resting_hr} — Karvonen/%HFR "
                            f"(Ferrauti S.459: bei niedriger/moderater Intensität präziser)")
    return out


def _pace_zones(race_dist_km: Optional[float], race_secs: Optional[int]) -> Optional[Dict[str, Any]]:
    """Deutsche Pace-Zonen aus EINEM realen Wettkampf (idealerweise ~10 km): Bänder als
    Faktor × Renntempo nach Ferrauti Tab. 7.7 (Joch 2004). Keine Distanz-Extrapolation."""
    if not race_dist_km or not race_secs:
        return None
    race_pace = race_secs / race_dist_km                   # sec/km — 10-km-Bestleistung als Anker
    bands = {z: [_fmt_pace(race_pace * f_slow), _fmt_pace(race_pace * f_fast)]
             for z, (f_slow, f_fast) in PACE_ZONES_FACTOR.items()}  # [langsam, schnell]
    anchor = "" if abs(race_dist_km - 10) <= 3 else " (Anker idealerweise ~10 km)"
    return {
        "bands_pace": bands,
        "race_pace": _fmt_pace(race_pace),
        "basis": f"Renntempo {_fmt_pace(race_pace)} aus {race_dist_km} km in {_fmt_secs(race_secs)} "
                 f"→ Faktor×Renntempo (Ferrauti Tab. 7.7, Joch 2004){anchor}",
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


def _long_run_anchor(race_dist_km: float) -> float:
    """Peak long run the line builds to: the FULL race distance for goals up to
    ~half-marathon range (the athlete covers the distance once before race day);
    capped at 75 % for longer races (a marathon is never run in full beforehand)."""
    if race_dist_km <= LONG_RUN_FULL_DIST_MAX:
        return float(race_dist_km)
    return round(race_dist_km * LONG_RUN_CAP_FACTOR, 1)


def _run_targets(phases: List[str], race_dist_km: float,
                 start_sessions: int = 2,
                 target_sessions: int = 4) -> tuple[List[float], List[float], List[int], dict]:
    """RUN-BASED, backward-planned skeleton: the deterministic backbone is the
    weekly LONG RUN approaching the race demand, planned backward from race day
    (goal decides, history only informs the coach's conversation):

      • anchor: last growth week's long run = _long_run_anchor(race distance);
      • the line starts at half the anchor and grows in even steps across every
        growth week (non-cutback, non-taper) — steady approach, no jumps;
      • cutback weeks drop the long run to 70 % (Ferrauti S.295), taper weeks
        to 50 %/30 % of the peak (volume down, intensity kept, S.117);
      • frequency still rises first (+1 session/week toward the athlete's
        target — Ferrauti S.83/S.188); each supporting run ≈ 40 % of that
        week's long run, so the week volume DERIVES from the runs.

    Returns (week_target_km, long_run_km, sessions_per_week, line_info)."""
    anchor = _long_run_anchor(race_dist_km)
    start_sessions = max(1, min(int(start_sessions or 2), int(target_sessions or 4)))
    target_sessions = max(start_sessions, int(target_sessions or 4))
    growth_idx = [i for i, ph in enumerate(phases)
                  if ph != "taper" and not (i > 0 and (i + 1) % CUTBACK_EVERY == 0)]
    n_growth = len(growth_idx)
    start_long = anchor * LONG_RUN_START_FACTOR if n_growth > 1 else anchor
    step = (anchor - start_long) / (n_growth - 1) if n_growth > 1 else 0.0
    line = {idx: start_long + k * step for k, idx in enumerate(growth_idx)}

    targets: List[float] = []
    long_runs: List[float] = []
    sessions_list: List[int] = []
    sessions = start_sessions
    prev_long = start_long
    peak_long = anchor
    taper_i = 0
    for i, ph in enumerate(phases):
        if ph == "taper":
            lr = peak_long * TAPER_LONG_FACTORS[min(taper_i, len(TAPER_LONG_FACTORS) - 1)]
            taper_i += 1
        elif i in line:
            lr = line[i]
            if i > 0 and sessions < target_sessions:
                sessions += 1                              # frequency before duration
            prev_long = lr
        else:                                              # cutback week
            lr = prev_long * CUTBACK_FACTOR
        lr = round(lr, 1)
        week_total = round(lr * (1 + SUPPORT_RUN_FACTOR * (sessions - 1)), 1)
        long_runs.append(lr)
        targets.append(week_total)
        sessions_list.append(sessions)
    info = {"anchor_km": round(anchor, 1), "start_long_run_km": round(start_long, 1),
            "step_km": round(step, 2), "growth_weeks": n_growth}
    return targets, long_runs, sessions_list, info


def _blocked_windows(timeline: List[dict]) -> List[dict]:
    return [e for e in timeline if e.get("type") in ("injury", "illness")]


def _feasibility(race: Dict[str, Any], targets: List[float], n_weeks: int,
                 zones: Dict[str, Any], line_info: Optional[dict] = None,
                 sessions: Optional[tuple] = None) -> Dict[str, Any]:
    """Goal-vs-data FACTS, no verdict: what the backward-planned long-run line
    demands vs. where the athlete stands. Pure arithmetic — judging
    achievability (SMART, Ferrauti Tab. 1.2) and confronting the athlete is the
    coach's job (compare the line's entry point with the athlete's real longest
    recent runs from Strava)."""
    sport = race.get("sport", "run")
    goal_dist = float(race.get("distance_km") or 0) or None
    out: Dict[str, Any] = {
        "sport": sport,
        "race_distance_km": goal_dist,
        "n_weeks": n_weeks,
        "start_week_km": targets[0] if targets else None,
        "peak_week_km": max(targets) if targets else None,
        "basis": ("backward-planned long-run line (anchor = race demand, even steps "
                  "across the growth weeks) + frequency-first build-up (Ferrauti "
                  "S.83/S.188) — check the line's ENTRY against the athlete's real "
                  "longest recent runs and the required vs. benchmark pace, then "
                  "talk to the athlete honestly"),
    }
    if line_info:
        out["long_run_line"] = line_info
        # Advisory (documented display threshold, not sport science): with fewer
        # than 4 growth weeks the line cannot approach the distance gradually.
        if line_info.get("growth_weeks", 99) < 4:
            out["warning"] = (
                f"only {line_info['growth_weeks']} growth week(s) before the race — "
                f"the long run cannot approach the race demand gradually (line steps "
                f"of {line_info.get('step_km')} km). Confront the athlete openly and "
                f"discuss options (later race / adjusted goal / finish-focus strategy).")
    if sessions:
        out["start_sessions_per_week"], out["target_sessions_per_week"] = sessions
    target_secs = _parse_hms(race.get("target_time", "")) if race.get("target_time") else None
    if target_secs and goal_dist:
        out["required_pace"] = _fmt_pace(target_secs / goal_dist)
    ps = (zones or {}).get("pace_source") or {}
    if ps.get("distance_km") and ps.get("time_secs") and ps.get("sport", "run") == sport:
        bench_dist = float(ps["distance_km"])
        out["benchmark"] = {
            "label": ps.get("label") or f"{bench_dist} km",
            "distance_km": bench_dist,
            "pace": _fmt_pace(float(ps["time_secs"]) / bench_dist),
        }
    return out


# ── main goal + milestones ──────────────────────────────────────────────────────
# ONE main goal (priority "A") drives the plan. Everything else in this same list
# is a MILESTONE on the way there — either a real tune-up/minor race (kind="race")
# or a non-race training checkpoint (kind="checkpoint", e.g. "first 15 km long
# run"). Milestones never alter the plan's deterministic volumes; scaffold_plan
# only annotates which week they fall in so the coach can plan gently around a
# race-kind milestone. This is the app's ONE goal/progress system.

def _races(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((doc.get("profile") or {}).get("races") or [])


def _a_race(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The single priority-A entry that drives the training plan, if any."""
    return next((r for r in _races(doc) if r.get("priority") == "A"), None)


def _milestones(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every non-main entry — race tune-ups and non-race checkpoints alike."""
    return [r for r in _races(doc) if r.get("priority") != "A"]


def _milestones_in_week(milestones: List[Dict[str, Any]], week_start: date) -> List[Dict[str, Any]]:
    week_end = week_start + timedelta(days=6)
    out = []
    for m in milestones:
        md = _parse_date(m.get("date", ""))
        if md and week_start <= md <= week_end:
            out.append({"id": m.get("id"), "name": m.get("name"), "date": m.get("date"),
                        "kind": m.get("kind", "race")})
    return out


def _week_overlaps_event(week_start: date, event: dict) -> bool:
    ev_start = _parse_date(event.get("start_date", "")) or date.min
    ev_end = _parse_date(event.get("end_date", "")) or ev_start
    week_end = week_start + timedelta(days=6)
    return ev_start <= week_end and ev_end >= week_start


def _validate_plan(plan: Dict[str, Any], timeline: List[dict],
                   main_sport: str = "run") -> List[str]:
    """Hard guardrails. Returns human-readable violations; empty = plan is legal.

    ``target_km`` counts the GOAL SPORT only; workouts in another sport are
    cross-training (need a duration) and never enter the ramp math. The ramp
    baseline prefers a week's recorded ``actual`` volume over its target — the
    demonstrated load is the true baseline once a week has been reviewed."""
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
    baseline_sessions: Optional[int] = None
    for i, w in enumerate(weeks):
        km = float(w.get("target_km") or 0)
        phase = (w.get("phase") or "").lower()
        cutback = bool(w.get("cutback"))
        sessions = int(w.get("sessions") or 0) or None
        actual = w.get("actual") if isinstance(w.get("actual"), dict) else None
        actual_km = float(actual["distance_km"]) if actual and actual.get("distance_km") else None
        long_run = float(w.get("long_run_km") or 0) or None
        if cutback or phase == "taper":
            continue_baseline = False
        else:
            continue_baseline = True
            # Sessions rise one per week, never more (frequency before duration —
            # Ferrauti S.83/S.188).
            if sessions and baseline_sessions and sessions > baseline_sessions + 1:
                violations.append(
                    f"week {i + 1}: sessions jump {baseline_sessions} → {sessions} "
                    f"— raise frequency one session per week (Ferrauti S.83)")
            # LEGACY weeks without a long-run line keep the old %-cap; line-based
            # weeks are governed by the line itself (the biggest run must match
            # long_run_km below — the weekly sum then follows from the runs).
            if long_run is None and baseline_km and baseline_km > 0:
                factor = 1 + MAX_WEEKLY_RAMP
                if sessions and baseline_sessions and sessions > baseline_sessions:
                    factor = max(factor, min(sessions, baseline_sessions + 1) / baseline_sessions)
                if km > baseline_km * factor + 0.10501:
                    ramp = (km - baseline_km) / baseline_km
                    violations.append(
                        f"week {i + 1}: volume ramp +{ramp * 100:.0f}% exceeds the "
                        f"allowed step (+{(factor - 1) * 100:.0f}%) "
                        f"({baseline_km} → {km} km)")
        if continue_baseline:
            # A reviewed week's demonstrated volume beats its target as baseline.
            baseline_km = actual_km if actual_km is not None else km
            baseline_sessions = sessions or baseline_sessions

        goal_sport_km = 0.0
        goal_sport_all_have_km = True
        n_goal_workouts = 0
        biggest_goal_run = 0.0
        for wo in w.get("workouts") or []:
            wo_sport = (wo.get("sport") or main_sport).lower()
            if wo_sport != main_sport and not (wo.get("duration_min") or wo.get("distance_km")):
                violations.append(
                    f"week {i + 1}: cross-training workout '{wo.get('title', '?')}' "
                    f"({wo_sport}) needs duration_min (cross-training is planned by "
                    f"duration, not km)")
            if wo_sport == main_sport:
                n_goal_workouts += 1
                if wo.get("distance_km"):
                    goal_sport_km += float(wo["distance_km"])
                    biggest_goal_run = max(biggest_goal_run, float(wo["distance_km"]))
                else:
                    goal_sport_all_have_km = False

        # Internal consistency (engineering bound, not sport science): the planned
        # goal-sport workouts must actually add up to the week's target — a week
        # targeting 8 km with a single 4 km run is half a plan. Only checked for
        # current/future weeks (past weeks are judged by their recorded actuals)
        # and only when every goal-sport workout carries a km figure.
        ws_d = _parse_date(w.get("start_date", ""))
        is_past = bool(ws_d and ws_d + timedelta(days=6) < date.today())
        if (not is_past and km > 0 and n_goal_workouts > 0 and goal_sport_all_have_km
                and not (0.9 * km <= goal_sport_km <= 1.1 * km)):
            violations.append(
                f"week {i + 1}: planned {main_sport} workouts sum to {goal_sport_km:.1f} km "
                f"but the week's target is {km} km — plan the full target (±10%), "
                f"spread over the week's sessions")
        # The line is the plan's backbone: the week's biggest goal-sport run must
        # BE the long run (±10 %) — the athlete trains ON the distance, the weekly
        # sum only follows from it.
        if (not is_past and long_run and n_goal_workouts > 0 and biggest_goal_run > 0
                and not (0.9 * long_run <= biggest_goal_run <= 1.1 * long_run)):
            violations.append(
                f"week {i + 1}: biggest {main_sport} run is {biggest_goal_run:.1f} km but "
                f"the long-run line prescribes {long_run} km (±10%) — the key session "
                f"must approach the race demand, don't split it into small runs")

        ws = _parse_date(w.get("start_date", ""))
        if ws:
            for ev in _blocked_windows(timeline):
                if _week_overlaps_event(ws, ev):
                    blocked = [s.lower() for s in (ev.get("blocked_sports") or [])]
                    for wo in w.get("workouts") or []:
                        sport = (wo.get("sport") or main_sport).lower()
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

    Returns the MAIN GOAL + MILESTONES: profile.race is the single main goal that
    drives the training plan (with days_to_race and current plan week);
    profile.races is the full list — the main goal plus any milestones (real
    tune-up/minor races, kind="race", or non-race training checkpoints,
    kind="checkpoint", e.g. "first 15 km long run") — each normalised with
    is_main, kind, source ("user" or "coach"), status ("pending"/"achieved") and
    its own days_to_race. Also returns training preferences, the personal
    timeline (injuries/illnesses/races/notes), the computed HR/pace zones (with
    the formula basis), a race-time prognosis for the main goal's distance, and
    the plan summary if one exists.
    """
    user = _user()
    doc = _load(user)
    profile = dict(doc.get("profile") or {})
    race = _a_race(doc) or {}
    races = sorted(_races(doc), key=lambda r: (0 if r.get("priority") == "A" else 1, r.get("date") or ""))
    today = date.today()
    for r in races:
        r.setdefault("kind", "race")
        r.setdefault("source", "user")
        r.setdefault("status", "pending")
        r["is_main"] = r.get("priority") == "A"
        rrd = _parse_date(r.get("date", ""))
        if rrd:
            r["days_to_race"] = (rrd - today).days
    profile["race"] = race or None
    profile["races"] = races
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
    # Prognose: das ZIELTEMPO ist reine Arithmetik und wird immer ausgewiesen, sobald
    # Zielzeit + Distanz gesetzt sind. Ein "on_track"-Urteil gibt es NUR mit einem realen
    # Benchmark-Wettkampf nahe der Zieldistanz IN der Zielsportart — Vergleich Renntempo
    # vs. Zieltempo, KEINE Distanz-Extrapolation (Riegel); fehlt der Benchmark, ehrlich
    # auf Leistungsdiagnostik verweisen (Ferrauti S.50).
    goal_dist = float(race["distance_km"]) if race.get("distance_km") else None
    if rd and goal_dist:
        prog: Dict[str, Any] = {}
        target_secs = _parse_hms(race.get("target_time", "")) if race.get("target_time") else None
        req_pace = target_secs / goal_dist if target_secs else None
        if req_pace:
            prog["required_pace"] = _fmt_pace(req_pace)
        sport = race.get("sport", "run")
        z = (doc.get("zones") or {}).get("pace_source") or {}
        if (z.get("distance_km") and z.get("time_secs") and z.get("sport", "run") == sport
                and abs(goal_dist - float(z["distance_km"])) <= max(1.0, 0.15 * goal_dist)):
            bench_dist = float(z["distance_km"])
            bench_pace = float(z["time_secs"]) / bench_dist       # sec/km
            prog.update({
                "benchmark": z.get("label") or f"{bench_dist} km",
                "benchmark_pace": _fmt_pace(bench_pace),
                "on_track": (bench_pace <= req_pace) if req_pace else None,
                "basis": "Benchmark-Renntempo vs. Zieltempo (Ferrauti S.50; keine Extrapolation)",
            })
        else:
            prog["note"] = (
                f"kein Benchmark nahe der Zieldistanz ({goal_dist} km, {sport}) — für eine "
                f"Prognose einen Testlauf/Wettkampf ~Zieldistanz absolvieren (messen statt schätzen)")
        if prog:
            out["prognosis"] = prog
    return out


@mcp.tool()
def set_race_goal(race_name: str, race_date: str, distance_km: float,
                  sport: str = "run", target_time: str = "",
                  weekly_sessions: int = 4, preferred_days: str = "") -> Dict[str, Any]:
    """Set (or replace) the athlete's MAIN GOAL — the one race that drives the
    training plan. The goal carries its SPORT ("run" or "ride", chosen when the
    goal is set) — the plan's volumes, key sessions and pace zones are built for
    that sport; other sports only appear as cross-training. Replacing the goal
    clears the stored plan (it was built for the old goal) — scaffold_plan +
    save_plan a new one. For anything that is NOT the main goal (a tune-up/minor
    race, or a non-race training checkpoint), use add_milestone instead —
    milestones never touch the plan.

    Args:
        race_name: e.g. "Baden-Marathon Half Marathon".
        race_date: ISO date "YYYY-MM-DD" — must be in the future.
        distance_km: race distance in km (21.1 for a half marathon).
        sport: "run" or "ride" — the goal's sport. Ask the athlete if unclear;
            never guess it from their activity history.
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
    sport = (sport or "run").strip().lower()
    if sport not in GOAL_SPORTS:
        return {"error": f"sport must be one of {GOAL_SPORTS}"}
    if target_time and _parse_hms(target_time) is None:
        return {"error": f"target_time '{target_time}' is not H:MM:SS / MM:SS"}
    user = _user()
    doc = _load(user)
    race = {
        "id": uuid.uuid4().hex[:12],
        "name": str(race_name).strip(), "date": rd.isoformat(),
        "distance_km": float(distance_km), "sport": sport,
        "target_time": str(target_time).strip() or None,
        "priority": "A", "kind": "race", "source": "user", "status": "pending",
    }
    doc["profile"]["races"] = _milestones(doc) + [race]
    doc["profile"]["weekly_sessions"] = max(1, min(int(weekly_sessions or 4), 14))
    if preferred_days:
        doc["profile"]["preferred_days"] = [d.strip() for d in preferred_days.split(",") if d.strip()]
    doc["plan"] = None                                     # a new main goal invalidates the old plan
    _save(user, doc)
    return {"ok": True, "race": race, "days_to_race": (rd - date.today()).days,
            "note": "existing plan cleared — scaffold and save a new one"}


@mcp.tool()
def add_milestone(title: str, target_date: str, kind: str = "checkpoint",
                  distance_km: float = 0, target_time: str = "", note: str = "",
                  source: str = "coach") -> Dict[str, Any]:
    """Add a MILESTONE on the way to the main goal — a checkpoint that makes a
    far-off goal feel closer and gives the athlete something to celebrate along
    the way. Two kinds: a real tune-up/minor RACE (kind="race", needs a real
    distance) or a non-race training CHECKPOINT (kind="checkpoint", e.g. "first
    15 km long run", "hit goal pace for 5 km", "4 weeks logged consistently").
    Milestones never change the plan's deterministic volumes — scaffold_plan
    just tells you which week each one falls in, so you can avoid stacking your
    hardest session of the week on top of a race-kind milestone. Shown on the
    athlete's timeline, separate from the freeform dashboard goals.

    PROACTIVITY: when building a plan, create 2-4 of these yourself (source=
    "coach") spread across the weeks — e.g. the week of the first double-digit
    long run, a goal-pace tempo effort, entering the peak phase — so the athlete
    has visible, motivating progress markers, not just one distant race day.

    Args:
        title: short label, e.g. "Autumn Half Marathon" or "First 15 km long run".
        target_date: ISO date this falls on/by. Required for both kinds — for a
            checkpoint without a natural date, use the Monday of the relevant
            plan week (from scaffold_plan's weeks).
        kind: "race" (needs distance_km) or "checkpoint" (distance_km/target_time
            are optional extra detail).
        distance_km: required if kind="race"; optional detail for a checkpoint.
        target_time: optional target time detail "H:MM:SS"/"MM:SS".
        note: one short, encouraging sentence — why this milestone matters.
        source: "coach" (you created it proactively) or "user" (the athlete asked
            for it, e.g. a real tune-up race they mentioned).
    """
    kind = (kind or "checkpoint").strip().lower()
    if kind not in MILESTONE_KINDS:
        return {"error": f"kind must be one of {MILESTONE_KINDS}"}
    td = _parse_date(target_date)
    if not td:
        return {"error": f"target_date '{target_date}' is not an ISO date (YYYY-MM-DD)"}
    if kind == "race" and (not distance_km or distance_km <= 0):
        return {"error": "distance_km must be > 0 for a race-kind milestone"}
    if target_time and _parse_hms(target_time) is None:
        return {"error": f"target_time '{target_time}' is not H:MM:SS / MM:SS"}
    source = source if source in ("user", "coach") else "coach"
    user = _user()
    doc = _load(user)
    milestone = {
        "id": uuid.uuid4().hex[:12], "name": str(title).strip(), "date": td.isoformat(),
        "distance_km": float(distance_km) if distance_km else None,
        "target_time": str(target_time).strip() or None,
        "priority": "milestone", "kind": kind, "note": str(note).strip() or None,
        "status": "pending", "source": source,
    }
    doc["profile"]["races"] = _races(doc) + [milestone]
    _save(user, doc)
    return {"ok": True, "milestone": milestone}


@mcp.tool()
def update_milestone_status(milestone_id: str, status: str) -> Dict[str, Any]:
    """Mark a milestone 'achieved' — e.g. once real Strava/Garmin data confirms
    the athlete hit it — or back to 'pending'. Never mark it achieved on a guess."""
    if status not in MILESTONE_STATUSES:
        return {"error": f"status must be one of {MILESTONE_STATUSES}"}
    user = _user()
    doc = _load(user)
    races = _races(doc)
    target = next((r for r in races if r.get("id") == milestone_id), None)
    if not target:
        return {"error": f"no milestone with id '{milestone_id}'"}
    target["status"] = status
    doc["profile"]["races"] = races
    _save(user, doc)
    return {"ok": True, "milestone": target}


@mcp.tool()
def delete_race_goal(race_id: str) -> Dict[str, Any]:
    """Remove the main goal or a milestone by its id (from get_athlete_overview's
    profile.races). Deleting the main goal also clears the stored training plan,
    since it was built for that goal."""
    user = _user()
    doc = _load(user)
    races = _races(doc)
    target = next((r for r in races if r.get("id") == race_id), None)
    if not target:
        return {"error": f"no race goal with id '{race_id}'"}
    doc["profile"]["races"] = [r for r in races if r.get("id") != race_id]
    if target.get("priority") == "A":
        doc["plan"] = None
    _save(user, doc)
    return {"ok": True, "deleted": race_id}


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
    if end_date and _parse_date(end_date) < _parse_date(start_date):
        return {"error": f"end_date {end_date} is before start_date {start_date}"}
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
def set_athlete_profile(age: int = 0) -> Dict[str, Any]:
    """Store stable athlete attributes used for zone defaults. Currently: age (years),
    which drives the literature HFmax estimate 208-0.7*age when no measured max HR from
    a real all-out effort exists. Captured at onboarding / editable in settings."""
    user = _user()
    doc = _load(user)
    if age and int(age) > 0:
        doc["profile"]["age"] = int(age)
    _save(user, doc)
    return {"ok": True, "profile": doc["profile"]}


@mcp.tool()
def compute_zones(max_hr: int = 0, resting_hr: int = 0, age: int = 0,
                  race_distance_km: float = 0, race_time: str = "",
                  race_label: str = "", race_sport: str = "run") -> Dict[str, Any]:
    """Compute and store HR + pace zones DETERMINISTICALLY.

    German training bands ReKom/GA1/GA2/WSA. HR zones as %HFmax and — with a resting
    HR — additionally %HFR/Karvonen (Ferrauti p.459). Pace zones as a factor x race
    pace from a real ~10 km race (Ferrauti tab. 7.7, Joch 2004). No lactate/threshold.

    DEFAULT = literature: if no measured max_hr is passed, HFmax is estimated from AGE
    (208 - 0.7*age, Tanaka via Guellich p.771), falling back to the stored profile age.
    Only pass a measured max_hr when the athlete has done a REAL all-out effort (a
    reference run / max-HR test) — a wrist-optical max from easy runs underestimates and
    would shift every zone too low. Pace zones need a real ~10 km race; otherwise omit.

    Args:
        max_hr: measured max HR in bpm — ONLY from a genuine all-out reference effort.
        resting_hr: resting HR in bpm (Garmin) — enables the more precise Karvonen/%HFR bands.
        age: age in years (defaults to the stored profile age) — drives the literature HFmax.
        race_distance_km: distance of a recent ~10 km race for pace zones (optional).
        race_time: its finish time "H:MM:SS" / "MM:SS".
        race_label: where the race result came from.
        race_sport: the benchmark race's sport ("run"/"ride") — pace zones and the
            prognosis only apply to a goal of the same sport.
    """
    user = _user()
    doc = _load(user)
    eff_age = int(age) or int((doc.get("profile") or {}).get("age") or 0)
    measured = bool(int(max_hr))
    mh = int(max_hr) or _estimate_hfmax(eff_age or None)
    hr = _hr_zones(mh, int(resting_hr) or None)
    secs = _parse_hms(race_time)
    pace = _pace_zones(float(race_distance_km) or None, secs)
    if not hr and not pace:
        return {"error": "need a max_hr, or an age (arg or stored profile) for the "
                         "208-0.7*age estimate, for HR zones; and/or race_distance_km+race_time"}
    zones: Dict[str, Any] = {"computed_at": datetime.utcnow().isoformat() + "Z"}
    if hr:
        zones["hr"] = hr
        zones["hr_max_used"] = mh
        zones["hr_max_source"] = "measured" if measured else f"age {eff_age} (208-0.7*age)"
        zones["hr_max_estimated"] = not measured           # True = literature/age estimate
    if pace:
        zones["pace"] = pace
        zones["pace_source"] = {"distance_km": float(race_distance_km),
                                "time_secs": secs, "label": race_label or None,
                                "sport": (race_sport or "run").strip().lower()}
    doc["zones"] = zones
    _save(user, doc)
    return {"ok": True, "zones": zones}


@mcp.tool()
def scaffold_plan(current_weekly_km: float = 0, current_weekly_sessions: int = 0) -> Dict[str, Any]:
    """The DETERMINISTIC, GOAL-DRIVEN plan skeleton the coach fills with workouts.

    RUN-BASED and planned BACKWARD from race day: the backbone is each week's
    ``long_run_km`` — a line of key sessions that approaches the race demand in
    even steps, ending at the full race distance (goals up to ~25 km) or 75 % of
    it (marathon+) in the last growth week before the taper. The athlete trains
    ON the distance, not on an abstract weekly sum: the week's ``target_km``
    DERIVES from its runs (long run + supporting runs of ~40 % its length),
    sessions still rise first (+1/week toward the athlete's target — Ferrauti
    S.83/S.188), every 4th week is a cutback, the taper steps down (S.117/S.295).
    Each week carries its ``phase_focus`` content prescription (Tab. 7.10/7.11).

    The ``feasibility`` block gives goal-vs-data FACTS — the long-run line
    (entry point, step size, growth weeks), required vs. benchmark pace.
    COMPARE the line's entry with the athlete's real longest recent runs from
    Strava and confront them honestly when goal and data don't line up (SMART
    "achievable", Ferrauti Tab. 1.2) — the server states facts, judging them is
    your job. A "warning" in the block MUST lead your summary.

    Each week also lists any MILESTONES that fall inside it (transient — not
    stored; plan gently around a race-kind one). Fill each week's ``workouts``
    (the week's biggest run must match ``long_run_km``) and pass the result to
    save_plan — do NOT invent your own week structure or volumes.

    Args:
        current_weekly_km: the athlete's CURRENT typical weekly volume in km in
            the GOAL SPORT (recent Strava weeks) — reported as a feasibility
            fact for your conversation; the line itself is goal-driven.
        current_weekly_sessions: how many goal-sport sessions per week the
            athlete currently does (recent Strava weeks; after a break, the last
            active weeks). Defaults to 2 — the typical Freizeitsport entry
            frequency (Ferrauti S.83). The plan raises this by one session per
            week toward the athlete's sessions target.
    """
    user = _user()
    doc = _load(user)
    race = _a_race(doc) or {}
    rd = _parse_date(race.get("date", ""))
    if not rd:
        return {"error": "no main goal set — call set_race_goal first"}
    goal_dist = float(race.get("distance_km") or 0)
    if goal_dist <= 0:
        return {"error": "main goal has no distance_km — set_race_goal first"}

    next_monday = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
    n_weeks = max(0, (rd - next_monday).days // 7)
    if n_weeks < 2:
        return {"error": f"only {n_weeks} full week(s) until the race — too short to plan"}
    phases = _phase_split(n_weeks)
    target_sessions = int((doc.get("profile") or {}).get("weekly_sessions", 4))
    targets, long_runs, sessions_per_week, line_info = _run_targets(
        phases, goal_dist,
        start_sessions=int(current_weekly_sessions) or 2,
        target_sessions=target_sessions)
    milestones = _milestones(doc)
    weeks = []
    for i, (ph, km) in enumerate(zip(phases, targets)):
        ws = next_monday + timedelta(weeks=i)
        weeks.append({
            "week": i + 1, "phase": ph, "start_date": ws.isoformat(),
            "phase_label": PHASE_FOCUS.get(ph, {}).get("label"),
            "phase_focus": PHASE_FOCUS.get(ph, {}).get("focus"),
            "target_km": km, "long_run_km": long_runs[i],
            "cutback": (i > 0 and (i + 1) % CUTBACK_EVERY == 0 and ph not in ("taper",)),
            "sessions": sessions_per_week[i],
            "workouts": [],
            "milestones": _milestones_in_week(milestones, ws),
        })
    feas = _feasibility(race, targets, n_weeks, doc.get("zones") or {},
                        line_info=line_info,
                        sessions=(sessions_per_week[0], target_sessions))
    if current_weekly_km:
        feas["current_weekly_km"] = float(current_weekly_km)
    return {
        "race": race, "n_weeks": n_weeks, "weeks": weeks,
        "feasibility": feas,
        "guardrails": {"long_run_line": line_info, "cutback_every": CUTBACK_EVERY,
                       "max_new_sessions_per_week": 1},
        "timeline_constraints": _blocked_windows(doc.get("timeline") or []),
        "zones": doc.get("zones") or {},
    }


@mcp.tool()
def save_plan(plan: dict) -> Dict[str, Any]:
    """Validate a filled plan against the hard guardrails and store it.

    Pass the scaffold_plan (or rescaffold_plan) result with each week's
    ``workouts`` filled in: every workout needs {day, title, sport, zone, and
    either duration_min or distance_km}; give intensity as the athlete's OWN
    zone bands (from the stored zones), add a one-sentence ``why`` and, where it
    came from the literature, a ``source``. The goal-sport workouts of a week
    must SUM to that week's target_km (±10 %) — spread over the week's sessions
    (frequency before duration, Ferrauti S.188), never one lone session leaving
    the rest of the target unplanned. You may also set a one-sentence ``focus``
    per week (what this week is FOR, in plain words).

    CROSS-TRAINING: workouts in a sport other than the goal sport are
    cross-training — planned by DURATION (duration_min required), never counted
    toward the week's target_km (which is goal-sport volume only). Use them per
    phase_focus: unspecific endurance (GA1) in the base phase, ReKom recovery
    sessions anywhere, substitutes during an injury window — never as the week's
    race-specific key session (Ferrauti Tab. 7.10/7.11).

    Rejected plans return the exact violations — fix them and call again;
    nothing is stored on rejection.
    """
    user = _user()
    doc = _load(user)
    if not isinstance(plan, dict):
        return {"error": "plan must be an object"}
    main_sport = (_a_race(doc) or {}).get("sport", "run")
    violations = _validate_plan(plan, doc.get("timeline") or [], main_sport)
    if violations:
        return {"error": "plan violates guardrails", "violations": violations}
    # Deterministic enrichment: a workout that names a zone (ReKom/GA1/GA2/WSA) but no
    # explicit intensity gets the athlete's OWN stored band for that zone — the numbers
    # come from compute_zones, never from the plan author. Match zone labels case- and
    # separator-insensitively ("GA 1" / "ga1" → "GA1").
    def _zkey(s: str) -> str:
        return str(s or "").replace(" ", "").replace("/", "").replace("-", "").replace("_", "").upper()

    zones = doc.get("zones") or {}
    hr_bands = {_zkey(k): v for k, v in ((zones.get("hr") or {}).get("bands_bpm") or {}).items()}
    pace_bands = {_zkey(k): v for k, v in ((zones.get("pace") or {}).get("bands_pace") or {}).items()}
    pace_sport = ((zones.get("pace_source") or {}).get("sport") or "run").lower()
    for w in plan.get("weeks") or []:
        for wo in w.get("workouts") or []:
            wo_sport = (wo.get("sport") or main_sport).lower()
            if wo_sport != main_sport:
                wo["cross_training"] = True       # marked for the UI + the ramp math
            z = _zkey(wo.get("zone"))
            if z in hr_bands and not wo.get("hr_range"):
                lo, hi = hr_bands[z]
                wo["hr_range"] = f"{lo}–{hi}"
            # Pace bands anchor on a benchmark race in ONE sport — never attach
            # them to a workout of a different sport (a swim in run-pace is noise).
            if z in pace_bands and not wo.get("pace_range") and wo_sport == pace_sport:
                slow, fast = pace_bands[z]
                wo["pace_range"] = f"{fast.replace('/km', '')}–{slow}"

    plan = {k: v for k, v in plan.items() if k in
            ("race", "weeks", "n_weeks", "guardrails", "notes", "status")}
    plan["status"] = "active"
    plan["saved_at"] = datetime.utcnow().isoformat() + "Z"
    doc["plan"] = plan
    _save(user, doc)
    n_workouts = sum(len(w.get("workouts") or []) for w in plan.get("weeks") or [])
    return {"ok": True, "n_weeks": len(plan.get("weeks") or []), "n_workouts": n_workouts}


@mcp.tool()
def record_week_actual(week: int, distance_km: float, sessions: int = 0,
                       note: str = "") -> Dict[str, Any]:
    """Record what the athlete ACTUALLY did in a plan week — the monitoring half
    of the adaptation loop (Ferrauti Tab. 1.2 Stufe 6: the short-term plan is
    continuously adjusted to athlete monitoring; S.185).

    Fetch the real numbers from Strava/Garmin first (goal-sport km + session
    count for that calendar week) and pass them in — RAW values only, never a
    derived "readiness score" (Ferrauti S.202). The server stores them on the
    week and computes compliance_pct (actual/target) deterministically. Reviewed
    weeks also become the ramp baseline in save_plan's guardrail (demonstrated
    load beats planned load).

    Args:
        week: the plan week number (1-based, from get_plan / the overview).
        distance_km: goal-sport km the athlete actually covered that week.
        sessions: how many sessions they actually did (optional).
        note: one short observation, e.g. "felt easy" / "skipped long run, sick".
    """
    user = _user()
    doc = _load(user)
    plan = doc.get("plan")
    if not plan or not (plan.get("weeks") or []):
        return {"error": "no plan stored — nothing to review"}
    weeks = plan["weeks"]
    try:
        wk = int(week)
    except (TypeError, ValueError):
        return {"error": f"week '{week}' is not a number"}
    if not 1 <= wk <= len(weeks):
        return {"error": f"week must be 1..{len(weeks)}"}
    if distance_km is None or float(distance_km) < 0:
        return {"error": "distance_km must be >= 0"}
    w = weeks[wk - 1]
    target = float(w.get("target_km") or 0)
    actual: Dict[str, Any] = {
        "distance_km": round(float(distance_km), 1),
        "sessions": int(sessions) or None,
        "note": str(note).strip() or None,
        "reviewed_at": datetime.utcnow().isoformat() + "Z",
    }
    if target > 0:
        actual["compliance_pct"] = int(round(100 * float(distance_km) / target))
    w["actual"] = actual
    doc["plan"] = plan
    _save(user, doc)
    return {"ok": True, "week": wk, "target_km": target or None, "actual": actual}


@mcp.tool()
def rescaffold_plan(current_weekly_km: float = 0) -> Dict[str, Any]:
    """Recompute the REMAINING weeks of the stored plan — the adjustment half of
    the adaptation loop, in the run-based model.

    The long-run LINE stays anchored to the race (the goal doesn't move):
    future growth weeks are refitted in even steps from the last frozen week's
    long run to the race anchor. What adapts is the SUPPORT volume around the
    line: pass current_weekly_km when the athlete is OVERLOADED (a deliberately
    reduced weekly volume — the supporting runs shrink, relief per Ferrauti
    S.295/Reizstufenregel, while the key sessions keep approaching the race) or
    leave it 0 to keep the standard ~40 %-support derivation. If even the line
    itself is unsustainable, the honest move is changing the GOAL (date/
    distance) — say so to the athlete instead of silently flattening the line.

    Past and in-progress weeks (start date not after today), including recorded
    ``actual`` data, are FROZEN; phases, cutbacks and taper structure are
    preserved. Returns the full plan with future weeks' long_run_km/target_km
    recomputed and their workouts CLEARED — fill them and call save_plan
    (nothing is stored until then).

    Args:
        current_weekly_km: optional REDUCED weekly volume for relief (overload);
            0 = derive normally. Never an invented number.
    """
    user = _user()
    doc = _load(user)
    plan = doc.get("plan")
    if not plan or not (plan.get("weeks") or []):
        return {"error": "no plan stored — use scaffold_plan for a fresh plan"}
    race = _a_race(doc) or {}
    goal_dist = float(race.get("distance_km") or 0)
    if goal_dist <= 0:
        return {"error": "no main goal distance — set_race_goal first"}
    today = date.today()
    weeks = plan["weeks"]
    n = len(weeks)
    future_idx = [i for i, w in enumerate(weeks)
                  if (_parse_date(w.get("start_date", "")) or date.min) > today]
    if not future_idx:
        return {"error": "no future weeks left in the plan — nothing to rescaffold"}
    future_set = set(future_idx)
    milestones = _milestones(doc)
    anchor = _long_run_anchor(goal_dist)

    # Line refit: from the last frozen growth week's long run (fallback: the
    # line's standard entry) to the anchor, even steps across future growth weeks.
    last_long: Optional[float] = None
    for i, w in enumerate(weeks):
        if i in future_set:
            break
        if (w.get("phase") or "").lower() != "taper" and not w.get("cutback") and w.get("long_run_km"):
            last_long = float(w["long_run_km"])
    start_long = last_long if last_long else anchor * LONG_RUN_START_FACTOR
    growth = [i for i in future_idx
              if (weeks[i].get("phase") or "").lower() != "taper" and not weeks[i].get("cutback")]
    step = (anchor - start_long) / len(growth) if growth else 0.0

    # Optional overload relief: scale the SUPPORT volume so the first future
    # week's total ≈ the requested reduced volume (the line itself stays).
    support_scale = 1.0
    relief_note = ""
    if current_weekly_km and growth:
        first_long = start_long + step
        first_sessions = int(weeks[growth[0]].get("sessions") or 3)
        full_support = first_long * SUPPORT_RUN_FACTOR * (first_sessions - 1)
        if full_support > 0 and current_weekly_km < first_long + full_support:
            support_scale = max(0.0, min(1.0, (float(current_weekly_km) - first_long) / full_support))
            relief_note = (f"support volume scaled to {round(support_scale * 100)} % for relief "
                           f"(requested {current_weekly_km} km/week); the long-run line stays "
                           f"anchored to the race. ")

    prev_long = start_long
    peak_long = anchor
    taper_i = 0
    lr = start_long
    for i in future_idx:
        w = weeks[i]
        ph = (w.get("phase") or "").lower()
        sessions = int(w.get("sessions") or 3)
        if ph == "taper":
            lr = peak_long * TAPER_LONG_FACTORS[min(taper_i, len(TAPER_LONG_FACTORS) - 1)]
            taper_i += 1
        elif i in growth:
            lr = prev_long + step
            prev_long = lr
        else:                                              # cutback week
            lr = prev_long * CUTBACK_FACTOR
        lr_r = round(lr, 1)
        w["long_run_km"] = lr_r
        w["target_km"] = round(lr_r * (1 + SUPPORT_RUN_FACTOR * support_scale * (sessions - 1)), 1)
        w["workouts"] = []
        w["phase_label"] = PHASE_FOCUS.get(ph, {}).get("label")
        w["phase_focus"] = PHASE_FOCUS.get(ph, {}).get("focus")
        ws = _parse_date(w.get("start_date", ""))
        if ws:
            w["milestones"] = _milestones_in_week(milestones, ws)
    targets = [float(w.get("target_km") or 0) for w in weeks]
    line_info = {"anchor_km": round(anchor, 1), "start_long_run_km": round(start_long + (step if growth else 0), 1),
                 "step_km": round(step, 2), "growth_weeks": len(growth)}
    return {
        "race": race, "n_weeks": n, "weeks": weeks,
        "rescaffolded_weeks": [i + 1 for i in future_idx],
        "frozen_weeks": [i + 1 for i in range(n) if i not in future_idx],
        "support_scale": support_scale,
        "feasibility": _feasibility(race, targets, n, doc.get("zones") or {}, line_info=line_info),
        "guardrails": {"long_run_line": line_info, "cutback_every": CUTBACK_EVERY,
                       "max_new_sessions_per_week": 1},
        "timeline_constraints": _blocked_windows(doc.get("timeline") or []),
        "zones": doc.get("zones") or {},
        "note": relief_note + "not saved yet — fill the rescaffolded weeks' workouts and call save_plan",
    }


@mcp.tool()
def get_plan() -> Dict[str, Any]:
    """The stored training plan (phases → weeks → workouts), or an error if none."""
    doc = _load(_user())
    return doc.get("plan") or {"error": "no plan stored — scaffold_plan + save_plan first"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
