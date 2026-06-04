# HealthBot

HealthBot is a Streamlit sports analytics dashboard that unifies Strava activities and Garmin health data behind an agentic chat interface. Every answer comes from live API data — a four-agent pipeline plans tool calls, executes them in parallel, selects visualisations, and synthesises the result. No cached summaries, no hallucinated numbers.

## Highlights

- **Dashboard** — activity map, summary metrics, training charts, Strava athlete stats.
- **Activity Analysis** — stream-based charts (HR, pace, elevation, cadence, power) with colourised route overlays selectable by metric.
- **3D Flythrough** — cinematic GPS route replay with server-side MP4 export (Playwright + WebCodecs, frame-by-frame deterministic encoding).
- **Health** — Garmin wellness trends: Body Battery, sleep stages, stress, HR, steps, training metrics, HRV.
- **Chat** — four-agent Q&A: FetchingAgent → (VisualizationAgent ∥ FlyoverAgent) → ChatAgent, with inline charts, 3D flythrough video pinned to the message, and a live trace panel.
- **Sync** — export Garmin activities to Strava as FIT files with preview and selection controls.
- **PIN barrier** — optional access PIN stored in `.streamlit/secrets.toml` blocks the entire app until authenticated.

## Project Layout

```
fitdash/
├── app.py                       # Streamlit entry point + PIN gate
├── requirements.txt
├── .env                         # API credentials (never committed)
├── .env.example                 # template — copy to .env and fill in
├── .streamlit/
│   ├── config.toml              # Streamlit theme + server config
│   └── secrets.toml             # APP_PIN (never committed)
│
├── auth/
│   ├── strava_oauth.py          # OAuth2 manager: token cache and auto-refresh
│   └── garmin_setup.py          # One-time Garmin MFA login
│
├── servers/
│   ├── strava.py                # Strava MCP server (SimpleMCPServer, 10 tools)
│   ├── garmin.py                # Garmin MCP server (GarminMCPServer, 10 tools)
│   └── agents/
│       ├── _base.py             # Shared LLM utils (get_llm_client, llm_call, truncate, extract_json)
│       ├── fetching.py          # FetchingAgent — plans + executes all data fetches
│       ├── visualization.py     # VisualizationAgent — selects which charts to render
│       ├── flyover.py           # FlyoverAgent — detects and resolves flythrough requests
│       └── chat.py              # ChatAgent — synthesises the final answer
│
└── ui/
    ├── orchestrator.py          # 3-phase coordinator: FetchingAgent → (Viz ∥ Flyover) → Chat
    ├── viz.py                   # Visualization registry (@register, can_render, render)
    ├── shared.py                # MCP singletons, OpenAI client, tool router (call_tool)
    ├── styles.py                # CSS variables, chart theme, colour constants
    ├── dashboard.py             # Dashboard tab
    ├── activity_analysis.py     # Stream charts + coloured route overlay
    ├── flythrough_3d.py         # 3D MapLibre flythrough — interactive preview + export
    ├── video_renderer.py        # Server-side MP4 renderer (Playwright + headless Chromium)
    ├── health.py                # Health tab
    ├── chat.py                  # Chat tab
    └── sync.py                  # Garmin → Strava export tab
```

## Multi-Agent Architecture

Every chat message is processed by four specialised LLM agents coordinated in three phases. Each agent is both a **FastMCP server** (runnable standalone via stdio) and callable in-process with zero transport overhead.

