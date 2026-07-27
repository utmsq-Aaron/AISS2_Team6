import json, time
from pathlib import Path

cache_file = Path('.cache/strava_activities.json')
if not cache_file.exists():
    print('No cache file')
else:
    age_h = (time.time() - cache_file.stat().st_mtime) / 3600
    data = json.loads(cache_file.read_text(encoding='utf-8'))
    print(f'Cache: {len(data)} activities, age {age_h:.1f}h')
    if data:
        print(f'Most recent: {data[0].get("name")} ({data[0].get("start_date","")[:10]}) id={data[0].get("id")}')
        print(f'Oldest:      {data[-1].get("name")} ({data[-1].get("start_date","")[:10]}) id={data[-1].get("id")}')
