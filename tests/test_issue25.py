"""Issue #25 — a structured, optional TARGET EVENT on a freeform goal.

Proves, WITHOUT the LLM gateway or a live stack, that:
  1. add_goal(event=...) stores a NORMALIZED event (ISO date, float distance/elevation).
  2. A messy event (blank strings, "12.5" string distance, unknown keys, full ISO
     datetime) normalizes correctly.
  3. An all-empty event → event is None (not a dict of Nones).
  4. user_memory.goal_block() renders a "Target event:" line for a goal WITH an event.
  5. A goal WITHOUT an event has no event line and does not crash goal_block.
  6. REGRESSION (#29): the goal schema still carries panel_build_started_at + panel_error.
  7. update_goal_event sets / clears the event through the update path.

Uses a TEMP dir (both _root hooks monkeypatched), so real data/user_memory is never
touched. Plain asserts — pytest is NOT installed.

Run:  python tests/test_issue25.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import goal_store  # noqa: E402
from core import user_memory  # noqa: E402

_USER = "issue25@example.com"
_TMP = Path(tempfile.mkdtemp(prefix="issue25_test_"))
# Redirect every read/write to a throwaway dir — real data/user_memory is untouched.
goal_store._root = lambda: _TMP            # type: ignore[assignment]
user_memory.memory_root = lambda: _TMP     # type: ignore[assignment]

_passed = 0


def check(cond: bool, msg: str) -> None:
    global _passed
    if cond:
        _passed += 1
        print(f"PASS  {msg}")
    else:
        print(f"FAIL  {msg}")
        raise SystemExit(1)


# ── 1. Clean event is stored, normalized ──────────────────────────────────────
g1 = goal_store.add_goal(
    _USER,
    "Finish Berlin Marathon under 3:30",
    sport="Running",
    source="user",
    event={
        "date": "2026-09-27",
        "name": "  Berlin Marathon  ",
        "distance_km": 42.2,
        "sport": "Running",
        "elevation_gain_m": 120,
    },
)
check(g1 is not None, "add_goal with a clean event returned a goal")
ev = g1["event"]
check(isinstance(ev, dict), "clean event stored as a dict")
check(ev["date"] == "2026-09-27", "date coerced to ISO string")
check(ev["name"] == "Berlin Marathon", "name trimmed")
check(isinstance(ev["distance_km"], float) and ev["distance_km"] == 42.2, "distance is float 42.2")
check(isinstance(ev["elevation_gain_m"], float) and ev["elevation_gain_m"] == 120.0, "elevation coerced to float")
check(ev["sport"] == "Running", "event sport trimmed")
check(set(ev.keys()) == {"date", "name", "distance_km", "sport", "elevation_gain_m"}, "only the 5 known keys survive")

# ── 2. Messy event normalizes ─────────────────────────────────────────────────
g2 = goal_store.add_goal(
    _USER,
    "Ride a gravel century",
    sport="Gravel Bike",
    source="user",
    event={
        "date": "2026-06-01T08:00:00",  # full ISO datetime → keep date part
        "name": "   ",                   # blank → None
        "distance_km": "12.5",           # string number → float
        "sport": "",                     # blank → None
        "elevation_gain_m": "not-a-num", # garbage → None
        "bogus_key": "dropped",          # unknown key → dropped
    },
)
ev2 = g2["event"]
check(ev2["date"] == "2026-06-01", "full ISO datetime coerced to date part")
check(ev2["name"] is None, "blank name → None")
check(ev2["distance_km"] == 12.5, "'12.5' string coerced to float 12.5")
check(ev2["sport"] is None, "blank sport → None")
check(ev2["elevation_gain_m"] is None, "garbage elevation → None")
check("bogus_key" not in ev2, "unknown key dropped")

# ── 3. All-empty event → None; no event arg → None ────────────────────────────
g3 = goal_store.add_goal(
    _USER,
    "Just move more this month",
    source="user",
    event={"date": "", "name": "  ", "distance_km": None, "sport": "", "elevation_gain_m": ""},
)
check(g3["event"] is None, "an all-empty event normalizes to None")

g4 = goal_store.add_goal(_USER, "Swim twice a week", source="user")
check(g4["event"] is None, "add_goal with no event arg → event is None")

# ── 6. REGRESSION (#29): schema still carries the panel_* concurrency fields ───
for g in (g1, g2, g3, g4):
    check("panel_build_started_at" in g and "panel_error" in g,
          f"panel_build_started_at + panel_error present (id={g['id']})")
check(g1["panel_build_started_at"] is None and g1["panel_error"] is None,
      "panel_* default to None on a fresh goal")

# ── 4 & 5. goal_block renders the event line only when an event exists ─────────
mem = user_memory.get_user_memory(_USER)
block = mem.goal_block()

check("Target event:" in block, "goal_block contains a 'Target event:' line")
check("Berlin Marathon on 2026-09-27" in block, "event line has name + ISO date")
check("42.2 km" in block, "event line has distance")
check("120 m elevation" in block, "event line has elevation (no trailing .0)")
check("(Running)" in block, "event line has sport in parens")

goals_now = [g for g in goal_store.list_goals(_USER) if g.get("status") == "active"]
with_event = [g for g in goals_now if g.get("event")]
check(block.count("Target event:") == len(with_event),
      f"exactly one event line per event-bearing goal ({len(with_event)})")
check("Ride a gravel century" in block and "12.5 km" in block,
      "partial event (date+distance, no name/sport) renders without crash")

# ── 7. update path: update_goal_event sets and clears the event ───────────────
updated = goal_store.update_goal_event(_USER, g4["id"], {"name": "Local 5K", "date": "2026-05-05"})
check(updated is not None and updated["event"]["name"] == "Local 5K", "update_goal_event sets an event")
cleared = goal_store.update_goal_event(_USER, g4["id"], {"name": "", "date": ""})
check(cleared["event"] is None, "update_goal_event with an all-empty event clears it to None")

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\nALL ISSUE-25 TESTS PASSED ({_passed} checks)")
