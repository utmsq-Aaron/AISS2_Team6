import json
from pathlib import Path

cache_file = Path('.cache/strava_activities.json')
if not cache_file.exists():
    print('No cache file found')
else:
    data = json.loads(cache_file.read_text(encoding='utf-8'))
    dead_ids = {18865663356, 18865664447}
    before = len(data)
    data = [a for a in data if a.get('id') not in dead_ids]
    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    print(f'Removed {before - len(data)} dead activities. Cache now has {len(data)} entries.')
