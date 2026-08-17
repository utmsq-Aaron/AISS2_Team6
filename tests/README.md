# Tests

Three different things live here, and the split matters:

| | `tests/unit/` | `tests/integration/` | `tests/tools/` |
|---|---|---|---|
| **What** | Real tests — assert, pass or fail | End-to-end scripts against the live system | Debug/inspection utilities |
| **Run with** | `pytest` | `python tests/integration/<script>.py` | `python tests/tools/<script>.py` |
| **Needs** | nothing | the full stack, an LLM gateway, real Strava/Garmin accounts | the stack, to have anything to look at |
| **Exit code** | meaningful | meaningful | always 0 — there is nothing to fail |
| **Costs** | nothing | LLM tokens | nothing (except `run_remaining.py`) |

If you are evaluating this project, run `pytest` — it works on a fresh checkout
with no accounts and no keys. `tests/integration/` is what we used while
building against real data; `tests/tools/` is scaffolding, kept because it is
still handy.

---

## The offline suite

```bash
.venv/bin/python -m pytest        # or just: pytest
```

Collects `tests/unit/` only. No MCP servers, no LLM gateway, no network, no user
data — everything is either pure arithmetic or runs against fakes. Under four
seconds.

| File | What it proves |
|---|---|
| `test_training_math.py` | The training plan is **computed, not generated**: HR/pace zones, the base→build→peak→taper split, the long-run line, cutback and taper weeks, and that weekly volume derives from the runs. These are the rules of [`docs/trainingsregeln.md`](../docs/trainingsregeln.md) asserted against the code that implements them. |
| `test_agent_layer.py` | The LangGraph + A2A layer: `build_trace()` emits the exact contract the frontend, chart service and route map read; peer `sub_artifacts` are flattened rather than lost; `_peers_for()` honours the depth limit; a full in-process A2A two-hop and a peer-to-peer mesh round-trip (fake chat model, fake MCP host, uvicorn on test ports 9100/9101/9103). |
| `test_route_export.py` | A planned route survives export: the GPX is valid XML carrying every point (lat/lon the right way round, elevation kept), and the Google Maps link keeps start and finish while thinning only the middle. |

Run `pytest` after touching `core/agent_trace.py`, `core/orchestrator*.py`,
`agents/`, `servers/athlete_mcp.py` or `core/route_export.py`.

`tests/integration/` is **not** collected, deliberately: most of those scripts
build a live orchestrator at import time, so merely collecting them would call
Strava and spend tokens. `tests/integration/conftest.py` enforces that.

---

## Integration scripts (`tests/integration/`)

Fire real chat queries at the orchestrator and grade the answers. Start the
stack with `./run.sh` first, then run one directly:

```bash
.venv/bin/python tests/integration/test_orchestrator.py
```

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
| `test_regression_queries.py` | Two queries pinned to fixed expectations, so their behaviour stays put |
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
| `dump_report.py` | Chart hints out of a saved test-report JSON (newest in `tests/logs/`, or pass a path) |
| `check_servers.py` | Which MCP servers are answering right now (ports from `core/config.py`) |
| `check_cache.py` | Contents of the Strava activity file cache |
| `clear_dead_cache.py` | **Mutates state** — drops the activity ids you pass from that cache |
| `check_constants.py` | Constants and public surface as the orchestrator module sees them |
| `run_remaining.py` | Re-runs only queries 20–25 of the viz-quality suite — **needs the live stack and spends LLM tokens**, unlike the rest of this table |

Output goes to `tests/logs/` (git-ignored, created on demand).

---

## Where the quality evaluation lives

These tests check that the machinery works. The **quality** evaluation — personas,
scorers, end-to-end runs and generated reports — is a separate harness in
[`evaluation/`](../evaluation/README.md). For assessing the project itself, read that one.
