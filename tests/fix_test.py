"""Focused regression test for the two previously-failing queries."""
import sys
import io
import json
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.host import ToolHost
from core.orchestrator import FitDashOrchestrator

host = ToolHost()
orc = FitDashOrchestrator(host)

tests = [
    ("hr_zones",
     "Show me the heart rate zone distribution of my last run"),
    ("cadence",
     "What was my cadence and HR zones during my last run?"),
    ("permission_check",
     "Fetch my last Garmin activity detail"),
]

results = []
for label, query in tests:
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"QUERY: {query}")
    t0 = time.time()
    answer, trace = orc.run(query, [])
    elapsed = time.time() - t0
    tools = [r["tool"] for r in (trace.get("tool_calls") or [])]
    viz = trace.get("viz_hints") or {}

    print(f"Tools used ({len(tools)}): {tools}")
    print(f"VIZ hints: {viz}")
    print(f"Elapsed: {elapsed:.1f}s")
    print(f"ANSWER:\n{answer[:500]}")

    # Check: no "shall I proceed" type text in the answer
    lower = answer.lower()
    asked_permission = any(p in lower for p in [
        "shall i", "would you like", "should i proceed",
        "would you like me to fetch", "want me to", "go ahead"
    ])
    print(f"\nAsked permission: {'❌ YES (BUG)' if asked_permission else '✅ NO'}")
    print(f"Tools called:     {'✅ YES' if len(tools) >= 2 else '❌ TOO FEW (expected ≥2)'}")

    results.append({
        "label": label, "query": query, "elapsed_s": round(elapsed, 1),
        "tools": tools, "viz_hints": viz,
        "asked_permission": asked_permission,
        "tool_count": len(tools),
        "answer": answer[:800],
    })

out = Path(__file__).parent / "fix_test_results.json"
out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nResults written to {out}")

# Summary
print("\n" + "="*60)
print("SUMMARY")
for r in results:
    ok = not r["asked_permission"] and r["tool_count"] >= 2
    print(f"  {'✅' if ok else '❌'} {r['label']}: {r['tool_count']} tools, "
          f"asked_permission={r['asked_permission']}")
