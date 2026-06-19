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

## Layout

| File | Purpose |
| --- | --- |
| `run_e2e.py` | the one entrypoint |
| `config.py` | model constants + official-OpenAI routing + paths |
| `personas.py` | the 10 persona test cases (2 types × 5) |
| `copilot_brief.py` | capability awareness injected into every persona |
| `agent_under_test.py` | `predict_fn` wrapping `FitDashOrchestrator.run` |
| `scorers.py` | 4 nano LLM judges + a deterministic tool-usage scorer |
| `report.py` | MLflow fact collection + gpt-5.4-mini HTML report |
