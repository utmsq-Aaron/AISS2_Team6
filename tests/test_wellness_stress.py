"""Verify wellness trends stress subplot."""
import json, sys
sys.path.insert(0, '.')
from core.viz_telegram import render_chart_png

data_with_stress = {
    "trend": [
        {"date": "2026-06-01", "total_sleep_h": 7.2, "body_battery_high": 82, "resting_hr": 52, "steps": 9800,  "avg_stress": 28},
        {"date": "2026-06-02", "total_sleep_h": 6.8, "body_battery_high": 71, "resting_hr": 54, "steps": 12300, "avg_stress": 45},
        {"date": "2026-06-03", "total_sleep_h": 8.1, "body_battery_high": 90, "resting_hr": 50, "steps": 7200,  "avg_stress": 22},
        {"date": "2026-06-04", "total_sleep_h": 7.5, "body_battery_high": 85, "resting_hr": 51, "steps": 11000, "avg_stress": 35},
    ]
}

data_no_stress = {
    "trend": [
        {"date": "2026-06-01", "total_sleep_h": 7.2, "body_battery_high": 82, "resting_hr": 52, "steps": 9800, "avg_stress": None},
        {"date": "2026-06-02", "total_sleep_h": 6.8, "body_battery_high": 71, "resting_hr": 54, "steps": 12300, "avg_stress": None},
    ]
}

png1 = render_chart_png("get_garmin_wellness_trends", json.dumps(data_with_stress))
png2 = render_chart_png("get_garmin_wellness_trends", json.dumps(data_no_stress))

print(f"With stress (5 rows):    {len(png1) // 1024 if png1 else 0} KB")
print(f"Without stress (4 rows): {len(png2) // 1024 if png2 else 0} KB")
print("OK" if png1 and png2 and len(png1) > len(png2) else "CHECK: stress chart should be bigger")
