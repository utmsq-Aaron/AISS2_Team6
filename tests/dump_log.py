"""Dump recent log entries to a file, bypassing stdout encoding issues."""
import json, sys, os

log_path = ".logs/agent_interactions.jsonl"
out_path = "tests/logs/tmp_log_dump.txt"

with open(log_path, encoding="utf-8") as f:
    lines = f.readlines()

N = int(sys.argv[1]) if len(sys.argv) > 1 else 28
recent = []
for l in lines[-N*3:]:
    try:
        recent.append(json.loads(l))
    except Exception:
        pass
recent = recent[-N:]

with open(out_path, "w", encoding="utf-8") as out:
    out.write(f"Total log entries: {len(recent)}\n\n")
    for e in recent:
        n_tools = e.get("n_tool_calls", "?")
        error = e.get("error") or ""
        q = (e.get("user_input") or "")[:65]
        ans = (e.get("answer_preview") or "")[:90].replace("\n", " ")
        timing = e.get("timing") or {}
        total_s = round(sum(timing.values()) / 1000, 1) if timing else 0
        status = "ERR" if error else "OK "
        ts = (e.get("ts") or "?")[11:19]
        out.write(f"[{ts}] {status} tools={n_tools} {total_s}s\n")
        out.write(f"  Q: {q}\n")
        out.write(f"  A: {ans}\n")
        if error:
            out.write(f"  !! {error[:120]}\n")
        out.write("\n")

print(f"Wrote {len(recent)} entries to {out_path}")
