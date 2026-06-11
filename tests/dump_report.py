"""Dump VIZ hints from earlier comprehensive test report."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
out = ROOT / "tests" / "logs" / "viz_hint_summary.txt"

with open(ROOT / "tests" / "logs" / "run_20260611_report.json", encoding="utf-8") as f:
    results = json.load(f)

with open(out, "w", encoding="utf-8") as f:
    for r in results:
        hints = r.get("viz_hints") or {}
        metric = hints.get("metric", "")
        tools = [t.split("__", 1)[-1] if "__" in t else t for t in r.get("tools", [])]
        q = (r.get("query") or "")[:65]
        issues = r.get("issues") or []
        err = r.get("error") or ""
        status = "ERR" if err else ("ISSUE" if issues else "OK")
        f.write(f"[{status}] {r['id']}: metric={metric!r} tools={tools}\n")
        f.write(f"       Q: {q}\n")
        if issues:
            for iss in issues[:2]:
                f.write(f"       !! {iss[:80]}\n")
        if err:
            f.write(f"       ERR: {err[:60]}\n")
        f.write("\n")

print(f"Wrote to {out}")
