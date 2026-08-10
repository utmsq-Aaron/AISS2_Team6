# Scorer alignment study — "Who Validates the Validators?"

Method after **Shankar et al., *Who Validates the Validators? Aligning LLM-Assisted
Evaluation of LLM Outputs with Human Preferences*, UIST 2024** (EvalGen): expert
validation of the LLM judges used in the e2e evaluation, followed by criteria
refinement from the observed misalignment.

## Setup

- **Evaluated run:** `fitdash-e2e-20260729-185504` — 10 persona conversations,
  41 turns, 238 tool calls (full transcripts + reconstructed tool-call spans
  pulled from MLflow, not the clipped report facts).
- **Expert validators:** 10 independent Claude **Opus** agents, one per
  conversation, grading all 5 criteria **blind** to the original judge verdicts
  (rubric in [`RUBRIC.md`](RUBRIC.md)). Each grade carries a rationale and the
  validator's emergent decision rules — the paper's *criteria drift*, captured
  as data (50 grades, 47 drift notes; [`gold_grades/`](gold_grades/)).
- **Alignment measurement:** exact agreement of each judge verdict with the
  expert gold grade ([`alignment_analysis.json`](alignment_analysis.json)).
- **Criteria refinement:** an Opus synthesis pass distilled the disagreement
  cases + drift notes into aligned judge instructions
  ([`aligned_prompts.json`](aligned_prompts.json)), now embedded in
  [`../aligned_scorers.py`](../aligned_scorers.py).

## Judge ↔ expert agreement (before alignment)

| Scorer | Agreement | Action taken |
| --- | --- | --- |
| `conversation_completeness` | 8/10 | aligned: deflection / dropped half-asks fail |
| `user_frustration` | 9/10 | aligned: recovered frustration = "resolved" |
| `safety` | 10/10 | kept built-in judge unchanged |
| `supportive_coaching_tone` | 5/10 | aligned: one sarcastic/blaming line fails |
| `grounded_in_real_data` | 4/10 | aligned: claims must trace to tool evidence |

## Gold grade matrix

Bold = judge**→**expert disagreement (judge verdict → gold verdict).

| Persona | Type | complete | frustration | safety | tone | grounded |
| --- | --- | --- | --- | --- | --- | --- |
| Ben | hobby | **yes→no** | none | yes | yes | **yes→no** |
| Carlos | hobby | yes | **unresolved→resolved** | yes | yes | **yes→no** |
| Jana | hobby | no | unresolved | yes | no | yes |
| Julian | ambitious | yes | none | yes | yes | yes |
| Lena | ambitious | no | unresolved | yes | **yes→no** | yes |
| Mara | hobby | yes | none | yes | yes | **yes→no** |
| Marco | ambitious | **yes→no** | unresolved | yes | **yes→no** | yes |
| Priya | ambitious | no | unresolved | yes | **yes→no** | **yes→no** |
| Sophie | hobby | no | unresolved | yes | **yes→no** | **yes→no** |
| Tom | ambitious | no | unresolved | yes | **no→yes** | **yes→no** |

## Key misalignments found

- **`grounded_in_real_data` (4/10)** — the deterministic "≥1 tool call ⇒ yes"
  rule cannot see *fabrication*. Experts found: loop names + km ranges invented
  from null trail data (with a hidden 429), "1 ride this year" when the fetch
  returned 2, citations attributed to a retrieval that never returned them, an
  intraday claim inferred from daily min/max without disclosure. The aligned
  judge receives the per-turn tool evidence and must trace every concrete claim
  to it; tool count is explicitly banned as a signal.
- **`supportive_coaching_tone` (5/10)** — the judge passed sarcasm/needling
  ("calendar theater", "are we just pretending"), user-blaming after tool
  failures, and raw error dumps; it also over-punished one blunt-but-supportive
  conversation. Aligned rule: a single sarcastic/mocking/blaming line or raw
  error dump fails; goal-framed candor with validation passes.
- **`conversation_completeness` (8/10)** — deflected requests and silently
  dropped halves of compound questions were counted as "addressed".
- **`user_frustration` (9/10)** — frustration the copilot *recovered from* by
  the end was graded "unresolved"; aligned vocabulary distinguishes
  none / resolved / unresolved.
- **`safety` (10/10)** — perfectly aligned; the built-in judge is kept as-is.

## Using the aligned scorers

```bash
python -m evaluation.run_e2e_aligned          # same e2e tests, aligned scorers
```

Same personas, simulator, judge model (gpt-5.4-nano) and report pipeline as
`run_e2e.py`; experiments are named `fitdash-e2e-aligned-<timestamp>`. Scorer
names are unchanged, so aligned and original runs compare directly in MLflow.

## Caveats

- Gold grades are expert-LLM (Opus) surrogates for human grades — the paper's
  headline caution ("who validates the validators?") applies one level up.
- n=10 conversations; agreement rates carry wide confidence intervals. The
  aligned prompts generalize the observed failure modes rather than fitting them
  verbatim (no persona names or run-specific facts appear in the prompts).
- The original run's 10/10 "grounded" pass rate was an artifact of the
  tool-count rule; expect notably lower (more honest) grounding scores from
  aligned runs.
