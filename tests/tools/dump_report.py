"""Dump VIZ hints out of a saved test-report JSON.

Takes the report path as an argument; with none, picks the newest
``*_report.json`` in tests/logs/ (where the viz-quality suite writes them).

    python tests/tools/dump_report.py [tests/logs/run_<ts>_report.json]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "tests" / "logs"
out = LOGS / "viz_hint_summary.txt"

if len(sys.argv) > 1:
    report = Path(sys.argv[1])
else:
    candidates = sorted(LOGS.glob("*_report.json"), key=lambda p: p.stat().st_mtime)
    report = candidates[-1] if candidates else None

if report is None or not report.exists():
    print(f"No report found — pass one explicitly, or run the viz-quality suite first.\n"
          f"Looked in: {LOGS}")
    raise SystemExit(0)

with open(report, encoding="utf-8") as f:
    results = json.load(f)

out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    for r in results:
        hints = r.get("viz_hints") or {}
        metric = hints.get("metric", "")
        tools = [t.split("__", 1)[-1] if "__" in t else t for t in r.get("tools", [])]
        q = (r.get("query") or "")[:65]
        issues = r.get("issues") or []
        err = r.get("error") or ""
        status = "ERR" if err else ("ISSUE" if issues else "OK")
        f.write(f"[{status}] {r.get('id')}: metric={metric!r} tools={tools}\n")
        f.write(f"       Q: {q}\n")
        if issues:
            for iss in issues[:2]:
                f.write(f"       !! {iss[:80]}\n")
        if err:
            f.write(f"       ERR: {err[:60]}\n")
        f.write("\n")

print(f"Read {report}\nWrote to {out}")
