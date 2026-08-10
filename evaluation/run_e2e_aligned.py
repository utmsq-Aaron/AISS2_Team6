"""End-to-end evaluation with ALIGNED scorers — same tests, validated judges.

Identical to ``run_e2e.py`` — same 12 personas, same simulator model, same flow,
same fixed-template report — except the conversations are scored by the **aligned
scorer set** (``aligned_scorers.py``). Those scorers were calibrated against
expert validation of the ``fitdash-e2e-20260729-185504`` run, following
Shankar et al., *"Who Validates the Validators?"* (UIST 2024): expert (Claude
Opus) validators graded every conversation per criterion blind to the original
judge verdicts, judge↔expert alignment was measured, and each judge prompt was
rewritten from the disagreement analysis and the validators' criteria-drift
notes. See ``alignment/ALIGNMENT.md`` for the full study.

Experiments are named ``fitdash-e2e-aligned-<timestamp>`` so aligned runs sit
side-by-side with (never mixed into) the original e2e experiments.

Run from the repo root with the stack up (``./dev_stack.sh``):

    python -m evaluation.run_e2e_aligned                 # all 12 personas
    python -m evaluation.run_e2e_aligned --smoke         # 1 persona, 2 turns
    python -m evaluation.run_e2e_aligned --type hobby_cyclist
    python -m evaluation.run_e2e_aligned --sport swimmer
    python -m evaluation.run_e2e_aligned --level ambitious
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys

from . import config
from .personas import LEVELS, PERSONA_TYPES, SPORTS

config.apply_openai_routing()

ALIGNED_EXPERIMENT_PREFIX = f"{config.EXPERIMENT_PREFIX}-aligned"


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
    ap = argparse.ArgumentParser(
        description="FitDash end-to-end persona evaluation with ALIGNED scorers."
    )
    ap.add_argument("--type", choices=list(PERSONA_TYPES),
                    default=None, help="only run one persona type (default: all 6)")
    ap.add_argument("--sport", choices=list(SPORTS),
                    default=None, help="only run one sport (default: all 3)")
    ap.add_argument("--level", choices=list(LEVELS),
                    default=None, help="only run one level (default: both)")
    ap.add_argument("--personas", type=int, default=None,
                    help="cap the number of personas (after the type/sport/level filters)")
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

    import os

    os.environ["MLFLOW_GENAI_SIMULATOR_MAX_WORKERS"] = str(max(1, args.workers))

    import mlflow
    from mlflow.genai.simulators import ConversationSimulator

    from . import personas as personas_mod
    from . import report as report_mod
    from .agent_under_test import get_orchestrator, make_predict_fn
    from .aligned_scorers import build_aligned_scorers

    orch = get_orchestrator()
    _preflight(orch)

    test_cases, persona_records = personas_mod.build_test_cases(
        persona_type=args.type, sport=args.sport, level=args.level, limit=args.personas
    )
    if not test_cases:
        sys.exit("✗ No personas selected.")

    ts = _timestamp()
    exp_name = args.experiment_name or f"{ALIGNED_EXPERIMENT_PREFIX}-{ts}"
    mlflow.set_tracking_uri(config.resolve_tracking_uri())
    mlflow.set_experiment(exp_name)
    experiment = mlflow.get_experiment_by_name(exp_name)

    print(
        f"\n▶ ALIGNED experiment '{exp_name}'  ·  {len(test_cases)} persona(s)  ·  "
        f"≤{args.max_turns} turns  ·  {args.workers} worker(s)\n"
        f"  simulator={config.SIMULATOR_MODEL_RAW}  judges={config.JUDGE_MODEL_RAW} (aligned prompts)\n"
    )

    config.apply_openai_routing()  # ensure routing wasn't clobbered by imports
    simulator = ConversationSimulator(
        test_cases=test_cases,
        max_turns=args.max_turns,
        user_model=config.SIMULATOR_MODEL,
    )
    scorers = build_aligned_scorers()
    predict_fn = make_predict_fn(orch)

    with mlflow.start_run(run_name=f"e2e-aligned-{ts}") as run:
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
            facts["experiment"]["scorer_set"] = "aligned (see evaluation/alignment/ALIGNMENT.md)"
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
