"""One-command end-to-end evaluation of the FitDash Training Copilot.

Each invocation creates a NEW MLflow experiment, has gpt-5.4-mini simulate
multi-turn conversations for every persona against the live Copilot, scores each
conversation with gpt-5.4-nano judges, and finally has gpt-5.4-mini write a
structured HTML report of the run.

Prerequisites: the full A2A stack and the MLflow tracking server must be running
(``./dev_stack.sh`` from the repo root). Run from the repo root:

    python -m evaluation.run_e2e                 # all 10 personas, 5 turns each
    python -m evaluation.run_e2e --smoke         # 1 persona, 2 turns (plumbing check)
    python -m evaluation.run_e2e --type hobby_cyclist
    python -m evaluation.run_e2e --personas 4 --max-turns 4 --workers 2
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys

# Route this process's openai:/ model calls to the official OpenAI API BEFORE
# anything imports MLflow/openai or the Copilot (whose .env points at the KIT
# gateway). Re-applied again just before evaluation to be safe.
from . import config

config.apply_openai_routing()


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _preflight(orch) -> None:
    """Fail fast with actionable guidance if the stack isn't up."""
    import mlflow

    tracking = config.resolve_tracking_uri()
    try:
        mlflow.set_tracking_uri(tracking)
        mlflow.search_experiments(max_results=1)
    except Exception as e:  # pragma: no cover - environment guard
        sys.exit(
            f"✗ Cannot reach the MLflow tracking server at {tracking}: {e}\n"
            f"  Start the stack first:  ./dev_stack.sh"
        )

    from .agent_under_test import orchestrator_reachable

    n_tools = orchestrator_reachable(orch)
    if n_tools <= 0:
        sys.exit(
            "✗ The Training Copilot (A2A orchestrator on :9000) is not reachable.\n"
            "  Start the stack first:  ./dev_stack.sh"
        )
    print(f"✓ MLflow at {tracking} | Copilot up ({n_tools} tools visible)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FitDash end-to-end persona evaluation.")
    ap.add_argument("--type", choices=["ambitious_triathlete", "hobby_cyclist"],
                    default=None, help="only run one persona type (default: both)")
    ap.add_argument("--personas", type=int, default=None,
                    help="cap the number of personas (after --type filter)")
    ap.add_argument("--max-turns", type=int, default=5, help="max turns per conversation")
    ap.add_argument("--workers", type=int, default=3,
                    help="parallel conversations (MLFLOW_GENAI_SIMULATOR_MAX_WORKERS)")
    ap.add_argument("--experiment-name", default=None, help="override the experiment name")
    ap.add_argument("--no-report", action="store_true", help="skip the HTML report")
    ap.add_argument("--smoke", action="store_true",
                    help="quick plumbing check: 1 persona, 2 turns")
    args = ap.parse_args(argv)

    if args.smoke:
        args.personas = args.personas or 1
        args.max_turns = min(args.max_turns, 2)
        args.workers = 1

    # The simulator reads this env var when it builds its thread pool.
    import os

    os.environ["MLFLOW_GENAI_SIMULATOR_MAX_WORKERS"] = str(max(1, args.workers))

    import mlflow
    from mlflow.genai.simulators import ConversationSimulator

    from . import personas as personas_mod
    from . import report as report_mod
    from .agent_under_test import get_orchestrator, make_predict_fn
    from .scorers import build_scorers

    orch = get_orchestrator()
    _preflight(orch)

    test_cases, persona_records = personas_mod.build_test_cases(
        persona_type=args.type, limit=args.personas
    )
    if not test_cases:
        sys.exit("✗ No personas selected.")

    ts = _timestamp()
    exp_name = args.experiment_name or f"{config.EXPERIMENT_PREFIX}-{ts}"
    mlflow.set_tracking_uri(config.resolve_tracking_uri())
    mlflow.set_experiment(exp_name)
    experiment = mlflow.get_experiment_by_name(exp_name)

    print(
        f"\n▶ Experiment '{exp_name}'  ·  {len(test_cases)} persona(s)  ·  "
        f"≤{args.max_turns} turns  ·  {args.workers} worker(s)\n"
        f"  simulator={config.SIMULATOR_MODEL_RAW}  judges={config.JUDGE_MODEL_RAW}\n"
    )

    config.apply_openai_routing()  # ensure routing wasn't clobbered by imports
    simulator = ConversationSimulator(
        test_cases=test_cases,
        max_turns=args.max_turns,
        user_model=config.SIMULATOR_MODEL,
    )
    scorers = build_scorers()
    predict_fn = make_predict_fn(orch)

    with mlflow.start_run(run_name=f"e2e-{ts}") as run:
        run_id = run.info.run_id
        results = mlflow.genai.evaluate(
            data=simulator, predict_fn=predict_fn, scorers=scorers
        )

        print("\n── Aggregate metrics ──")
        for k, v in (getattr(results, "metrics", {}) or {}).items():
            print(f"  {k}: {v}")

        report_path = None
        if not args.no_report:
            run_meta = {
                "run_id": run_id,
                "timestamp": ts,
                "max_turns": args.max_turns,
            }
            print("\n✍  Rendering HTML report (template + prose by",
                  config.PERSONA_REPORT_MODEL_RAW, ")…")
            facts = report_mod.collect_facts(experiment, results, persona_records, run_meta)
            config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            facts_path = config.REPORTS_DIR / f"{exp_name}.facts.json"
            import json

            facts_path.write_text(json.dumps(facts, indent=2, default=str), encoding="utf-8")
            try:
                html = report_mod.render_html(facts)
                report_path = config.REPORTS_DIR / f"{exp_name}.html"
                report_path.write_text(html, encoding="utf-8")
                mlflow.log_artifact(str(report_path), artifact_path="report")
            except Exception as e:  # report failure shouldn't sink the whole run
                print(f"  ⚠ report generation failed: {e}")
            mlflow.log_artifact(str(facts_path), artifact_path="report")

    print("\n✓ Done.")
    print(f"  Experiment:  {exp_name}  (id {experiment.experiment_id})")
    print(f"  MLflow UI:   {config.resolve_tracking_uri()}/#/experiments/{experiment.experiment_id}")
    if report_path:
        print(f"  HTML report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
