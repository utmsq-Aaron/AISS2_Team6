"""Regression test for GH #29 — the goal panel that sticks on "Building your panel…"
forever. Two defects, both proved here without the LLM gateway or a live bridge:

  DEFECT A (skip-window collision): the FastAPI form path optimistically flips a goal
    to "building" for instant UI feedback. The build worker then used to read the SAME
    "building" timestamp and conclude a build was already in flight → skipped the
    first-and-only build → wedged forever. Fixed by a DEDICATED build-start marker
    (``panel_build_started_at``) stamped ONLY by the real builder.
  DEFECT B (worker offline): the build queue is drained only by the Telegram bridge; if
    it's down nothing ever leaves "building". Fixed by a lazy READ-PATH watchdog
    (``goal_store._reap_stale_building``) that flips a long-stuck "building" to "error".

Runs with a TEMP user-memory dir (``_root`` monkeypatched), so real ``data/`` is never
touched. Plain asserts — pytest is NOT installed.

Run:  python tests/test_goal_panel_watchdog.py
"""

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import goal_store  # noqa: E402

_USER = "watchdog@example.com"
_TMP = Path(tempfile.mkdtemp(prefix="goal_watchdog_test_"))
# Redirect every read/write to a throwaway dir — real data/user_memory is untouched.
goal_store._root = lambda: _TMP  # type: ignore[assignment]


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _hand_age(gid: str, seconds_ago: float) -> None:
    """Rewind a goal's age markers directly in goals.json (simulate the passage of
    time without sleeping). Ages BOTH updated_at and panel_build_started_at (whichever
    the watchdog reads)."""
    path = goal_store._goals_path(_USER)
    doc = json.loads(path.read_text(encoding="utf-8"))
    for g in doc["goals"]:
        if g["id"] == gid:
            stamp = _iso_ago(seconds_ago)
            g["updated_at"] = stamp
            if g.get("panel_build_started_at") is not None:
                g["panel_build_started_at"] = stamp
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _patch_field(gid: str, **fields) -> None:
    """Force arbitrary fields onto a stored goal (to build states the API can't)."""
    path = goal_store._goals_path(_USER)
    doc = json.loads(path.read_text(encoding="utf-8"))
    for g in doc["goals"]:
        if g["id"] == gid:
            g.update(fields)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


# ── tests ───────────────────────────────────────────────────────────────────────

def test_form_path_does_not_skip_first_build():
    """DEFECT A regression proof: the API's optimistic 'building' flip
    (build_started=False, default) must NOT look like an in-flight build, so the
    first-and-only real build runs."""
    from core.goal_panel import _already_building_recently

    goal = goal_store.add_goal(_USER, "Improving my FTP in 4 weeks", source="user")
    assert goal is not None
    gid = goal["id"]
    assert goal["panel_build_started_at"] is None, "new goal must not carry a build marker"

    # The API path: enqueue then optimistically flip to "building" (build_started=False).
    g = goal_store.set_panel_status(_USER, gid, "building")
    assert g["panel_status"] == "building"
    assert g["panel_build_started_at"] is None, "the API flip must NOT stamp the build marker"
    assert _already_building_recently(g) is False, \
        "the first build must NOT be skipped by the API's own 'building' flip (Defect A)"

    # Now the REAL builder starts and stamps the dedicated marker → a genuinely
    # concurrent SECOND build correctly sees it and skips.
    g2 = goal_store.set_panel_status(_USER, gid, "building", build_started=True)
    assert g2["panel_build_started_at"] is not None, "the real builder must stamp the marker"
    assert _already_building_recently(g2) is True, \
        "a concurrent second build must be skipped once the real build is in flight"
    print("PASS  form path runs the first build; concurrent guard still holds (Defect A)")


def test_read_path_reaps_stuck_building():
    """DEFECT B regression proof: a panel stuck in 'building' past
    GOAL_PANEL_STUCK_SECONDS is flipped to 'error' on the next read — even with the
    build worker entirely offline (nothing here drains a queue)."""
    threshold = goal_store._stuck_seconds()

    goal = goal_store.add_goal(_USER, "Run a sub-4h marathon", source="user")
    gid = goal["id"]
    goal_store.set_panel_status(_USER, gid, "building", build_started=True)
    _hand_age(gid, threshold + 120)  # older than the stuck window

    reaped = goal_store.get_goal(_USER, gid)
    assert reaped["panel_status"] == "error", f"stuck 'building' must be reaped: {reaped['panel_status']}"
    assert reaped["panel_error"], "a reaped panel must carry a user-facing panel_error"
    assert "offline" in reaped["panel_error"].lower(), reaped["panel_error"]
    assert reaped["panel_build_started_at"] is None, "reaper must clear the in-flight marker"

    # And the flip is persisted (list_goals also reaps + writes).
    goal2 = goal_store.add_goal(_USER, "Second stuck goal", source="user")
    gid2 = goal2["id"]
    goal_store.set_panel_status(_USER, gid2, "building", build_started=True)
    _hand_age(gid2, threshold + 120)
    listed = {g["id"]: g for g in goal_store.list_goals(_USER)}
    assert listed[gid2]["panel_status"] == "error", "list_goals must reap too"
    assert listed[gid2]["panel_error"], "list_goals reap must set panel_error"
    print(f"PASS  read-path watchdog flips a >{threshold}s stuck 'building' to 'error' (Defect B)")


def test_fresh_building_not_reaped():
    """A panel that only just started building must survive the watchdog untouched."""
    goal = goal_store.add_goal(_USER, "Fresh building goal", source="user")
    gid = goal["id"]
    goal_store.set_panel_status(_USER, gid, "building", build_started=True)
    _hand_age(gid, 15)  # well under the stuck window

    g = goal_store.get_goal(_USER, gid)
    assert g["panel_status"] == "building", "a fresh build must NOT be reaped"
    assert g.get("panel_error") in (None, ""), "a fresh build must carry no panel_error"
    print("PASS  a fresh (<stuck) 'building' panel is left alone")


def test_set_panel_clears_inflight_markers():
    """A completed build is no longer in flight: set_panel must clear both the
    build-start marker and any stale panel_error."""
    goal = goal_store.add_goal(_USER, "Goal that finishes building", source="user")
    gid = goal["id"]
    goal_store.set_panel_status(_USER, gid, "building", build_started=True)
    # Simulate a stale leftover error alongside the in-flight marker.
    _patch_field(gid, panel_error="stale error from a prior attempt")
    before = goal_store.get_goal(_USER, gid)
    assert before["panel_build_started_at"] is not None and before["panel_error"]

    saved = goal_store.set_panel(_USER, gid, {
        "headline": "On pace", "status": "on_track",
        "tiles": [{"label": "FTP", "value": "250W"}, {"label": "Weeks", "value": "4"}],
        "note": "Looking good.",
    })
    assert saved is not None
    assert saved["panel_status"] == "ready"
    assert saved["panel_build_started_at"] is None, "success must clear the build marker"
    assert saved["panel_error"] is None, "success must clear any prior panel_error"
    print("PASS  set_panel(success) clears panel_build_started_at + panel_error")


if __name__ == "__main__":
    try:
        test_form_path_does_not_skip_first_build()
        test_read_path_reaps_stuck_building()
        test_fresh_building_not_reaped()
        test_set_panel_clears_inflight_markers()
        print("\nALL GOAL-PANEL WATCHDOG TESTS PASSED")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
