# FitDash

FitDash is a Streamlit sports analytics dashboard that unifies Strava activities and Garmin health data behind an agentic chat interface. Every answer comes from live API data — the assistant plans tool calls, executes them in parallel, then synthesises the result. No cached summaries, no hallucinated numbers.

## Highlights

- **Dashboard** — activity map, summary metrics, training charts, Strava athlete stats.
- **Activity Analysis** — stream-based charts (HR, pace, elevation, cadence, power) with colourised route overlays selectable by metric.
- **3D Flythrough** — cinematic GPS route replay using MapLibre.
- **Health** — Garmin wellness trends: Body Battery, sleep stages, stress, HR, steps, training metrics, HRV.
- **Chat** — agentic Q&A: planner → parallel executor → synthesiser, with a live trace panel in the UI.
- **Sync** — export Garmin activities to Strava as FIT files with preview and selection controls.

## Project Layout

```
fitdash/
├── app.py                   # Streamlit entry point
├── requirements.txt
├── .env                     # credentials (never committed)
├── .env.example             # template — copy to .env and fill in
├── .streamlit/config.toml   # Streamlit theme + server config
│
├── auth/
│   ├── strava_oauth.py      # OAuth2 manager: token cache and auto-refresh
│   └── garmin_setup.py      # One-time Garmin MFA login
│
├── mcp/
│   ├── strava.py            # Strava MCP server (9 tools)
│   └── garmin.py            # Garmin MCP server (10 tools)
│
└── ui/
    ├── orchestrator.py      # Planner → executor → synthesiser loop
    ├── shared.py            # MCP singletons, OpenAI client, tool router
    ├── styles.py            # CSS variables, chart theme, colour constants
    ├── dashboard.py         # Dashboard tab
    ├── activity_analysis.py # Stream charts + coloured route overlay
    ├── flythrough_3d.py     # 3D MapLibre flythrough
    ├── health.py            # Health tab
    ├── chat.py              # Chat tab
    └── sync.py              # Garmin → Strava export tab
```

## Orchestrator: How the Chat Works

Every user message is processed in three deterministic phases before any text is returned.

```
User question
      │
      ▼
 ┌─────────────────────────────────────────────┐
 │ 1. Planner                                  │
 │  LLM → JSON plan: [{tool, args, label}, …]  │
 │  Rules: explicit dates, one call/day for     │
 │  intraday data, aggregate tools for ranges,  │
 │  full-history search for superlatives        │
 └─────────────────┬───────────────────────────┘
                   │ plan (list of steps)
                   ▼
 ┌─────────────────────────────────────────────┐
 │ 2. Executor (parallel ThreadPoolExecutor)   │
 │  Runs all tool calls concurrently           │
 │  Per-call timeout · collects errors too     │
 └─────────────────┬───────────────────────────┘
                   │ tool results
                   ▼
 ┌─────────────────────────────────────────────┐
 │ 3. Synthesiser                              │
 │  LLM writes answer from results only        │
 │  No fabrication · handles missing data      │
 │  Responds in the user's language            │
 └─────────────────────────────────────────────┘
```

The UI shows a collapsible **Agent trace** after each answer: planned calls, execution status per tool, and phase timings. Every run is also appended to `.logs/agent_interactions.jsonl`.

### Orchestrator Internals

The logic lives in [ui/orchestrator.py](ui/orchestrator.py), wired into the chat via [ui/chat.py](ui/chat.py).

| Constant | Default | Purpose |
|---|---|---|
| `MAX_PLAN_STEPS` | 60 | Hard cap on planned tool calls per turn |
| `MAX_WORKERS` | 5 | Parallel threads (kept low for Garmin rate limits) |
| `TOOL_TIMEOUT` | 45 s | Per-tool call timeout |

Extending the orchestrator typically means adjusting the planner prompt rules in [ui/orchestrator.py](ui/orchestrator.py) or registering new tools in [ui/shared.py](ui/shared.py).

## Dashboard: What It Shows

- **Activity Map** with route overlays and selectable focus activity.
- **Key Metrics**: total distance, time, elevation, average HR.
- **Training Overview** with adaptive aggregation (day / week / month).
- **Recent Activities** cards with pace and elevation.
- **Activity Analysis** (on selection): coloured route overlays and per-km charts from raw GPS streams.
- **3D Flythrough**: cinematic GPS route replay at selectable speed.

