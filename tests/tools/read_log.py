import json, sys, os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

log = Path(__file__).parent.parent / ".logs" / "agent_interactions.jsonl"
lines = log.read_text(encoding="utf-8").strip().split("\n")
recent = lines[-30:]
for i, line in enumerate(recent):
    d = json.loads(line)
    ms = d["timing"].get("total_ms", 0)
    err = d["error"]
    print(f"[{i+1:02d}] {d['ts'][11:16]}  {d['n_tool_calls']:2d}tools  {ms//1000:3d}s  err={'YES' if err else 'no'}  {d['user_input'][:60]}")
    if err:
        print(f"      ERR: {err}")
    print(f"      {d['answer_preview'][:150]}")
    print()
