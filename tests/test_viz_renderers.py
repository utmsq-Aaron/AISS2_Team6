"""Quick test of all viz_telegram renderers with real API data."""
import sys, json, os
sys.path.insert(0, '.')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from core.host import ToolHost
from core import viz_telegram as vt

host = ToolHost()

def test(label, tool, args=None, user_query=""):
    args = args or {}
    try:
        res = host.call_tool(f"garmin__{tool}", args)
        data = json.loads(res)
        if data.get("error"):
            print(f"  TOOL ERROR [{label}]: {data['error'][:80]}")
            return
        fn = vt._REGISTRY.get(tool)
        if not fn:
            print(f"  NO RENDERER [{label}]: {tool}")
            return
        try:
            png = fn(data, user_query=user_query)
        except TypeError:
            png = fn(data)
        if png:
            print(f"  OK [{label}]: {len(png)//1024} KB PNG")
        else:
            print(f"  NONE [{label}]: renderer returned None")
    except Exception as e:
        print(f"  EXCEPTION [{label}]: {e}")

def test_strava(label, tool, args=None, user_query=""):
    args = args or {}
    try:
        res = host.call_tool(f"strava__{tool}", args)
        data = json.loads(res)
        if data.get("error"):
            print(f"  TOOL ERROR [{label}]: {data['error'][:80]}")
            return
        fn = vt._REGISTRY.get(tool)
        if not fn:
            print(f"  NO RENDERER [{label}]: {tool}")
            return
        try:
            png = fn(data, user_query=user_query)
        except TypeError:
            png = fn(data)
        if png:
            print(f"  OK [{label}]: {len(png)//1024} KB PNG")
        else:
            print(f"  NONE [{label}]: renderer returned None")
    except Exception as e:
        print(f"  EXCEPTION [{label}]: {e}")

print("=== Garmin Renderers ===")
test("sleep", "get_garmin_sleep")
test("body_battery (7d)", "get_garmin_body_battery", {"start_date": "2026-06-04", "end_date": "2026-06-10"})
test("hr_timeline", "get_garmin_heart_rate_timeline")
test("steps_timeline", "get_garmin_steps_timeline")
test("stress_timeline", "get_garmin_stress_timeline")
test("hrv_status", "get_garmin_hrv_status")
test("daily_health", "get_garmin_daily_health")
test("wellness_14d", "get_garmin_wellness_trends", {"days": 14})
test("training_metrics", "get_garmin_training_metrics")

print("\n=== Garmin Activities (checking date field fix) ===")
try:
    res = host.call_tool("garmin__get_garmin_activities", {"limit": 20})
    data = json.loads(res)
    acts = data.get("activities") or []
    print(f"  Got {len(acts)} activities")
    if acts:
        print(f"  First act keys: {list(acts[0].keys())[:8]}")
        print(f"  First act date field: {acts[0].get('date')!r} / start_date: {acts[0].get('start_date')!r}")
        # Test profile
        profile = vt._profile_activities(acts)
        print(f"  Profile: span_days={profile['span_days']}, n_sports={profile['n_sports']}, dates={profile['date_from']} to {profile['date_to']}")
        # Test render with user_query
        png = vt._activities(data, user_query="Show me my recent runs with pace")
        if png:
            print(f"  CHART: {len(png)//1024} KB PNG")
        else:
            print("  CHART: None (failed)")
except Exception as e:
    import traceback; traceback.print_exc()

print("\n=== GPS Track (activity_gps_track) ===")
try:
    # Get most recent Garmin activity ID
    res = host.call_tool("garmin__get_garmin_activities", {"limit": 1})
    acts = json.loads(res).get("activities") or []
    if acts:
        act_id = acts[0]["id"]
        print(f"  Using activity_id={act_id}")
        res2 = host.call_tool("garmin__get_activity_gps_track", {"activity_id": act_id})
        data2 = json.loads(res2)
        if data2.get("error"):
            print(f"  GPS error: {data2['error'][:80]}")
        else:
            print(f"  GPS points: {data2.get('total_points', 0)}")
            fn = vt._REGISTRY.get("get_activity_gps_track")
            png = fn(data2)
            print(f"  CHART: {len(png)//1024 if png else 0} KB PNG")
except Exception as e:
    import traceback; traceback.print_exc()

print("\nDone.")
