"""The training math is arithmetic, not model output — this proves it.

The central design decision of this project is that the *plan* is computed
deterministically inside `servers/athlete_mcp.py` (zones, phase split, long-run
line, weekly volume) and the LLM only selects and explains workouts. That claim
is only worth making if it is checked, so these tests assert the rules from
[`docs/trainingsregeln.md`](../../docs/trainingsregeln.md) directly against the
functions that implement them.

Runs offline: importing the module builds a FastMCP object but starts no server,
touches no user data and needs no LLM.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from servers import athlete_mcp as am  # noqa: E402 — needs the sys.path line above


# ── heart-rate zones ──────────────────────────────────────────────────────────

def test_hrmax_fallback_follows_tanaka():
    """HRmax = 208 − 0.7·age (Tanaka 2001) — the documented fallback formula."""
    assert am._estimate_hrmax(30) == 187          # 208 − 21
    assert am._estimate_hrmax(50) == 173          # 208 − 35
    # No age → no guess. The book prefers a measurement; inventing one is worse.
    assert am._estimate_hrmax(None) is None
    assert am._estimate_hrmax(0) is None


def test_hr_zones_are_ordered_and_within_max():
    zones = am._hr_zones(max_hr=190, resting_hr=None)
    bands = zones["bands_bpm"]

    assert list(bands) == ["ReKom", "GA1", "GA2", "WSA"]
    for lo, hi in bands.values():
        assert lo < hi <= 190                     # no band may exceed HRmax
    # The four German zones ascend without gaps between their lower bounds.
    lows = [bands[z][0] for z in ("ReKom", "GA1", "GA2", "WSA")]
    assert lows == sorted(lows)
    assert zones["method"] == "%HRmax"


def test_karvonen_bands_appear_only_with_a_resting_hr():
    """%HRR needs a resting HR; without one the server must not fake it."""
    assert "bands_bpm_hrr" not in am._hr_zones(max_hr=190, resting_hr=None)

    zones = am._hr_zones(max_hr=190, resting_hr=50)
    hrr = zones["bands_bpm_hrr"]
    # Karvonen is anchored on the resting HR, so every band sits above it.
    assert list(hrr) == ["ReKom", "GA1", "GA2", "WSA"]
    for lo, hi in hrr.values():
        assert 50 < lo < hi <= 190
    # The two methods genuinely differ — that is why both are reported rather
    # than one being derived from the other.
    assert hrr["GA1"] != zones["bands_bpm"]["GA1"]
    assert "Karvonen" in zones["hrr_basis"]


def test_no_max_hr_means_no_zones():
    assert am._hr_zones(max_hr=None, resting_hr=50) is None


# ── pace zones ────────────────────────────────────────────────────────────────

def _secs(pace: str) -> int:
    """'4:30/km' → 270."""
    mm, ss = pace.replace("/km", "").split(":")
    return int(mm) * 60 + int(ss)


def test_pace_zones_derive_from_one_real_race():
    zones = am._pace_zones(race_dist_km=10, race_secs=45 * 60)

    assert zones["race_pace"] == "4:30/km"        # 2700 s / 10 km
    bands = zones["bands_pace"]
    for slow, fast in bands.values():
        assert _secs(slow) > _secs(fast)          # [slow, fast] — bigger sec/km is slower
    # Easy really is easier than race pace, hard really is harder.
    assert _secs(bands["ReKom"][0]) > 270
    assert _secs(bands["WSA"][1]) < 270


def test_pace_zones_flag_a_far_off_anchor():
    """The factors are calibrated on a ~10 km result; anything else is labelled."""
    near = am._pace_zones(race_dist_km=12, race_secs=54 * 60)
    far = am._pace_zones(race_dist_km=42.195, race_secs=3 * 3600)
    assert "anchor" not in near["basis"]
    assert "anchor ideally ~10 km" in far["basis"]


def test_no_race_result_means_no_pace_zones():
    assert am._pace_zones(None, 2700) is None
    assert am._pace_zones(10, None) is None


# ── phase split ───────────────────────────────────────────────────────────────

def test_phase_split_covers_every_week_in_order():
    for n in range(1, 40):
        phases = am._phase_split(n)
        assert len(phases) == n
        # Phases never interleave: each one appears as a single contiguous block.
        seen = []
        for ph in phases:
            if not seen or seen[-1] != ph:
                seen.append(ph)
        assert len(seen) == len(set(seen)), f"{n} weeks interleave: {phases}"
        assert seen == [p for p in ("base", "build", "peak", "taper") if p in seen]


def test_taper_is_two_weeks_when_there_is_room():
    assert am._phase_split(16).count("taper") == 2
    assert am._phase_split(10).count("taper") == 2
    assert am._phase_split(9).count("taper") == 1       # short runway → one week
    assert am._phase_split(0) == []


# ── the long-run line (the backbone of the plan) ──────────────────────────────

def test_long_run_anchor_full_distance_up_to_half_marathon():
    """Up to ~25 km the athlete covers the race distance once before race day."""
    assert am._long_run_anchor(10) == 10.0
    assert am._long_run_anchor(21.1) == 21.1
    assert am._long_run_anchor(25.0) == 25.0            # boundary is inclusive


def test_long_run_anchor_caps_the_marathon():
    """A marathon is never run in full in training — 75 % is the cap."""
    assert am._long_run_anchor(42.195) == 31.6          # 0.75 × 42.195
    assert am._long_run_anchor(50) == 37.5


def test_long_run_line_builds_to_the_anchor_and_starts_at_half():
    phases = am._phase_split(16)
    targets, long_runs, sessions, info = am._run_targets(phases, race_dist_km=21.1)

    assert len(targets) == len(long_runs) == len(sessions) == 16
    assert info["anchor_km"] == 21.1
    assert info["start_long_run_km"] == round(21.1 * am.LONG_RUN_START_FACTOR, 1)
    # The peak long run reaches the race demand — that is the whole point.
    assert max(long_runs) == 21.1


def test_every_fourth_week_is_a_cutback():
    phases = am._phase_split(16)
    _, long_runs, _, _ = am._run_targets(phases, race_dist_km=21.1)

    for i, ph in enumerate(phases):
        is_cutback = ph != "taper" and i > 0 and (i + 1) % am.CUTBACK_EVERY == 0
        if is_cutback:
            assert long_runs[i] < long_runs[i - 1], f"week {i + 1} should be a recovery week"


def test_taper_reduces_volume_from_the_peak():
    phases = am._phase_split(16)
    _, long_runs, _, _ = am._run_targets(phases, race_dist_km=21.1)

    taper_idx = [i for i, ph in enumerate(phases) if ph == "taper"]
    peak = max(long_runs)
    assert taper_idx, "a 16-week plan must taper"
    for i in taper_idx:
        assert long_runs[i] < peak
    # Taper steps down, never back up.
    assert long_runs[taper_idx[0]] > long_runs[taper_idx[-1]]


def test_frequency_rises_before_duration_and_stops_at_the_target():
    phases = am._phase_split(16)
    _, _, sessions, _ = am._run_targets(phases, race_dist_km=21.1,
                                        start_sessions=2, target_sessions=4)

    assert sessions[0] == 2
    assert max(sessions) == 4                          # never overshoots the athlete's target
    assert sessions == sorted(sessions)                # monotonic: frequency only goes up
    assert sessions[3] == 4, "the target frequency is reached early, not at the end"


def test_week_volume_derives_from_the_runs():
    """Week km = long run + 40 % of it per supporting run — not a free-floating number."""
    phases = am._phase_split(12)
    targets, long_runs, sessions, _ = am._run_targets(phases, race_dist_km=10)

    for week, (total, lr, n) in enumerate(zip(targets, long_runs, sessions, strict=True), start=1):
        expected = round(lr * (1 + am.SUPPORT_RUN_FACTOR * (n - 1)), 1)
        assert total == expected, f"week {week}"


# ── formatting helpers ────────────────────────────────────────────────────────

def test_time_parsing_round_trips():
    assert am._parse_hms("1:23:45") == 5025
    assert am._parse_hms("45:00") == 2700           # mm:ss when there is no hour part
    assert am._fmt_secs(5025) == "1:23:45"
    assert am._fmt_secs(None) is None
    assert am._parse_hms("nonsense") is None


def test_pace_formatting():
    assert am._fmt_pace(270) == "4:30/km"
    assert am._fmt_pace(None) is None
