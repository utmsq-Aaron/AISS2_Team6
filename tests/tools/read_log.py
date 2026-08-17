"""Print the most recent turns from .logs/agent_interactions.jsonl.

One line per turn plus the answer preview. Schema is whatever
``core.orchestrator._write_log`` writes: run_id, ts, model, user_input,
n_tool_calls, tools, agents, error, has_route, answer.
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

# .logs lives at the repo root, two levels up from tests/tools/.
log = Path(__file__).resolve().parents[2] / ".logs" / "agent_interactions.jsonl"
if not log.exists():
    print(f"No log at {log} — run a chat turn first.")
    raise SystemExit(0)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
lines = log.read_text(encoding="utf-8").strip().split("\n")

for i, line in enumerate(lines[-N:]):
    try:
        d = json.loads(line)
    except Exception:
        continue
    err = d.get("error")
    ts = (d.get("ts") or "?")[11:16]
    agents = ",".join(a for a in (d.get("agents") or []) if a)
    print(f"[{i + 1:02d}] {ts}  {d.get('n_tool_calls', 0):2d}tools  "
          f"err={'YES' if err else 'no'}  {(d.get('user_input') or '')[:60]}")
    if agents:
        print(f"      agents: {agents}")
    if err:
        print(f"      !! {str(err)[:150]}")
    print(f"      {(d.get('answer') or '')[:150]}")
