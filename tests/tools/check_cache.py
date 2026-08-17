"""Show what is in the Strava activity file cache and how stale it is."""

import json
import time
from pathlib import Path

cache_file = Path(__file__).resolve().parents[2] / ".cache" / "strava_activities.json"
if not cache_file.exists():
    print(f"No cache file at {cache_file}")
else:
    age_h = (time.time() - cache_file.stat().st_mtime) / 3600
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    print(f"Cache: {len(data)} activities, age {age_h:.1f}h")
    if data:
        first = data[0]
        print(f"Most recent: {first.get('name')} "
              f"({(first.get('start_date') or '')[:10]}) id={first.get('id')}")
