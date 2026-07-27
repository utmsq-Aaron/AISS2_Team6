"""Quick focused test — runs key queries and reports tool calls + VIZ hints."""
import json
import sys
import io
import time
from pathlib import Path

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
from core.orchestrator import FitDashOrchestrator
from core.host import ToolHost

FOCUSED = [
    ("no_viz_lookup",   "Which of my hikes reached the highest altitude?"),
    ("viz_show",        "Show me my recent runs"),
    ("negative_query",  "Did I run a marathon in 2020?"),
    ("recovery",        "Am I ready to train hard today? Check my HRV, body battery and sleep"),
    ("gps_map",         "Show me the GPS map of my last run"),
    ("hr_zones",        "Show me the heart rate zone distribution of my last run"),
    ("training_load",   "What is my current training load? Am I overtraining?"),
    ("last_run",        "Give me detailed stats of my last run, laps and splits"),
    ("weekly_volume",   "Show me my weekly training volume over the last 8 weeks"),
    ("personal_bests",  "What are my personal bests in running?"),
    ("cadence",         "What was my cadence and HR zones during my last run?"),
    ("compare",         "How does my last run compare to my typical runs?"),
    ("marathon_fake",   "Show me my marathon from last Christmas"),
    ("week_checkin",    "Weekly check-in: training and recovery this week"),
]

OUT = Path("tests/quick_test_results.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

def run():
    host = ToolHost()
    orch = FitDashOrchestrator(host=host)
    results = []

    print(f"\n{'='*70}\nFocused Test ({len(FOCUSED)} queries)\n{'='*70}")
    for label, query in FOCUSED:
        t0 = time.perf_counter()
        try:
            answer, trace = orch.run(query, history=[], progress_cb=None)
        except Exception as e:
            print(f"[{label}] EXCEPTION: {e}")
            results.append({"label": label, "query": query, "error": str(e)})
            continue
        elapsed = time.perf_counter() - t0
        tools = [tc["tool"] for tc in (trace.get("tool_calls") or [])]
        hints = trace.get("viz_hints") or {}
        errs  = [tc["tool"] + ": " + str(tc["error"])[:60]
                 for tc in (trace.get("tool_calls") or []) if tc.get("error")]

        results.append({
            "label": label, "query": query, "elapsed_s": round(elapsed, 1),
            "tools": tools, "viz_hints": hints, "errors": errs,
            "answer": answer[:600],
        })

        print(f"\n[{label}] ({elapsed:.1f}s)")
        print(f"  Q: {query}")
        print(f"  Tools: {[t.split('__',1)[-1] for t in tools]}")
        print(f"  VIZ:   {hints}")
        if errs:
            print(f"  ERRS:  {errs}")
        # Show first 300 chars, ascii-safe
        safe_ans = answer[:300].encode("ascii", "replace").decode("ascii")
        print(f"  A: {safe_ans}")

    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults saved to {OUT}")

if __name__ == "__main__":
    run()