```
User question
      │
      ▼  Phase 1 (sequential)
 ┌─────────────────────────────────────────────────────────────────┐
 │  FetchingAgent  (servers/agents/fetching.py)                   │
 │                                                                 │
 │  Planning:                                                      │
 │  · LLM planner → minimal JSON list of MCP tool calls           │
 │  · Explicit YYYY-MM-DD dates; one call/day for intraday data;   │
 │    aggregate tools for ranges; start_date=2010-01-01 for bests  │
 │  · Clarification path: if the query is genuinely ambiguous AND  │
 │    history doesn't resolve it → sets clarification_needed=true  │
 │    + writes a short clarification_question; skips fetch entirely│
 │  · Loop guard: if history already contains a clarification on   │
 │    this topic → proceeds with best-effort plan instead          │
 │  · Parse guard: if planner output is unparseable → clarification│
 │                                                                 │
 │  Execution (2-pass):                                            │
 │  · All planned steps run in parallel (ThreadPoolExecutor ×5)   │
 │  · Refinement pass: if initial results include a list tool      │
 │    (get_activities, get_personal_bests, …) the LLM checks      │
 │    whether a follow-up call (e.g. get_activity_streams for a    │
 │    specific activity_id) is now possible; max 5 extra steps     │
 │  · Dedup: by-name results superseded by by-id results are       │
 │    dropped so downstream agents see only the correct data       │
 │  · Progress fires per-tool: "Retrieved: {label} (N/M)"         │
 │  · Reasoning surfaced immediately after planning                │
 └────────────────────────────┬────────────────────────────────────┘
                              │ structured JSON  {results, reasoning,
                              │  clarification_needed, clarification_question}
                   ┌──────────┴──────────┐
                   │  Phase 2 (parallel) │  ← skipped when clarification_needed
                   ▼                     ▼
 ┌─────────────────────┐   ┌───────────────────────────┐
 │  VisualizationAgent │   │  FlyoverAgent              │
 │  (visualization.py) │   │  (flyover.py)              │
 │  · Selects ≤4 charts│   │  · Fast path: if           │
 │    from fetched data│   │    launch_flythrough ran,   │
 │  · Fast path: render│   │    extract action directly  │
 │    all if ≤2 charts │   │  · LLM path: extract all 4 │
 │  · LLM path: rank by│   │    params from fetched data │
 │    relevance if 3+  │   │  · Returns null if any      │
 │                     │   │    param is still missing   │
 └──────────┬──────────┘   └──────────────┬─────────────┘
            │ viz_actions                  │ flyover_action
            └──────────────┬──────────────┘
                           ▼  Phase 3 (sequential)
 ┌─────────────────────────────────────────────────────────────────┐
 │  ChatAgent  (servers/agents/chat.py)                            │
 │                                                                 │
 │  Input — structured data block:                                 │
 │    FETCH PLAN: <reasoning>                                      │
 │    RETRIEVED (N source(s)): <labels>                            │
 │    ERRORS — these sources failed: <label: error> ...           │
 │    CLARIFICATION HINT: <question>   ← if set by FetchingAgent  │
 │    [N chart(s) will render automatically below this answer.]    │
 │    [Flythrough rendering for '…' — … video appears below.]     │
 │                                                                 │
 │  Behaviour:                                                     │
 │  · Conversational sports-analyst tone; leads with the insight   │
 │  · Opens with "Looking at your last N days…" style framing      │
 │  · Clarification hint → asks exactly that one question          │
 │  · Never mentions numeric activity IDs                          │
 │  · All sources failed → one sentence + suggest connection check │
 │  · Answers in the user's language; last 10 turns of history     │
 └─────────────────────────────────────────────────────────────────┘
```

The **Orchestrator** (`ui/orchestrator.py`) coordinates all phases, passes `clarification_needed` and `clarification_question` between agents, maintains the execution trace, logs every run to `.logs/agent_interactions.jsonl`, and surfaces a collapsible debug panel in the UI.

### Clarification flow

When FetchingAgent determines the query is too ambiguous to fetch safely (e.g. *"show me my data"*), it sets `clarification_needed=true` and writes a short question. The Orchestrator:
1. Skips Phase 2 entirely (no useless viz or flyover analysis on empty data).
2. Passes the `clarification_question` hint to ChatAgent as a `CLARIFICATION HINT:` header.
3. ChatAgent rephrases the hint naturally and asks exactly that one question.

On the user's follow-up, the FetchingAgent sees the prior clarification in conversation history and proceeds with a best-effort plan instead of asking again.

### Flythrough parameter collection

The agent collects flythrough parameters via natural conversation — no separate form is shown. When the FlyoverAgent has all four parameters (activity, orientation, map style, duration) it fires `launch_flythrough` directly. The resulting video is rendered and pinned inside a collapsed expander directly below the message where it was requested.

### Running agents standalone

Each agent is a valid FastMCP server. Run without Streamlit:

```bash
python servers/agents/fetching.py       # stdio MCP server
python servers/agents/visualization.py
python servers/agents/flyover.py
python servers/agents/chat.py
```

### Agent constants

