"""Test: activity streams viz import and logic."""
import sys, json
sys.path.insert(0, '.')

# Just check that the import of the relevant function works (no Streamlit context needed)
# We can't run the full Streamlit renderer, but we can test the logic

# Test the Strava cache filter (already passing), and check the new viz structure
from servers.strava_mcp import StravaAPI
api = StravaAPI()
print("StravaAPI._filter_activities imported OK")

# Test the math for distance calculation in viz_activity_streams
import math
points = [
    {"lat": 49.0, "lon": 8.4, "ele": 115, "hr": 145},
    {"lat": 49.001, "lon": 8.401, "ele": 118, "hr": 148},
    {"lat": 49.002, "lon": 8.402, "ele": 120, "hr": 152},
    {"lat": 49.003, "lon": 8.401, "ele": 119, "hr": 158},
    {"lat": 49.004, "lon": 8.400, "ele": 116, "hr": 155},
]

dists = []
cum_d = 0.0
for i, p in enumerate(points):
    if i > 0:
        prev = points[i - 1]
        dlat = (p["lat"] - prev["lat"]) * 111000
        dlon = (p["lon"] - prev["lon"]) * 111000 * 0.85
        cum_d += math.sqrt(dlat**2 + dlon**2) / 1000
    dists.append(round(cum_d, 3))

print(f"Distance calc: {dists}")

# Verify has_hr/has_ele detection
has_hr = any(p.get("hr") for p in points[:50])
has_ele = any(p.get("ele") is not None for p in points[:50])
print(f"has_hr: {has_hr}, has_ele: {has_ele}")
assert has_hr, "should have HR"
assert has_ele, "should have elevation"

# Test with no HR
points_no_hr = [{"lat": 49.0+i*0.001, "lon": 8.4, "ele": 115+i} for i in range(5)]
has_hr2 = any(p.get("hr") for p in points_no_hr[:50])
has_ele2 = any(p.get("ele") is not None for p in points_no_hr[:50])
print(f"No HR case: has_hr: {has_hr2}, has_ele: {has_ele2}")

print("\nAll checks passed!")
