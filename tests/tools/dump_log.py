"""Dump recent log entries to a file, bypassing stdout encoding issues."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
log_path = ROOT / ".logs" / "agent_interactions.jsonl"
out_path = ROOT / "tests" / "logs" / "tmp_log_dump.txt"

if not log_path.exists():
    print(f"No log at {log_path} — run a chat turn first.")
    raise SystemExit(0)

with open(log_path, encoding="utf-8") as f:
    lines = f.readlines()

N = int(sys.argv[1]) if len(sys.argv) > 1 else 28
recent = []
for line in lines[-N * 3:]:
    try:
        recent.append(json.loads(line))
    except Exception:
        pass
recent = recent[-N:]

out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", encoding="utf-8") as out:
    out.write(f"Total log entries: {len(recent)}\n\n")
    for e in recent:
        error = e.get("error") or ""
        status = "ERR" if error else "OK "
        ts = (e.get("ts") or "?")[11:19]
        agents = ",".join(a for a in (e.get("agents") or []) if a)
        out.write(f"[{ts}] {status} tools={e.get('n_tool_calls', '?')}"
                  f"{'  agents=' + agents if agents else ''}\n")
        out.write(f"  Q: {(e.get('user_input') or '')[:65]}\n")
        out.write(f"  A: {(e.get('answer') or '')[:90].replace(chr(10), ' ')}\n")
        if error:
            out.write(f"  !! {error[:120]}\n")
        out.write("\n")

print(f"Wrote {len(recent)} entries to {out_path}")