| Agent | Constant | Default | Purpose |
|---|---|---|---|
| FetchingAgent | `MAX_STEPS` | 60 | Hard cap on planned tool calls per turn |
| FetchingAgent | `MAX_REFINE_STEPS` | 5 | Max follow-up steps in the refinement pass |
| FetchingAgent | `MAX_WORKERS` | 5 | Parallel fetch threads |
| FetchingAgent | `TIMEOUT_S` | 120 s | Per-tool call timeout |
| VisualizationAgent | `MAX_CHARTS` | 4 | Maximum inline charts per answer |
| Orchestrator | history window | 10 turns | Conversation turns passed to each agent |
| Orchestrator | Phase 2 timeout | 45 s | Per-agent timeout (viz + flyover) |

## Dashboard

- **Activity Map** with route overlays and selectable focus activity (newest activities listed first).
- **Key Metrics**: total distance, time, elevation, average HR.
- **Training Overview** with adaptive aggregation (day / week / month depending on period).
- **Recent Activities** cards with pace and elevation.
- **Activity Analysis** (on selection): coloured route overlays (green = fast/low, red = slow/high) and per-km charts from raw GPS streams (HR, pace, elevation, cadence, power).
- **3D Flythrough**: cinematic GPS route replay — interactive preview in-browser, server-side MP4 export.

## Health

- Sleep stages (deep / REM / light / awake) with score and contextual quality hover tooltip.
- Body Battery daily highs and lows.
- Resting HR and daily max HR trends.
- Steps, stress, and intensity minutes with WHO goal reference lines.
- Training metrics: VO₂max, readiness score, training load, race predictions.
- HRV last-night value and personal baseline range.

## Chat

Type any question about your fitness data. The FetchingAgent plans and executes data fetches in parallel, the VisualizationAgent selects relevant charts (rendered inline), and the ChatAgent answers from real numbers only — in a conversational, training-partner tone.

If a question is genuinely ambiguous, the agent asks one short clarifying question before fetching anything. On the follow-up it proceeds immediately without asking again.

### What to ask

**Performance and personal bests**
- *"What is my fastest 5 km pace ever?"*
- *"Show my top 5 longest rides sorted by elevation."*
- *"Which run had the highest average heart rate this year?"*

**Sleep and recovery**
- *"Compare my average deep sleep and REM sleep last week vs the week before."*
- *"How many nights did I sleep less than 6 hours in the last 30 days?"*
- *"Show my Body Battery trend over the last 3 weeks."*

**Training trends and load**
- *"How has my weekly running distance changed over the last 12 weeks?"*
- *"What is my current VO₂max and training readiness score?"*
- *"Which week had the highest training load in the last 6 months?"*

**Cross-source correlations**
- *"On days my resting HR was above 60, how did my sleep look?"*
- *"Was my stress elevated on days I didn't train last week?"*

**Intraday stress and effort**
- *"When was I most stressed yesterday? Show me the stress timeline."*
- *"Was my stress high during the afternoon on Monday?"*
- *"Which of my runs last month had the highest max heart rate?"*
- *"How many Strava PRs did I set this week?"*

**Weight and body composition**
- *"What is my weight trend over the last month?"*
- *"Has my body fat percentage changed over the last 3 months?"*

**3D Flythrough (via Chat)**
- *"Make a 3D flythrough of my Bergen hike, landscape, satellite, 60 seconds."*
  The agent collects any missing parameters through natural conversation (orientation, map style, duration), then triggers the render automatically. The video appears collapsed in an expander directly below the message where it was requested — it stays pinned there and does not float to the bottom as the conversation continues.

### What the UI shows

After each answer the **Agent trace** expander reveals:
- FetchingAgent reasoning and exact tool calls with per-call duration and status (✅ / ❌).
- Whether Phase 2 ran or was skipped (clarification path).
- Phase timings: FetchingAgent · Viz+Flyover (parallel) · ChatAgent.
- Total wall-clock time.

## 3D Flythrough

Renders a cinematic GPS camera animation over real satellite terrain using MapLibre GL JS, then encodes an MP4 via WebCodecs (H.264, hardware-accelerated). Encoding is deterministic and frame-by-frame — not a screen recording — so quality is independent of machine load or tab focus.

### How it works

