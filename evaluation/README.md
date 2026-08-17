# FitDash end-to-end evaluation

A self-contained, multi-turn evaluation harness for the **Training Copilot**,
built on MLflow's GenAI simulator + judges, following
[MLflow's multi-turn evaluation tutorial](https://mlflow.org/blog/multiturn-evaluation/).

It is **structurally separate** from the main app: it imports
`core.orchestrator` to drive the Copilot but is never imported by it, and it
changes none of the Copilot's behaviour.

## What it does

For each run it:

1. Creates a **new MLflow experiment** (`fitdash-e2e-<timestamp>`).
2. Has **gpt-5.4-mini** role-play **12 personas** — 3 sports (*cyclist*,
   *runner*, *swimmer*) × 2 levels (*hobby*, *ambitious*) × 2 personas each,
   with **Sophie** (the product-slide persona) among the hobby cyclists — each
   pursuing a different multi-turn goal. Every persona is made aware of the
   Copilot's real capabilities (`copilot_brief.py`).
3. Runs each conversation against the **live Copilot** (`FitDashOrchestrator`),
   tracing every turn to the experiment and grouping turns by session. The
   Copilot's specialist + tool-call structure is **reconstructed as spans** into
   each turn's trace (from the trace dict `run()` returns), so the tool calls are
   visible in the e2e experiment — the deep spans the agents really emit live in
   the separate `fitdash` experiment, out of this process's reach.
4. Scores each conversation with the tutorial's scorer set:
   - `ConversationCompleteness`, `UserFrustration`, `Safety` (built-in judges, **gpt-5.4-nano**)
   - `supportive_coaching_tone` — a `ConversationalGuidelines` assertion (**gpt-5.4-nano**)
   - `grounded_in_real_data` — a **deterministic, session-level code scorer** that
     inspects the conversation's **tool-call spans** (not the chat text) and reports
     whether the Copilot actually used its tools to fetch real data. No LLM.
5. Renders a **structured HTML report** → `reports/<experiment>.html`. The layout
   and styling are a **fixed template defined in `report.py`** (not model-authored);
   all hard data (header, scorecard, cohort stats, per-persona cards, transcripts) is
   filled in deterministically. Only the prose fields — executive summary, per-cohort
   blurbs, per-persona verdicts, recommendations — are written by the model, each via
   a small completion scoped to just that field's facts.

## Models (per the brief)

| Role | Model |
| --- | --- |
| Persona / user simulator | `gpt-5.4-mini-2026-03-17` |
| Scorers / judges | `gpt-5.4-nano-2026-03-17` |
| Report prose fields | `gpt-5.4-nano-2026-03-17` |

All three run on the **official OpenAI API**. `config.py` rewrites this
process's `OPENAI_API_KEY` to `OPENAI_OFFICIAL_API_KEY` from `.env` and clears
the KIT-gateway base URL, so MLflow's `openai:/…` provider reaches these models.
The Copilot's own agents run in separate processes and are unaffected.

## Install

Nothing extra is needed in practice: the app's own `requirements.txt` already
provides everything this harness imports (mlflow ≥ 3, openai, python-dotenv), and
`tqdm` arrives transitively. [`requirements.txt`](requirements.txt) here exists to
state the harness's own floor explicitly — mlflow **3.10+** for the multi-turn
simulator and the GenAI scorers, above the app's `>=3,<4`. Install it if you run
the evaluation against a leaner environment than the app's:

```bash
pip install -r evaluation/requirements.txt
```

The judges need `OPENAI_OFFICIAL_API_KEY` in `.env` — see *Models* above for why
they deliberately do not use the gateway the Copilot itself runs on.

## Running

From the **repo root**, with the stack up (`./run.sh`):

```bash
python -m evaluation.run_e2e                 # all 12 personas, ≤5 turns
python -m evaluation.run_e2e --smoke         # 1 persona, 2 turns (quick check)
python -m evaluation.run_e2e --type hobby_cyclist
python -m evaluation.run_e2e --sport swimmer      # all levels of one sport
python -m evaluation.run_e2e --level ambitious    # all sports at one level
python -m evaluation.run_e2e --personas 4 --max-turns 4 --workers 2
```

Output: a new experiment in the MLflow UI (`http://127.0.0.1:5001`), plus
`reports/<experiment>.html` and `<experiment>.facts.json` (also logged as run
artifacts). Reports are git-ignored — they are per-run artifacts.

## Aligned evaluation (`run_e2e_aligned.py`)

The original scorers were **validated against expert grades** following
Shankar et al., *"Who Validates the Validators?"* (UIST 2024): Claude Opus
validators graded every conversation of run `fitdash-e2e-20260729-185504`
blind, per criterion; judge↔expert agreement was measured; misaligned judges
were rewritten from the disagreement analysis. Full study + gold grades:
[`alignment/ALIGNMENT.md`](alignment/ALIGNMENT.md).

| Scorer | Agreement | Aligned change |
| --- | --- | --- |
| `safety` | 10/10 | kept built-in, unchanged |
| `user_frustration` | 9/10 | recovered frustration now = `resolved` |
| `conversation_completeness` | 8/10 | deflection / dropped half-asks fail |
| `supportive_coaching_tone` | 5/10 | one sarcastic/blaming line fails |
| `grounded_in_real_data` | 4/10 | claims must trace to tool evidence (was: tool count) |

```bash
python -m evaluation.run_e2e_aligned          # same e2e tests, aligned scorers
```

Identical personas/simulator/judge-model/report; scorer names unchanged;
experiments are `fitdash-e2e-aligned-<timestamp>` so both scorer sets compare
side-by-side in MLflow. The aligned judges live in `aligned_scorers.py`
(session-level `@scorer` functions feeding full transcripts — plus per-turn
tool evidence for grounding — to the same nano judge).

## Real-user evaluation (`run_users.py`)

The same idea, but over **real users** instead of simulated personas. Every chat
turn a logged-in user has is tracked live into that user's *own* MLflow experiment
`fitdash-user-<slug>` (by `core/user_tracking.py`), with the Copilot's tool calls
reconstructed as spans — the same shape as the e2e traces. `run_users.py` reads
those experiments, groups traces into conversations (by `session_id` = chat id),
and scores each:

- **`grounded_in_real_data`** — deterministic, from the conversation's tool-call
  spans (did the Copilot actually fetch data?). No LLM.
- an **LLM judge** (gpt-5.4-nano, the e2e judge model) over each transcript:
  completeness, frustration, safety, supportive coaching tone.

Then gpt-5.4-mini writes one combined HTML report across all users.

```bash
python -m evaluation.run_users                      # all users, with LLM judging
python -m evaluation.run_users --user marvin.kit@gmail.com
python -m evaluation.run_users --no-judge           # deterministic only (no OpenAI key needed)
python -m evaluation.run_users --max-convos 5 --no-report
```

Output: `reports/fitdash-users-<timestamp>.html` + `.facts.json`. Per-user
experiments appear in the MLflow UI alongside `fitdash` and the e2e experiments.

## Layout

| File | Purpose |
| --- | --- |
| `run_e2e.py` | persona (simulated) evaluation entrypoint |
| `run_users.py` | **real-user** evaluation entrypoint (reads per-user experiments) |
| `run_e2e_aligned.py` | e2e with **aligned scorers** (post-validation) |
| `aligned_scorers.py` | the aligned judge set from the alignment study |
| `alignment/` | the alignment study: rubric, gold grades, analysis, ALIGNMENT.md |
| `robustness/` | five probes against the live MCP stack — see [`robustness/README.md`](robustness/README.md) |
| `requirements.txt` | the harness's own dependency floor (see *Install*) |
| `config.py` | model constants + official-OpenAI routing + paths |
| `personas.py` | the 12 persona test cases (3 sports × 2 levels × 2) |
| `copilot_brief.py` | capability awareness injected into every persona |
| `agent_under_test.py` | `predict_fn` wrapping `FitDashOrchestrator.run` |
| `scorers.py` | 4 nano LLM judges + a deterministic tool-usage scorer |
| `report.py` | persona-run fact collection + fixed-template HTML (prose by nano) |
| `user_report.py` | real-user fact collection + scoring + gpt-5.4-mini HTML report |

> Per-user tracking is **best-effort** and lives in `core/user_tracking.py` (called
> from the chat endpoint after each turn). It routes each turn's trace to the user's
> experiment via MLflow's `trace_destination`, independent of the shared `fitdash`
> experiment the agents log to. Disable all tracing with `MLFLOW_TRACING=0`; change
> the per-user experiment prefix with `USER_EXPERIMENT_PREFIX`.
