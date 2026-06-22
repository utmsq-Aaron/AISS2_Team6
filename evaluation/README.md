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
2. Has **gpt-5.4-mini** role-play **10 personas** (5 *ambitious triathletes*,
   5 *hobby road cyclists* — including the slide personas **Julian** and
   **Sophie**), each pursuing a different multi-turn goal. Every persona is made
   aware of the Copilot's real capabilities (`copilot_brief.py`).
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
5. Has **gpt-5.4-mini** write a **structured HTML report** combining the hard
   MLflow facts with its own analysis → `reports/<experiment>.html`.

## Models (per the brief)

| Role | Model |
| --- | --- |
| Persona / user simulator | `gpt-5.4-mini-2026-03-17` |
| Scorers / judges | `gpt-5.4-nano-2026-03-17` |
| Report writer | `gpt-5.4-mini-2026-03-17` |

All three run on the **official OpenAI API**. `config.py` rewrites this
process's `OPENAI_API_KEY` to `OPENAI_OFFICIAL_API_KEY` from `.env` and clears
the KIT-gateway base URL, so MLflow's `openai:/…` provider reaches these models.
The Copilot's own agents run in separate processes and are unaffected.

## Running

From the **repo root**, with the stack up (`./dev_stack.sh`):

```bash
python -m evaluation.run_e2e                 # all 10 personas, ≤5 turns
python -m evaluation.run_e2e --smoke         # 1 persona, 2 turns (quick check)
python -m evaluation.run_e2e --type hobby_cyclist
python -m evaluation.run_e2e --personas 4 --max-turns 4 --workers 2
```

Output: a new experiment in the MLflow UI (`http://127.0.0.1:5001`), plus
`reports/<experiment>.html` and `<experiment>.facts.json` (also logged as run
artifacts). Reports are git-ignored — they are per-run artifacts.

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
| `config.py` | model constants + official-OpenAI routing + paths |
| `personas.py` | the 10 persona test cases (2 types × 5) |
| `copilot_brief.py` | capability awareness injected into every persona |
| `agent_under_test.py` | `predict_fn` wrapping `FitDashOrchestrator.run` |
| `scorers.py` | 4 nano LLM judges + a deterministic tool-usage scorer |
| `report.py` | persona-run MLflow fact collection + gpt-5.4-mini HTML report |
| `user_report.py` | real-user fact collection + scoring + gpt-5.4-mini HTML report |

> Per-user tracking is **best-effort** and lives in `core/user_tracking.py` (called
> from the chat endpoint after each turn). It routes each turn's trace to the user's
> experiment via MLflow's `trace_destination`, independent of the shared `fitdash`
> experiment the agents log to. Disable all tracing with `MLFLOW_TRACING=0`; change
> the per-user experiment prefix with `USER_EXPERIMENT_PREFIX`.