## Health: What It Tracks

- Sleep stages (deep / REM / light / awake) with score and contextual quality hover tooltip.
- Body Battery daily highs and lows.
- Resting HR and daily max HR trends.
- Steps, stress, and intensity minutes with WHO goal reference lines.
- Training metrics: VO₂max, readiness score, training load, race predictions.
- HRV last-night value and personal baseline range.

## Chat: How To Use It

Type any question about your fitness data. The assistant figures out which tools to call, fetches the data, and answers from real numbers only.

### What to ask

**Performance and personal bests**

- *"What is my fastest 5 km pace ever?"*
- *"Show my top 5 longest rides sorted by elevation."*
- *"Which run had the highest average heart rate this year?"*
- *"What gear have I used most this season?"*

**Sleep and recovery**

- *"Compare my average deep sleep and REM sleep last week vs the week before."*
- *"How many nights did I sleep less than 6 hours in the last 30 days?"*
- *"What was my sleep score on my hardest training days last month?"*
- *"Show my Body Battery trend over the last 3 weeks."*

**Training trends and load**

- *"How has my weekly running distance changed over the last 12 weeks?"*
- *"What is my current VO₂max and training readiness score?"*
- *"Show my race time predictions for a half marathon."*
- *"Which week had the highest training load in the last 6 months?"*

**Cross-source correlations**

- *"On days my resting HR was above 60, how did my sleep look?"*
- *"Show my heart rate timeline yesterday and mark when I was active."*
- *"Was my stress elevated on days I didn't train last week?"*
- *"How does my Body Battery at the start of runs correlate with the distance I covered?"*

**Intraday detail**

- *"Show HR peaks before sleep in the last 14 days."*
- *"What time did my step count peak yesterday?"*
- *"Show the full heart rate and step timeline for last Tuesday."*

### What the UI shows

After each answer the **Agent trace** expander reveals:

- the planner's reasoning and the exact tool calls it generated,
- execution status (✅ / ❌) and duration per call,
- phase timings: plan · exec · synth.

## Sync: Garmin → Strava Export

The Sync tab implements a three-stage workflow:

1. **Fetch** Garmin activities for a selected date range.
2. **Preview and select** which activities to export.
3. **Upload** as FIT files to Strava with per-activity progress feedback.

## MCP Servers and Tools

FitDash uses lightweight JSON-RPC MCP servers (in-process by default, stdio-compatible for subprocess transport). Each server exposes a fixed tool set that the planner and the UI both consume.

### MCP Server Design

Both servers in [mcp/strava.py](mcp/strava.py) and [mcp/garmin.py](mcp/garmin.py) implement the same minimal interface:

- `tools/list` — returns tool metadata: name, description, input schema.
- `tools/call` — invokes a tool by name, returns a JSON string.

Each server defines a `tools` list (schema for the LLM), routes requests in `_dispatch`, and always returns JSON text so results are transport-agnostic.

### Adding a New Tool

Four steps:

1. **Add the schema** to the server's `tools` list.
2. **Implement the handler** (async method returning JSON text).
3. **Register it** in `_dispatch`.
4. **Done** — the planner picks it up automatically via the tool list in [ui/shared.py](ui/shared.py).

```python
# 1. Schema — in mcp/strava.py SimpleMCPServer.__init__
self.tools.append({
    "name": "get_example_metric",
    "description": "Concise, specific description the LLM can plan with.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"},
        },
        "required": ["date"],
    },
})

# 2. Handler
async def _get_example_metric(self, args: Dict) -> str:
    data = {"date": args["date"], "value": 42}
    return json.dumps(data, indent=2)

# 3. Registration — in _dispatch
"get_example_metric": self._get_example_metric,
```

### Strava Tools (9)

