# Tests

Two different things live here, and the split matters:

| | `tests/*.py` | `tests/tools/*.py` |
|---|---|---|
| **What** | Tests — they assert, grade, and can fail | Debug/inspection utilities — they only print or dump |
| **Exit code** | Meaningful (non-zero on failure) | Always 0; there is nothing to fail |
| **Needs the stack** | Mostly yes (one exception, below) | Yes, to have anything to look at |
| **Purpose** | Prove the system behaves | Look at what the system just did |

If you are evaluating this project, `tests/` is what you want. `tests/tools/` is
scaffolding we used while building, kept because it is still handy.

---

## Running

From the repo root, with the project's virtualenv:

```bash
.venv/bin/python tests/<script>.py
```

Most tests need the **live stack** (MCP servers + agents + an LLM gateway) — start it
with `./run.sh` and wait until it reports ready. The one exception is
`test_agent_layer.py`, which is fully deterministic and needs nothing at all.

---

## Tests

### `test_agent_layer.py` — the primary regression test ★

**Runs offline.** No LLM gateway, no MCP servers, no network: it substitutes a fake
chat model and a fake MCP host, then asserts the contracts the rest of the app
depends on:

1. `core/agent_trace.build_trace()` emits the exact trace shape the frontend, the
   chart service and the route map read.
2. Peer `sub_artifacts` (agent-to-agent mesh calls) are flattened into `agents` and
   `tool_calls` rather than being lost.
3. `_peers_for()` honours the depth limit and the env toggle.

Run this after touching `core/agent_trace.py`, `core/orchestrator*.py` or `agents/`.
Fast, deterministic, and it catches the breakages that actually hurt.

```bash
.venv/bin/python tests/test_agent_layer.py
```

### End-to-end query tests

These fire real chat queries at the orchestrator and grade the answers. They need the
full stack up and they consume LLM tokens.

| Script | What it covers |
|---|---|
| `test_viz_quality.py` | The big one — 25 representative queries, graded on tool choice, chart data and route maps (codes below) |
| `test_viz_quick.py` | Short version of the above, for fast iteration |
| `test_viz_renderers.py` | Chart renderers in isolation (`core/viz_telegram.py` → PNG bytes) |
| `test_orchestrator.py` | A full orchestrator turn: delegation and trace assembly |
| `test_orch_mini.py` | Smallest possible orchestrator round-trip |
| `test_interactions.py` | Many queries in sequence, logging issues per turn |
| `test_chained.py` | Multi-step queries needing several tools in one turn |
| `test_multiquery.py` | Parallel delegation (several specialists in one turn) |
| `test_smoke_queries.py` | Quick pass over the key query shapes |
| `test_regression_queries.py` | The two queries that were broken once — kept so they stay fixed |
| `test_final.py` | Broad end-to-end pass |
| `test_new_tools.py` | Newly added MCP tools answer at all |
| `test_strava_cache.py` | Strava file cache: hits, and eviction of dead activity ids |
| `test_streams_viz.py` | GPS stream fetch → route data actually present |
| `test_training_load.py` | ATL / CTL / TSB computation |
| `test_wellness_stress.py` | Garmin wellness + intraday stress |

### Grading codes

| Code | Meaning |
|---|---|
| `WRONG_TOOL` | None of the expected tools was called |
| `NO_MAP_DATA` | Query expects a GPS/route map but `route_data` is `None` |
| `WRONG_VIZ_METRIC` | Model emitted a different (or no) chart hint than expected |
| `EMPTY_CHART` | Every chart render returned `None` or under 1 KB |
| `TOOL_ERROR` | An MCP tool returned an `{"error": …}` payload |
| `HALLUCINATED_MAP` | Answer claims a map is shown, but no GPS data was fetched |
| `EMPTY_ANSWER` | Answer is blank or under 30 characters |
| `CRASH` | Orchestrator raised an unhandled exception |

**Known false positives.** Some Garmin/Strava fields are legitimately null — HRV and
VO2max when the device did not record them, `suffer_score` on most Strava activities.
The model then correctly answers "not available" and correctly omits the chart hint,
but the grader still flags `WRONG_VIZ_METRIC`. Read that code with this in mind.

---

## Debug tools (`tests/tools/`)

Not tests. They read state and print it; none of them assert anything.

| Script | What it shows |
|---|---|
| `read_log.py`, `read_recent_log.py` | Recent turns from `.logs/agent_interactions.jsonl` |
| `dump_log.py` | The same, written to a file (avoids console-encoding issues) |
| `dump_hints.py` | Chart hints vs. tools called, side by side |
| `dump_report.py` | Chart hints out of a saved test-report JSON |
| `check_servers.py` | Which MCP ports are answering right now |
| `check_cache.py` | Contents of the Strava activity file cache |
| `clear_dead_cache.py` | **Mutates state** — drops dead activity ids from that cache |
| `check_constants.py` | Constants as the orchestrator module sees them |
| `check_prompt.py` | ⚠️ **Broken.** Reads `core.orchestrator._SYSTEM`, which moved to `agents/prompts.py` when the A2A agent layer replaced the single-prompt loop. Kept as a marker — fix or delete |
| `run_remaining.py` | Re-runs only queries 20–25 of the viz-quality suite |

Output goes to `tests/logs/` (git-ignored, created on demand).

---

## Where the quality evaluation lives

These tests check that the machinery works. The **quality** evaluation — personas,
scorers, end-to-end runs and generated reports — is a separate harness in
[`evaluation/`](../evaluation/README.md). For assessing the project itself, read that one.
