"""Quick test: verify all viz_telegram renderers load and handle basic data."""
import sys, json
sys.path.insert(0, '.')

from core.viz_telegram import can_render, render_chart_png

# Tools expected to have renderers
EXPECTED = [
    "get_garmin_sleep",
    "get_garmin_body_battery",
    "get_garmin_heart_rate_timeline",
    "get_activities",
    "get_garmin_activities",
    "get_activity_streams",
    "analyze_performance_trends",
    "get_training_load",
    "get_garmin_wellness_trends",
    "get_activity_gps_track",
    "get_garmin_daily_health",
    "get_garmin_steps_timeline",
    "get_garmin_stress_timeline",
    "get_garmin_hrv_status",
    "get_garmin_training_metrics",
    "get_garmin_activity_detail",
    "get_training_trends",
    "get_yearly_breakdown",
    "compare_activity_to_baseline",
    "get_garmin_body_composition",
    "get_activity_stats",
    "get_personal_bests",
    "get_weather_forecast",
    "get_gear_info",
]

print(f"Checking {len(EXPECTED)} expected renderers:\n")
all_ok = True
for tool in EXPECTED:
    ok = can_render(tool)
    status = "OK  " if ok else "MISS"
    print(f"  [{status}] {tool}")
    if not ok:
        all_ok = False

print()