| Tool | What it returns |
|---|---|
| `get_activities` | Recent activities (distance, pace, HR, kudos) with optional sport and limit filters |
| `get_activity_stats` | Aggregate totals and per-sport breakdown across all synced activities |
| `get_athlete_profile` | Athlete profile, gear, and Strava's official all-time / YTD / last-4-weeks stats |
| `get_training_trends` | Per-week training load (distance, time, elevation, sport mix) for the last N weeks |
| `get_personal_bests` | Top 5 by distance, duration, elevation, and speed; biggest week; longest streak |
| `get_yearly_breakdown` | Year-over-year totals since 2022 with per-sport breakdown |
| `get_gear_info` | Registered bikes and shoes with brand, model, and accumulated mileage |
| `get_activity_detail` | Deep single-activity detail: laps, splits, HR, power, cadence, PRs, gear |
| `get_activity_streams` | Raw GPS streams (lat/lon, altitude, HR, cadence, velocity, power) for route visualisation |

### Garmin Tools (10)

| Tool | What it returns |
|---|---|
| `get_garmin_activities` | Garmin activity list with distance, pace, HR, calories, and training effect |
| `get_garmin_activity_detail` | Per-lap splits, HR zone breakdown, power zones for one activity |
| `get_garmin_daily_health` | Steps, calories, resting HR, stress, intensity minutes, Body Battery for one day |
| `get_garmin_heart_rate_timeline` | Full-day HR in ~15-minute intervals; useful for spotting stress or illness spikes |
| `get_garmin_sleep` | Sleep stages (deep/REM/light/awake), sleep score, SpO₂, respiration, HRV for one night |
| `get_garmin_body_battery` | Daily Body Battery highs, lows, and intraday timeline over a date range |
| `get_garmin_hrv_status` | Last-night HRV, personal baseline range, and readiness status |
| `get_garmin_training_metrics` | VO₂max, 7- and 28-day training load, training status, readiness score, race predictions |
| `get_garmin_wellness_trends` | Multi-day rollup of HR, steps, stress, sleep score, and Body Battery — preferred for date-range comparisons |
| `get_garmin_steps_timeline` | 15-minute step buckets with activity level (sedentary / active / sleeping) for one day |

## Setup

### Prerequisites

- Python 3.10 or later
- A [Strava API application](https://www.strava.com/settings/api) — set the callback domain to `localhost`
- An OpenAI-compatible API key (OpenAI, Azure OpenAI, or a local proxy)
- *(Optional)* A Garmin Connect account for the Health and Sync tabs

### Installation

```bash
# Clone and enter the project
git clone <repo-url>
cd fitdash

# Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up credentials
cp .env.example .env
# Then edit .env — see Environment Variables below
```

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `CLIENT_ID` | Yes | Strava application client ID |
| `CLIENT_SECRET` | Yes | Strava application client secret |
| `OPENAI_API_KEY` | Yes | OpenAI (or compatible) API key |
| `AGENT_MODEL` | Yes | Model name, e.g. `gpt-4o` or `claude-opus-4-7` |
| `OPENAI_BASE_URL` | No | Custom API base URL — omit for openai.com |
| `GARMIN_EMAIL` | No | Garmin Connect email (Health and Sync tabs) |
| `GARMIN_PASSWORD` | No | Garmin Connect password |

## Authentication

### Strava OAuth

Strava OAuth runs automatically on first use. The app opens a browser window to authorise access; tokens are saved to `.tokens/strava.json` and refreshed automatically on subsequent runs.

### Garmin Setup

```bash
python auth/garmin_setup.py
```

Run once after filling in `GARMIN_EMAIL` and `GARMIN_PASSWORD`. Tokens are stored in `.tokens/` and persist until they expire. Re-run the script if Garmin login fails.

## Running

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501).

## Caching and Rate Limits

- Most data loaders use `@st.cache_data` with TTLs ranging from 5 minutes (today's data) to 30 minutes (multi-day trends).
- Garmin parallel calls are capped at 5 workers to stay within Connect's rate limits.
- The orchestrator caps plan steps at 60 to prevent runaway tool calls on broad questions.
- Sleep fields that are `null` in Garmin data mean the device recorded no data for that night; they are excluded from averages and reported explicitly.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Strava auth fails | Delete `.tokens/strava.json` and reload the app to re-authorise |
| Garmin tokens expired | Re-run `python auth/garmin_setup.py` |
| Port 8080 already in use | Kill the process on that port, or change `REDIRECT_URI` in `auth/strava_oauth.py` |
| No route visible on map | The activity has no GPS stream (indoor workout or Strava privacy zone) |
| Chat returns "Garmin not connected" | Run `auth/garmin_setup.py` and confirm `.tokens/garmin_tokens.json` exists |
