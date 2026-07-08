"""Score REAL users and write an HTML report from their per-user MLflow experiments.

The counterpart to ``run_e2e.py``: instead of simulating personas, this reads the
conversations real users actually had with the Copilot — each tracked live in its
own experiment ``fitdash-user-<slug>`` by ``core.user_tracking`` — groups them into
conversations, scores each (deterministic tool-grounding + a gpt-5.4-nano judge over
the transcript), and has gpt-5.4-mini write one combined HTML report.

Prerequisites: the MLflow tracking server must be reachable and users must have
chatted (so per-user experiments exist). Run from the repo root:

    python -m evaluation.run_users                      # all users, with LLM judging
    python -m evaluation.run_users --user marvin.kit@gmail.com
    python -m evaluation.run_users --no-judge           # deterministic only (no LLM, fast/free)
    python -m evaluation.run_users --max-convos 5 --no-report
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys

from . import config

# Note: OpenAI routing is applied lazily inside main() only when the LLM judge or
# the HTML report is actually needed — so `--no-judge --no-report` runs with no
# OpenAI key at all (pure deterministic grounding from the traces).


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FitDash real-user evaluation report.")
    ap.add_argument("--user", action="append", default=None,
                    help="only this user/email (repeatable); matches the experiment slug")
    ap.add_argument("--no-judge", action="store_true",
                    help="skip the LLM judge — deterministic grounding only (no OpenAI calls)")
    ap.add_argument("--max-convos", type=int, default=None,
                    help="cap conversations scored per user")
    ap.add_argument("--no-report", action="store_true", help="skip the HTML report")
    args = ap.parse_args(argv)

    import mlflow

    from . import user_report as ur

    # Only the LLM judge / HTML report need the official-OpenAI models.
    if not args.no_judge or not args.no_report:
        config.apply_openai_routing()

    tracking = config.resolve_tracking_uri()
    try:
        mlflow.set_tracking_uri(tracking)
        mlflow.search_experiments(max_results=1)
    except Exception as e:  # pragma: no cover
        sys.exit(f"✗ Cannot reach the MLflow tracking server at {tracking}: {e}\n"
                 f"  Start the stack first:  ./server-start.sh  (or ./dev_stack.sh)")

    exps = ur.list_user_experiments()
    if args.user:
        from core.user_tracking import experiment_name
        wanted = {experiment_name(u) for u in args.user}
        exps = [e for e in exps if e.name in wanted]
    if not exps:
        sys.exit("✗ No per-user experiments found (prefix "
                 f"'{__import__('core.user_tracking', fromlist=['experiment_prefix']).experiment_prefix()}-'). "
                 "Have users chatted yet?")

    print(f"✓ MLflow at {tracking} | {len(exps)} user experiment(s)"
          + ("" if args.no_judge else f" | judge={config.JUDGE_MODEL_RAW}"))

    ts = _timestamp()
    users = []
    for exp in sorted(exps, key=lambda e: e.name):
        print(f"  · scoring {exp.name} …")
        if not args.no_judge:
            config.apply_openai_routing()  # ensure routing wasn't clobbered by imports
        users.append(ur.collect_user_facts(exp, judge=not args.no_judge, max_convos=args.max_convos))

    totals = {
        "users": len(users),
        "conversations": sum(u["n_conversations"] for u in users),
        "turns": sum(u["n_turns"] for u in users),
    }
    facts = {
        "generated_at": ts,
        "tracking_uri": tracking,
        "config": {
            "judge_model": None if args.no_judge else config.JUDGE_MODEL_RAW,
            "report_model": config.REPORT_MODEL_RAW,
        },
        "totals": totals,
        "users": users,
    }

    print("\n── Summary ──")
    for u in users:
        g = u["grounding"]
        print(f"  {u['user']}: {u['n_conversations']} convo(s), {u['n_turns']} turn(s), "
              f"grounding {int(g['rate']*100)}%, {u['error_turns']} error turn(s)")

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    base = config.REPORTS_DIR / f"fitdash-users-{ts}"
    base.with_suffix(".facts.json").write_text(json.dumps(facts, indent=2, default=str), encoding="utf-8")
    print(f"\n  facts:  {base.with_suffix('.facts.json')}")

    if not args.no_report:
        print(f"✍  Writing HTML report with {config.REPORT_MODEL_RAW} …")
        try:
            html = ur.render_html(facts)
            base.with_suffix(".html").write_text(html, encoding="utf-8")
            print(f"  report: {base.with_suffix('.html')}")
        except Exception as e:
            print(f"  ⚠ report generation failed: {e}")

    print("\n✓ Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
