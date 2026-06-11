"""Test: verify the Strava file cache filter logic works correctly."""
import sys, json, time
sys.path.insert(0, '.')

# Test the _filter_activities method directly
from servers.strava_mcp import StravaAPI

api = StravaAPI()

# Synthetic activities covering multiple sports and date ranges
TEST_ACTS = [
    {"name": "Morning Run", "type": "Run", "sport_type": "Run", "start_date": "2026-06-10T07:00:00Z", "distance": 10000, "average_speed": 3.0, "average_heartrate": 155},
    {"name": "Lunch Ride", "type": "Ride", "sport_type": "Ride", "start_date": "2026-06-09T12:00:00Z", "distance": 25000, "average_speed": 7.0},
    {"name": "Evening Run", "type": "Run", "sport_type": "Run", "start_date": "2026-06-08T18:00:00Z", "distance": 8000, "average_speed": 2.8, "average_heartrate": 162},
    {"name": "Hike", "type": "Hike", "sport_type": "Hike", "start_date": "2026-06-07T09:00:00Z", "distance": 15000, "average_speed": 1.5},
    {"name": "Old Run", "type": "Run", "sport_type": "Run", "start_date": "2026-03-01T07:00:00Z", "distance": 12000, "average_speed": 3.2},
]

def check(label, result, expected_count, first_name=None):
    ok = len(result) == expected_count
    if first_name and ok:
        ok = ok and result[0]["name"] == first_name
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}: got {len(result)}, expected {expected_count}")
    if not ok:
        print(f"    -> got: {[a['name'] for a in result]}")
    return ok

all_ok = True

# Test 1: No filter → all 5
r = api._filter_activities(TEST_ACTS, None, None, None)
all_ok &= check("No filter", r, 5)

# Test 2: sport_type=Run → 3 runs
r = api._filter_activities(TEST_ACTS, "Run", None, None)
all_ok &= check("sport_type=Run", r, 3)

# Test 3: sport_type=Ride → 1
r = api._filter_activities(TEST_ACTS, "Ride", None, None)
all_ok &= check("sport_type=Ride", r, 1)

# Test 4: start_date filter → activities from June onward (4 recent, not old March one)
r = api._filter_activities(TEST_ACTS, None, "2026-06-01", None)
all_ok &= check("start_date=2026-06-01", r, 4)

# Test 5: sport_type + start_date → runs from June (2 runs, not the March one)
r = api._filter_activities(TEST_ACTS, "Run", "2026-06-01", None)
all_ok &= check("Run + start_date=June", r, 2)

# Test 6: end_date → all before June 10
r = api._filter_activities(TEST_ACTS, None, None, "2026-06-09")
all_ok &= check("end_date=2026-06-09", r, 4)

# Test 7: date range
r = api._filter_activities(TEST_ACTS, None, "2026-06-08", "2026-06-09")
all_ok &= check("2026-06-08 to 2026-06-09", r, 2)

# Test 8: Case-insensitive sport match
r = api._filter_activities(TEST_ACTS, "run", None, None)
all_ok &= check("sport_type=run (lowercase)", r, 3)

print()
print("All tests passed!" if all_ok else "Some tests FAILED!")