```
GPS stream (Strava)
      │  _prepare_track(): downsample → smooth
      ▼
MapLibre GL JS page (flythrough_3d.py → _build_html)
  · Satellite 3D (ESRI imagery + terrain DEM) or Dark Flat
  · Speed-adaptive bearing EMA — no jerky turns
  · Dynamic pitch (tilts on climbs) + zoom (pulls back at speed)
  · Tile pre-warm: visits 90 positions before recording starts
  · waitForTiles() per frame: areTilesLoaded() poll, not full idle wait
  · Elevation widget, info card, progress bar composited onto each frame
      │
      ▼  AUTO_EXPORT=true
WebCodecs VideoEncoder → mp4-muxer → .mp4 download
```

The page runs inside a **headless Chromium** instance via Playwright (`ui/video_renderer.py`). Python launches the browser, waits for the browser's download event, and returns raw MP4 bytes — served via `st.download_button` in the UI.

### Rendering pipeline

| Stage | Detail |
|---|---|
| Resolution | HD (1920×1080), 2K (2560×1440), 4K (3840×2160) — landscape or portrait (9:16) |
| Frame rate | 60 fps (hardware H.264) · 15 fps (software x264 fallback) |
| Codec | H.264 (`avc1.640033`), hardware-accelerated where available (NVENC/AMF/QuickSync) |
| Container | MP4 via `mp4-muxer` — precise timestamps, no duration-fix needed |
| Tile quality | `waitForTiles()` polls `map.areTilesLoaded()` before each frame; 90-position pre-warm loads all route tiles before encoding begins |
| Bitrates | HD 8 Mbps · 2K 16 Mbps · 4K 40 Mbps |

### Usage

**From the Dashboard:** click **🎥 3D Flythrough** on any activity with a GPS route. The interactive preview appears. Set export parameters (orientation, resolution, duration) below the preview and click **Render & Export**. The download button appears when encoding completes.

**From Chat:** ask for a flythrough in natural language. The agent collects missing parameters (orientation: landscape/portrait; map style: Satellite 3D / Dark Flat; duration: 30–120 s) through conversation, then triggers the render automatically. The video is pinned to the message where it was requested, collapsed by default so it does not interrupt reading.

## Sync: Garmin → Strava Export

Two-stage workflow:

1. **Fetch** Garmin activities for a selected date range.
2. **Preview and select** which activities to export (FIT download → Strava upload with per-activity progress).

Strava deduplicates by file hash — re-uploading an existing activity is safe.

## MCP Servers and Tools

Both servers expose the same minimal interface:
- `tools` list — tool metadata (name, description, input schema) for the LLM planner.
- `_dispatch(name, args)` — routes a tool call by name, returns a JSON string.

### Strava Tools (10)

| Tool | What it returns |
|---|---|
| `get_activities` | Recent activities (distance, pace, avg/max HR, suffer_score, kilojoules, pr_count, kudos) with optional sport and limit filters |
| `get_activity_stats` | Aggregate totals (distance, time, elevation, kilojoules) and per-sport breakdown across all synced activities |
| `get_athlete_profile` | Athlete profile and Strava's official all-time / YTD / last-4-weeks stats |
| `get_training_trends` | Per-week training load (distance, time, elevation, sport mix) |
| `get_personal_bests` | Top 5 by distance, duration, elevation, and speed; biggest week; longest streak |
| `get_yearly_breakdown` | Year-over-year totals with per-sport breakdown |
| `get_gear_info` | Registered bikes and shoes with accumulated mileage |
| `get_activity_detail` | Deep single-activity detail: laps, HR, power, cadence, PRs, gear |
| `get_activity_streams` | Raw GPS streams (lat/lon, altitude, HR, cadence, velocity, power) |
| `launch_flythrough` | Trigger a 3D flythrough render — returns an action payload the UI converts to a server-side render + download button |

### Garmin Tools (12)

