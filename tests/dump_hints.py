"""Dump VIZ hints from the most recent N log entries."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
out_path = ROOT / "tests" / "logs" / "tmp_hints.txt"

with open(ROOT / ".logs" / "agent_interactions.jsonl", encoding="utf-8") as f:
    lines = f.readlines()

recent = []
for l in lines[-N*3:]:
    try:
        recent.append(json.loads(l))
    except Exception:
        pass
recent = recent[-N:]

with open(out_path, "w", encoding="utf-8") as out:
    for e in recent:
        ts = (e.get("ts") or "")[:19]
        q = (e.get("user_input") or "")[:60]
        hints = e.get("viz_hints") or {}
        metric = hints.get("metric", "")
        # New log format has 'tools' list; fall back to tool_calls for old entries
        tools_raw = e.get("tools") or [tc["tool"] for tc in (e.get("tool_calls") or [])]
        tools = [t.split("__", 1)[-1] if "__" in t else t for t in tools_raw]
        error = e.get("error") or ""
        out.write(f"[{ts[11:19]}] metric={metric!r:25s} tools={tools[:4]}\n")
        out.write(f"  Q: {q}\n")
        if error:
            out.write(f"  ERR: {error[:60]}\n")
        out.write("\n")

print(f"Wrote {len(recent)} entries to {out_path}")
