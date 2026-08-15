# Technical robustness suite

The technical counterpart to `evaluation/run_e2e.py`. That harness asks whether the
Copilot's **answers** are good; this one asks whether the **plumbing** holds. It drives the
stack through `core.host.ToolHost` — the same client the agents use — so it measures what
the agents experience. No MLflow, no new dependencies; only `--with-orchestrator` spends
LLM tokens.

## What each probe measures

| Probe | Measures |
|-------|----------|
| **sweep** | `--repeat N` sequential calls per fixtured tool with valid args. Per call: latency + ok/err (err = the result is a JSON object with an `error` key, or is empty). Per tool: success count, p50/p95/max — rolled up per server. |
| **malformed** | Hostile variants per tool: missing required arg, wrong type, out-of-domain value, 10k-char string, unknown extra arg. PASS = structured error **or** tolerated normal reply; FAIL = an exception escaped `ToolHost`, which its contract forbids. Each tool then gets one valid call — the *server-survives* check. |
| **unreachable** | A `ToolHost` on `http://127.0.0.1:9/mcp`: `alist_tools()` must return `[]`, `acall_tool()` an error string, both within `timeout + 3 s`. A mixed registry (real weather + ghost) must still list weather's tools — the skip-not-fail contract. |
| **concurrency** | `--conc-calls` calls alternating `weather__get_weather_forecast` / `routes__geocode`, bounded by an `asyncio.Semaphore` per `--concurrency` level. Success rate, p50/p95, wall time, throughput. |
| **selection** *(opt-in)* | 12 read-only prompts through the real `FitDashOrchestrator`, serialized. Each carries an expected evidence set (sleep → `garmin__`, load → `strava__`, weather → `weather__`, free slot → `calendar__`, loop → `routes__`, exercise science → `search_fitness_literature`, season → `athlete__`/`strava__`). PASS = a tool with an expected prefix appears in `trace["tool_calls"]`. |

## Running

From the repo root, with the stack up (MCP `:8101–:8109`, agents `:9000–:9006`):

```bash
python -m evaluation.robustness.run_robustness                       # full run, 5 calls/tool
python -m evaluation.robustness.run_robustness --with-orchestrator   # + selection (LLM tokens)
python -m evaluation.robustness.run_robustness --repeat 1 --only weather__,routes__geocode
python -m evaluation.robustness.run_robustness --skip-sweep --concurrency 1,4,8 --conc-calls 16
```

`--only` matches comma-separated substrings against the full `server__tool` name; `--out`
overrides the report path (default `evaluation/reports/robustness-<UTC ts>.json`). A compact
table goes to stdout; the JSON is the source of truth.

## Safety model

Read-only by construction, in three layers:

1. **Default-deny** — a tool is called only if it has an explicit entry in `fixtures.py`.
   Everything else is skipped with a reason (`SKIP[name]`, else auto-reason `"no fixture"`).
2. **Write-path veto** — `fixtures.write_path_reason()` independently rejects any tool whose
   name contains a mutating verb segment (`create add set update delete record send launch
   schedule book export write post mark complete rescaffold import`), the whole `telegram`
   server, and the whole `flythrough` server (its one tool starts a headless render). It runs
   for every candidate, so a fixture alone is never sufficient. Calendar is therefore limited
   to `list_calendars`/`list_events`, athlete to `get_athlete_overview`/`get_plan`.
3. **Import-time self-check** — `fixtures.py` refuses to load if layers 1 and 2 disagree.

Selection prompts are phrased read-only ("show", "check", "how was", "find") — none asks the
coach to create, schedule, message, or modify anything.

## Output schema (sketch)

```jsonc
{ "meta":        { "generated_at_utc", "duration_s", "args", "servers", "inventory",
                   "fixtured", "selected", "skipped", "skip_list": [{"tool", "reason"}] },
  "sweep":       { "repeat", "tools": [{"tool","server","calls","ok","err","success_rate",
                   "p50_ms","p95_ms","max_ms","latencies_ms","errors"}], "by_server": {…} },
  "malformed":   { "tools": [{"tool", "variants": [{"label","args","outcome","verdict","ms",
                   "error"}], "survives": {"ok","ms","error"}}], "totals": {…} },
  "unreachable": { "endpoint", "bound_ms", "checks": [{"name","passed","duration_ms"}] },
  "concurrency": { "pool", "levels": [{"level","success_rate","p50_ms","p95_ms","wall_s"}] },
  "selection":   { "enabled", "passed", "total", "prompts": [{"prompt","expect","passed",
                   "matched_tools","tools","agents","answer_chars","ms"}] },
  "summary":     { "sweep", "per_server_success_rate", "malformed", "unreachable",
                   "concurrency", "selection", "error_taxonomy": [{"pattern","count",
                   "example","tools","sources"}] } }
```

`error_taxonomy` normalizes each message (lowercased, whitespace collapsed, digits → `<n>`,
quoted spans masked), counts the top 10, and keeps one raw example truncated to 120 chars.
`sources` distinguishes sweep errors from the deliberately malformed calls (expected) and
the concurrency probe.