# Smoke-test each renderer with minimal data
SMOKE_DATA = {
    "get_garmin_body_composition": json.dumps({
        "measurements": [
            {"date": "2026-06-01", "weight_kg": 72.5, "body_fat_pct": 14.2, "bmi": 22.8},
            {"date": "2026-06-05", "weight_kg": 72.1, "body_fat_pct": 13.9, "bmi": 22.7},
            {"date": "2026-06-09", "weight_kg": 71.8, "body_fat_pct": 13.8, "bmi": 22.6},
        ],
        "latest": {"weight_kg": 71.8, "bmi": 22.6, "body_fat_pct": 13.8},
    }),
    "get_training_trends": json.dumps({"weeks": [
        {"week_start": "2026-05-01", "distance_km": 42.0, "activities": 3},
        {"week_start": "2026-05-08", "distance_km": 35.5, "activities": 2},
        {"week_start": "2026-05-15", "distance_km": 55.2, "activities": 4},
    ]}),
    "get_yearly_breakdown": json.dumps({"years": [
        {"year": 2024, "total_distance_km": 820.0},
        {"year": 2025, "total_distance_km": 1240.5},
        {"year": 2026, "total_distance_km": 310.0},
    ]}),
    "compare_activity_to_baseline": json.dumps({
        "activity": {"name": "Morning Run", "date": "2026-06-09"},
        "assessment": "harder than usual",
        "comparisons": {
            "pace_min_per_km":  {"difficulty_percentile": 72, "activity_value": 5.2, "baseline_avg": 5.8},
            "avg_hr_bpm":       {"difficulty_percentile": 68, "activity_value": 158, "baseline_avg": 152},
            "distance_km":      {"difficulty_percentile": 45, "activity_value": 10.1, "baseline_avg": 9.8},
            "elevation_m":      {"difficulty_percentile": 80, "activity_value": 220, "baseline_avg": 140},
        },
    }),
    "get_activity_stats": json.dumps({
        "total_activities": 432, "total_distance_km": 4821.3,
        "total_time_hours": 520.4, "total_elevation_gain_m": 38900,
        "sport_breakdown": {
            "Run":  {"count": 310, "distance_km": 3200.0, "time_hours": 320.0, "elevation_m": 21000},
            "Ride": {"count": 80,  "distance_km": 1400.0, "time_hours": 140.0, "elevation_m": 14000},
            "Hike": {"count": 42,  "distance_km": 221.3,  "time_hours":  60.4, "elevation_m":  3900},
        },
    }),
    "get_personal_bests": json.dumps({
        "top_5_by_distance": [
            {"name": "Ultra Mountain Trail", "date": "2025-09-14", "distance_km": 52.3, "type": "Hike", "elevation_gain_m": 3100},
            {"name": "Pfälzerwald Marathon",  "date": "2024-10-06", "distance_km": 42.2, "type": "Run",  "elevation_gain_m":  900},
            {"name": "Half-Marathon Karlsruhe","date": "2025-04-13","distance_km": 22.1, "type": "Run",  "elevation_gain_m":  150},
        ],
        "top_5_by_elevation": [
            {"name": "Allgäu Tour",       "date": "2025-08-01", "distance_km": 38.0, "elevation_gain_m": 3400},
            {"name": "Black Forest Loop", "date": "2024-07-20", "distance_km": 25.5, "elevation_gain_m": 2800},
        ],
        "top_5_fastest": [
            {"name": "5K Park Run", "date": "2025-05-10", "pace_min_per_km": 3.92},
            {"name": "Track 10K",   "date": "2024-09-21", "pace_min_per_km": 4.15},
        ],
        "biggest_week": {"week": "2025-W40", "distance_km": 95.0},
        "longest_streak_days": 18,
        "total_unique_active_days": 312,
    }),
    "get_weather_forecast": json.dumps({
        "location": "Karlsruhe",
        "forecast": [
            {"date": "2026-06-10", "temp_min_c": 14, "temp_max_c": 24, "precip_probability_pct": 10, "wind_max_kmh": 15, "weather_condition": "Partly Cloudy"},
            {"date": "2026-06-11", "temp_min_c": 16, "temp_max_c": 28, "precip_probability_pct": 5,  "wind_max_kmh": 10, "weather_condition": "Sunny"},
            {"date": "2026-06-12", "temp_min_c": 18, "temp_max_c": 31, "precip_probability_pct": 20, "wind_max_kmh": 18, "weather_condition": "Partly Cloudy"},
            {"date": "2026-06-13", "temp_min_c": 15, "temp_max_c": 22, "precip_probability_pct": 65, "wind_max_kmh": 28, "weather_condition": "Thunderstorm"},
            {"date": "2026-06-14", "temp_min_c": 12, "temp_max_c": 18, "precip_probability_pct": 80, "wind_max_kmh": 35, "weather_condition": "Heavy Rain"},
            {"date": "2026-06-15", "temp_min_c": 11, "temp_max_c": 16, "precip_probability_pct": 40, "wind_max_kmh": 20, "weather_condition": "Cloudy"},
            {"date": "2026-06-16", "temp_min_c": 13, "temp_max_c": 21, "precip_probability_pct": 15, "wind_max_kmh": 12, "weather_condition": "Partly Cloudy"},
        ],
    }),
    "get_gear_info": json.dumps({
        "shoes": [
            {"name": "Nike Vaporfly 3",  "brand": "Nike",   "distance_km": 412.5, "primary": True},
            {"name": "Brooks Ghost 15",  "brand": "Brooks", "distance_km": 820.3, "primary": False},
            {"name": "ASICS Nimbus 25",  "brand": "ASICS",  "distance_km": 290.1, "primary": False},
        ],
        "bikes": [
            {"name": "Canyon Grail CF SL", "brand": "Canyon", "distance_km": 5280.0, "primary": True},
        ],
    }),
    "get_garmin_activity_detail": json.dumps({
        "name": "Track workout", "date": "2026-06-09", "type": "running",
        "laps": [
            {"lap": 1, "distance_km": 1.0, "pace_min_per_km": 4.5, "avg_hr": 155},
            {"lap": 2, "distance_km": 1.0, "pace_min_per_km": 4.3, "avg_hr": 162},
            {"lap": 3, "distance_km": 1.0, "pace_min_per_km": 4.2, "avg_hr": 168},
            {"lap": 4, "distance_km": 1.0, "pace_min_per_km": 4.4, "avg_hr": 164},
        ],
        "hr_zones": [
            {"zone": 1, "time_min": 2.0, "hr_low": 0},
            {"zone": 2, "time_min": 5.5, "hr_low": 115},
            {"zone": 3, "time_min": 12.0, "hr_low": 135},
            {"zone": 4, "time_min": 18.0, "hr_low": 155},
            {"zone": 5, "time_min": 6.5, "hr_low": 175},
        ],
    }),
}

print("Smoke-testing renderers with synthetic data:\n")
for tool, data in SMOKE_DATA.items():
    try:
        png = render_chart_png(tool, data)
        sz = len(png) // 1024 if png else 0
        print(f"  {'OK  ' if png else 'NONE'} {tool}: {sz} KB")
    except Exception as e:
        print(f"  FAIL {tool}: {e}")
        all_ok = False

print()
print("All OK!" if all_ok else "Some renderers have issues!")
