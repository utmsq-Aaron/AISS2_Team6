"""Read recent agent_interactions.jsonl entries as a compact Q/A listing.

Same source as read_log.py, formatted question-first for skimming a session.
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

log_path = Path(__file__).resolve().parents[2] / ".logs" / "agent_interactions.jsonl"
if not log_path.exists():
    print(f"No log at {log_path} — run a chat turn first.")
    raise SystemExit(0)

lines = log_path.read_text(encoding="utf-8").strip().split("\n")

# Show the last N entries
N = int(sys.argv[1]) if len(sys.argv) > 1 else 25
recent = []
for line in lines[-N * 3:]:
    try:
        recent.append(json.loads(line))
    except Exception:
        pass
recent = recent[-N:]

print(f"Last {len(recent)} log entries:\n")
for e in recent:
    error = e.get("error") or ""
    status = "ERR" if error else "OK "
    agents = ",".join(a for a in (e.get("agents") or []) if a)
    print(f"[{(e.get('ts') or '?')[11:19]}] {status} tools={e.get('n_tool_calls', '?')}"
          f"{'  agents=' + agents if agents else ''}")
    print(f"  Q: {(e.get('user_input') or '')[:65]}")
    print(f"  A: {(e.get('answer') or '')[:90]}")
    if error:
        print(f"  !! {error}")
    print()
