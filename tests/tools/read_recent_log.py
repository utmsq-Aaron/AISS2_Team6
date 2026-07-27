"""Read recent agent_interactions.jsonl entries (correct log format)."""
import json, sys, os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

log_path = ".logs/agent_interactions.jsonl"
with open(log_path, encoding="utf-8") as f:
    lines = f.readlines()

# Show the last N entries
N = int(sys.argv[1]) if len(sys.argv) > 1 else 25
recent = []
for l in lines[-N*3:]:
    try:
        e = json.loads(l)
        recent.append(e)
    except Exception:
        pass
recent = recent[-N:]

print(f"Last {len(recent)} log entries:\n")
for e in recent:
    n_tools = e.get("n_tool_calls", "?")
    error = e.get("error") or ""
    q = (e.get("user_input") or "")[:65]
    ans = (e.get("answer_preview") or "")[:90]
    timing = e.get("timing") or {}
    total_ms = sum(timing.values()) if timing else 0
    status = "ERR" if error else "OK "
    print(f"[{e.get('ts','?')[11:19]}] {status} tools={n_tools} {total_ms}ms")
    print(f"  Q: {q}")
    print(f"  A: {ans}")
    if error:
        print(f"  !! {error}")
    print()
