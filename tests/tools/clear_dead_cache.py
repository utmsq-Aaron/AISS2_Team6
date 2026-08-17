"""Drop activity ids from the Strava file cache. **Mutates state.**

Use when the cache holds activities that no longer exist upstream (deleted on
Strava), which otherwise surface as 404s on every detail fetch.

    python tests/tools/clear_dead_cache.py 18865663356 18865664447
"""

import json
import sys
from pathlib import Path

cache_file = Path(__file__).resolve().parents[2] / ".cache" / "strava_activities.json"

dead_ids = {int(a) for a in sys.argv[1:] if a.strip().isdigit()}
if not dead_ids:
    print(__doc__)
    print("Nothing to do — pass one or more activity ids.")
    raise SystemExit(0)

if not cache_file.exists():
    print(f"No cache file at {cache_file}")
    raise SystemExit(0)

data = json.loads(cache_file.read_text(encoding="utf-8"))
before = len(data)
data = [a for a in data if a.get("id") not in dead_ids]
cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
print(f"Removed {before - len(data)} dead activities. Cache now has {len(data)} entries.")