| Tool | What it returns |
|---|---|
| `get_garmin_activities` | Garmin activity list with distance, pace, HR, calories, training effect |
| `get_garmin_activity_detail` | Per-lap splits, HR zone breakdown, power zones for one activity |
| `get_garmin_daily_health` | Steps, calories, resting HR, stress, intensity minutes, Body Battery for one day |
| `get_garmin_heart_rate_timeline` | Full-day HR in ~15-minute intervals |
| `get_garmin_sleep` | Sleep stages (deep/REM/light/awake), sleep score, SpO₂, HRV for one night |
| `get_garmin_body_battery` | Daily Body Battery highs, lows, intraday timeline over a date range |
| `get_garmin_hrv_status` | Last-night HRV, personal baseline range, readiness status |
| `get_garmin_training_metrics` | VO₂max, training load (7 d / 28 d), training status, race predictions |
| `get_garmin_wellness_trends` | Multi-day rollup of HR, steps, stress, sleep score, Body Battery |
| `get_garmin_steps_timeline` | 15-minute step buckets with activity level for one day |
| `get_garmin_stress_timeline` | Intraday stress levels (~3-min intervals) with avg, peak, and category for one day |
| `get_garmin_body_composition` | Weight, BMI, body fat %, and muscle mass measurements over a date range (Garmin scale required) |

### Adding a New Tool

Four steps:

1. Add the schema to the server's `tools` list.
2. Implement the async handler (returns JSON string).
3. Register it in `_dispatch`.
4. Done — the FetchingAgent picks it up automatically via `get_all_openai_tools()`.

```python
# 1. Schema — in servers/strava.py SimpleMCPServer.__init__
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

To also render it as a chart in Chat, add a `@register("get_example_metric")` renderer in `ui/viz.py` and add the tool name to the `_RENDERABLE` set in `servers/agents/visualization.py` and to the renderable-tools list in `_SYSTEM` in the same file.

## Setup

### Prerequisites

- Python 3.10 or later
- A [Strava API application](https://www.strava.com/settings/api) (callback domain: `localhost`)
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

# Install Chromium for server-side flythrough rendering (one-time, per environment)
playwright install chromium --with-deps

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

### Access PIN (optional)

To restrict access when running on a local network:

1. Edit `.streamlit/secrets.toml`:
   ```toml
   APP_PIN = "your-pin-here"
   ```
2. The app shows a PIN form on every new session. The sidebar shows a **🔒 Lock** button to log out.
3. If `APP_PIN` is not set, the gate is bypassed (open access).

## Authentication

### Strava OAuth

Strava OAuth runs automatically on first use. The app opens a browser window to authorise access; tokens are saved to `.tokens/strava.json` and refreshed automatically on subsequent runs.

### Garmin Setup

```bash
python auth/garmin_setup.py
```

Run once after filling in `GARMIN_EMAIL` and `GARMIN_PASSWORD`. Tokens are stored in `.tokens/` and persist until they expire. Re-run if Garmin login fails.

## Running

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501). For phone/remote access, set `address = "0.0.0.0"` in `.streamlit/config.toml` and open `http://<your-machine-ip>:8501`.

## Caching and Rate Limits

- Data loaders use `@st.cache_data` with TTLs from 5 minutes (today's data) to 30 minutes (multi-day wellness trends).
- FetchingAgent caps parallel workers at 5 to respect Garmin Connect rate limits.
- FetchingAgent caps plan steps at 60 to prevent runaway tool calls on broad questions.
- `null` sleep fields mean the device recorded no data — excluded from averages and noted explicitly.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Strava auth fails | Delete `.tokens/strava.json` and reload the app to re-authorise |
| Garmin tokens expired | Re-run `python auth/garmin_setup.py` |
| Port 8080 already in use | Kill the process or change `REDIRECT_URI` in `auth/strava_oauth.py` |
| No route visible on map | Activity has no GPS stream (indoor workout or Strava privacy zone) |
| Chat returns "Garmin not connected" | Run `auth/garmin_setup.py` and confirm `.tokens/garmin_tokens.json` exists |
| Flythrough render fails: `playwright` import error | Run `pip install playwright && playwright install chromium --with-deps` |
| Flythrough render very slow (CPU 100%, GPU idle) | `PLAYWRIGHT_SWIFTSHADER=1` is set — remove it to use GPU acceleration |
| Flythrough render times out on a GPU-less server | Set `PLAYWRIGHT_SWIFTSHADER=1` in the environment |
| New Strava activities not visible in Dashboard | Use **🔄 Refresh data** in the sidebar to clear the 5-minute cache |
| `from mcp.server.fastmcp import FastMCP` fails | A local `mcp/` directory is shadowing the installed MCP SDK — rename it to `servers/` |
