# FitDash Test Suite

Integration tests that fire real queries at the orchestrator and verify:
- Correct tool selection (Garmin vs. Strava, GPS streams fetched before claiming a map, …)
- Visualization quality (VIZ metric hints, non-empty chart PNGs, route maps populated)
- Chart rendering in Telegram (`core/viz_telegram.py` — produces PNG bytes)

All tests require live MCP servers. Start them first:

```bash
python -m servers.strava_mcp   # :8103
python -m servers.garmin_mcp   # :8104
python -m servers.weather_mcp  # :8101  (for weather queries)
python -m servers.routes_mcp   # :8102  (for route/trail queries)
```

Run tests with the conda environment:

```bash
conda run -n aiss2026 python tests/<script>.py
```

---

## Main test scripts

### `test_viz_quality.py` — 25-query visualization quality test

The primary end-to-end test. Fires 25 representative chat queries and for each:

1. Calls `FitDashOrchestrator.run()`
2. Runs every tool result through `core/viz_telegram.py`'s `render_chart_png()` — records PNG byte count
3. Checks whether `route_data` was populated (for GPS/route queries)
4. Checks whether the model emitted the correct `<!--VIZ{...}-->` metric hint
5. Grades each result (WRONG_TOOL / NO_MAP_DATA / WRONG_VIZ_METRIC / EMPTY_CHART / TOOL_ERROR / HALLUCINATED_MAP / EMPTY_ANSWER)

Output:
- `tests/logs/viz_quality_<timestamp>.json` — per-query trace + grader results
- `tests/logs/viz_quality_<timestamp>.md` — human-readable summary

Query categories (one per section in the file):
1. HR zone distribution (Garmin-only fix)
2. GPS hike map (stream-fetch fix)
3. Lap splits / cadence (Garmin detail fix)
4. Weather forecast
5. Route planning (circular loop)
6. Training metrics (VO2max, marathon prediction)
7. Body composition
8. Gear mileage
9. All-time activity stats
10. Training load (ATL/CTL/TSB)
11. GPS run coloured by heart rate
12. Performance / pace trend
13. Year-over-year breakdown
14. Personal bests
15. 14-day wellness trends
16. HRV status
17. Step count intraday
18. Stress timeline (intraday)
19. Compare activity to baseline
20. Explore trails near home
21. Elevation gain metric hint
22. Pace metric hint
23. HR metric hint
24. Suffer score metric hint
25. Recovery multi-chart (HRV + body battery + sleep in parallel)

```bash
conda run -n aiss2026 python tests/test_viz_quality.py
```

**Known false positives in the grader:**
- `WRONG_VIZ_METRIC` for suffer_score: Strava `suffer_score` is null for most activities; the model correctly omits the VIZ tag when there is no data.
- `INCORRECT_NO_DATA` for HRV / VO2max / body composition: these fields are sometimes null in Garmin (device not recording, plan limitation). Model correctly says "not available" — tools did succeed.
- `NO_VIZ_HINT` for `analyze_performance_trends` / `get_personal_bests`: these tools have their own dedicated Streamlit renderers; the activity-list VIZ hint only applies to `get_activities`.

---

### `run_remaining.py` — focused 6-query test (queries 20–25)

Runs only the last 6 queries from `test_viz_quality.py` (the ones most likely to need re-running after fixes). Faster to iterate on VIZ metric hints and multi-tool recovery without waiting for the full 25-query suite.

Output: `tests/logs/remaining_<timestamp>.json`

```bash
conda run -n aiss2026 python tests/run_remaining.py
```

---

## Debug / inspection utilities

### `dump_log.py` — recent agent_interactions.jsonl entries

Reads the last N entries from `.logs/agent_interactions.jsonl` and writes them to
`tests/logs/tmp_log_dump.txt` (excluded from git). Useful to track live test progress.

```bash
conda run -n aiss2026 python tests/dump_log.py 10   # last 10 entries
```

Fields logged per run (as of the viz-quality session):
- `ts`, `user_input`, `n_tool_calls`, `tools` (list of namespaced tool names)
- `timing` (dict with `total_ms` key), `error`
- `viz_hints` (dict, e.g. `{"metric": "pace_min_per_km"}`)
- `has_route` (bool), `answer_preview` (first 300 chars)

### `dump_hints.py` — VIZ hints from recent log entries

Same as `dump_log.py` but focused on showing `viz_metric` and `tools` side-by-side.
Writes to `tests/logs/tmp_hints.txt`.

```bash
conda run -n aiss2026 python tests/dump_hints.py 15
```

### `dump_report.py` — VIZ hints from a saved report JSON

Reads `tests/logs/run_20260611_report.json` (the comprehensive 48-query run) and prints
per-query `metric`, `tools`, and any issues.

```bash
conda run -n aiss2026 python tests/dump_report.py
```

---

## Log files (committed)

| File | Description |
|------|-------------|
| `logs/run_20260611_report.json` | 48-query comprehensive test — full traces, VIZ hints, issues |
| `logs/run_20260611_issues.json` | Flat issue list from the 48-query run |
| `logs/run_20260611_analysis.md` | Human-readable analysis: confirmed real problems vs. false positives |
| `logs/remaining_20260611_2134.json` | 6-query viz-quality re-run (queries 20–25) with PNG byte counts |
| `logs/remaining_run_v2.txt` | Console output from the focused re-run |

`tmp_*.txt` files in `logs/` are excluded from git (they're debug scratch files).

---

## Fixes applied based on test findings (June 2026)

| Issue | Root cause | Fix |
|-------|-----------|-----|
| GPS hike map hallucinated ("map is shown" with zero GPS data) | Model never called `get_activity_streams` | `GPS MAPS` rule in `core/orchestrator.py` system prompt |
| HR zone distribution used Strava (which has none) | No routing rule | `HR ZONE DISTRIBUTION` rule: always use `garmin__get_garmin_activity_detail` |
| Cadence / lap splits from Strava detail (no cadence there) | No routing rule | `LAP SPLITS / CADENCE / POWER` rule in prompt |
| Strava 404 dead IDs retried 3× per turn (cache not invalidated) | File cache kept dead IDs for 24 h | `_evict_activity()` in `servers/strava_mcp.py` removes dead IDs immediately |
| `suffer_score` query showed distance chart (metric not in registry) | `_METRIC_CFG` in `ui/viz.py` missing `suffer_score` | Added to `_METRIC_CFG` and `VIZ TAGS` guide in system prompt |
| `ToolHost.call_tool()` could hang indefinitely on TCP stall | `self.timeout` attribute existed but was never passed to the MCP client | `asyncio.wait_for(_do(), timeout=60s)` in both `acall_tool` and `alist_tools` in `core/host.py` |
| Telegram sent two maps for GPS activities (staticmap + HR-colored folium) | `telegram_bridge.py` called `route_render.py` for all ROUTE_TOOLS | Skip `render_route_image` for `get_activity_streams` and `get_activity_gps_track` |
| LLM call timeout too aggressive (KIT gateway sometimes >60 s) | `timeout=60` in `client.chat.completions.create()` | Raised to `timeout=90` in `core/orchestrator.py` |

---

## Graded issue codes

| Code | Meaning |
|------|---------|
| `WRONG_TOOL` | None of the expected tools was called |
| `NO_MAP_DATA` | Query expects a GPS/route map but `route_data` is `None` |
| `WRONG_VIZ_METRIC` | Model emitted a different (or no) `<!--VIZ{...}-->` metric hint than expected |
| `EMPTY_CHART` | All `render_chart_png()` calls returned `None` or <1 KB |
| `TOOL_ERROR` | An MCP tool returned an `{"error": ...}` payload |
| `HALLUCINATED_MAP` | Model says "map is shown" but no GPS data was fetched |
| `EMPTY_ANSWER` | Answer is blank or <30 characters |
| `CRASH` | Orchestrator raised an unhandled exception (network timeout, etc.) |
