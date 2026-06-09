# FitDash

FitDash is a Streamlit sports analytics dashboard that unifies Strava activities and Garmin health data behind an agentic chat interface. Every answer comes from live API data — no cached summaries, no hallucinated numbers.

📖 **Architecture:** [`docs/mcp-architecture.md`](docs/mcp-architecture.md) — MCP-standard design, how to add a new server, and how to extend with external MCP servers.

## Highlights

- **Dashboard** — activity map, summary metrics, training charts, live weather widget (temperature, wind, UV, pollen).
- **Activity Analysis** — stream-based charts (HR, pace, elevation, cadence, power) with colourised route overlays selectable by metric.
- **Health** — Garmin wellness trends: Body Battery, sleep stages, stress, HR, steps, training metrics, HRV.
- **Chat** — AI sports analyst backed by a tool-agnostic tool-use loop: the model discovers and calls tools itself (Strava, Garmin, Weather, Routes) and answers from live data only.
- **Routes** — route planning powered by OpenRouteService: circular routes, A→B routes, trail search, isochrone maps.
- **Sync** — export Garmin activities to Strava as FIT files with preview and selection controls.
- **Settings** — configure all API connections (LLM key, Strava OAuth, Garmin, ORS) directly in the app.
- **PIN barrier** — optional access PIN stored in `.streamlit/secrets.toml` blocks the entire app until authenticated.

> **Note:** Strava's API became a paid service in May 2025. The app is fully functional without Strava — Garmin, Weather, and Routes work out of the box.

## Architecture

```
┌──────────────── UI (Streamlit) ─────────────────┐
│  app.py  ·  ui/dashboard.py  ·  ui/health.py    │
│  ui/chat.py  ·  ui/routes_explorer.py  · …      │
│     └── call_tool("server__tool", args)          │
└────────────────────┬────────────────────────────┘
                     │
        core/host.py · ToolHost  (single MCP client)
                     │  Streamable HTTP
     ┌───────────────┼───────────────┐
     ▼               ▼               ▼
 :8101 weather   :8103 strava    :8104 garmin
 :8102 routes    :8105 calendar
 (native FastMCP servers — each an independent process)
```

All data flows through the MCP servers — the UI never calls Strava or Garmin APIs directly. Each server handles auth, retries, and data formatting; the UI receives clean, ready-to-display JSON.

The **Chat** tab uses a tool-agnostic tool-use loop (`core/orchestrator.py`): the LLM discovers all 31 tools via `ToolHost.list_tools()` and decides itself which to call — no tool names are hardcoded anywhere.

## Project Layout

```
fitdash/
├── app.py                       # Streamlit entry point + PIN gate
├── requirements.txt
├── .env                         # API credentials (never committed)
├── .env.example                 # template — copy to .env and fill in
├── docker-compose.yml           # one service per MCP server
├── .streamlit/
│   ├── config.toml              # Streamlit theme + server config
│   └── secrets.toml             # APP_PIN (never committed)
│
├── auth/
│   ├── strava_oauth.py          # OAuth2 manager: token cache and auto-refresh
│   └── garmin_setup.py          # One-time Garmin MFA login
│
├── core/                        # MCP-standard engine — Streamlit-free, vendor-neutral
│   ├── config.py                # Declarative registry: server name → MCP URL
│   ├── host.py                  # ToolHost — the single MCP client (list_tools / call_tool)
│   ├── llm.py                   # Vendor-neutral LLM seam (provider/model from config)
│   └── orchestrator.py          # Tool-agnostic tool-use loop (drives the Chat tab)
│
├── servers/
│   ├── weather_mcp.py           # FastMCP server — weather via Open-Meteo (port 8101)
│   ├── routes_mcp.py            # FastMCP server — routes via OpenRouteService (port 8102)
│   ├── strava_mcp.py            # FastMCP server — Strava v3 API, OAuth2 (port 8103)
│   ├── garmin_mcp.py            # FastMCP server — Garmin Connect (port 8104)
│   └── calendar_mcp.py          # FastMCP server — Google Calendar, read-only (port 8105)
│
└── ui/
    ├── shared.py                # ToolHost singleton, call_tool(), connection checks
    ├── styles.py                # CSS variables, chart theme, colour constants
    ├── dashboard.py             # Dashboard tab
    ├── activity_analysis.py     # Stream charts + coloured route overlay
    ├── health.py                # Health tab
    ├── chat.py                  # Chat tab
    ├── routes_explorer.py       # Routes tab
    ├── settings.py              # Settings tab (API key management, OAuth flows)
    └── sync.py                  # Garmin → Strava export tab
```

## MCP Servers and Tools

Each server is a self-contained FastMCP service. The UI calls every tool via `call_tool("server__tool_name", args)` — namespaced, uniform, no special-casing per server.

### Weather (port 8101) — 4 tools

| Tool | What it returns |
|---|---|
| `weather__get_current_weather` | Current conditions: temperature, wind, weather code |
| `weather__get_weather_forecast` | Multi-day forecast |
| `weather__get_pollen_levels` | Pollen load (grasses, birch, alder, mugwort) — scale 0–5 |
| `weather__get_uv_index` | UV index with WHO category |

### Routes (port 8102) — 5 tools

| Tool | What it returns |
|---|---|
| `routes__plan_route` | A→B route with waypoints, distance, duration, elevation profile |
| `routes__plan_circular_route` | Loop route from a start point for a target distance |
| `routes__get_elevation_profile` | Elevation profile for a given route |
| `routes__explore_trails` | Paginated trail search (hiking/cycling/running/MTB) within a radius |
| `routes__get_isochrone` | Reachability polygon for a time or distance budget |

### Strava (port 8103) — 10 tools

| Tool | What it returns |
|---|---|
| `strava__get_activities` | Recent activities with distance, pace, HR, elevation, kudos, map polyline |
| `strava__get_activity_stats` | Aggregate totals and per-sport breakdown |
| `strava__get_athlete_profile` | Athlete profile + official YTD / last-4-weeks / all-time stats |
| `strava__get_training_trends` | Per-week training load (distance, time, elevation, sport mix) |
| `strava__get_personal_bests` | Top 5 by distance, duration, elevation, speed; biggest week; longest streak |
| `strava__get_yearly_breakdown` | Year-over-year totals with per-sport breakdown |
| `strava__get_gear_info` | Registered bikes and shoes with accumulated mileage |
| `strava__get_activity_detail` | Deep single-activity detail: laps, HR, power, cadence, PRs, gear |
| `strava__get_activity_streams` | Raw GPS streams (lat/lon, altitude, HR, cadence, velocity, power) |
| `strava__launch_flythrough` | Trigger a 3D flythrough render — returns action payload for the UI |

### Garmin (port 8104) — 13 tools

| Tool | What it returns |
|---|---|
| `garmin__get_garmin_activities` | Garmin activity list with distance, pace, HR, calories, training effect |
| `garmin__get_garmin_activity_detail` | Per-lap splits, HR zone breakdown for one activity |
| `garmin__get_garmin_daily_health` | Steps, calories, resting HR, stress, Body Battery for one day |
| `garmin__get_garmin_heart_rate_timeline` | Full-day HR in ~15-minute intervals |
| `garmin__get_garmin_sleep` | Sleep stages (deep/REM/light/awake), sleep score, SpO₂, HRV for one night |
| `garmin__get_garmin_body_battery` | Daily Body Battery highs, lows, intraday timeline over a date range |
| `garmin__get_garmin_hrv_status` | Last-night HRV, personal baseline range, readiness status |
| `garmin__get_garmin_training_metrics` | VO₂max, training load (7 d / 28 d), training status, race predictions |
| `garmin__get_garmin_wellness_trends` | Multi-day rollup of HR, steps, stress, sleep score, Body Battery |
| `garmin__get_garmin_steps_timeline` | 15-minute step buckets with activity level for one day |
| `garmin__get_garmin_stress_timeline` | Intraday stress levels (~3-min intervals) with avg, peak, category |
| `garmin__get_garmin_body_composition` | Weight, BMI, body fat %, muscle mass over a date range |
| `garmin__get_activity_gps_track` | Full GPS track (lat/lon/ele/time) for one Garmin activity |

## Adding a New Server

One file + one line — see [`docs/mcp-architecture.md`](docs/mcp-architecture.md) §3 for the full walkthrough.

```python
# servers/example_mcp.py
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("example", host="127.0.0.1",
              port=int(os.getenv("EXAMPLE_MCP_PORT", "8106")), stateless_http=True)

@mcp.tool()
def my_tool(param: str) -> dict:
    """Clear description — the model picks this tool based solely on this text."""
    return {"result": param}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

Then one line in `core/config.py`:
```python
"example": _url("example", 8106),
```

Start with `python -m servers.example_mcp`. `ToolHost` discovers the new tools automatically; the Chat agent can call them immediately — no other file needs to change.

## Setup

### Prerequisites

- Python 3.11 or later
- A KIT Gateway API key (from the Übungsleitung / DSI portal) — or any OpenAI-compatible key
- *(Optional)* A Garmin Connect account for the Health tab and activity data in Chat
- *(Optional)* An [OpenRouteService](https://openrouteservice.org/dev/#/signup) key for route planning (free, no credit card)
- *(Optional)* A Strava API application — note: Strava requires a paid subscription since May 2025

### Installation

```bash
# Clone and enter the project
git clone <repo-url>
cd fitdash

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up credentials
cp .env.example .env
# Edit .env — see Environment Variables below
```

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | KIT Gateway key or any OpenAI-compatible key |
| `OPENAI_BASE_URL` | Yes | `https://ai-gateway.dsi-experimente.de/v1` for KIT |
| `AGENT_MODEL` | Yes | `kit.gpt-4.1` (recommended) |
| `GARMIN_EMAIL` | No | Garmin Connect email — enables Health tab and Chat |
| `GARMIN_PASSWORD` | No | Garmin Connect password |
| `ORS_API_KEY` | No | OpenRouteService key — enables Routes tab |
| `CLIENT_ID` | No | Strava app client ID (paid API since May 2025) |
| `CLIENT_SECRET` | No | Strava app client secret |

All settings can also be configured at runtime in the **⚙️ Settings** tab.

### Access PIN (optional)

To restrict access when running on a local network, add to `.streamlit/secrets.toml`:
```toml
APP_PIN = "your-pin-here"
```
If `APP_PIN` is not set, the gate is bypassed (open access).

## Authentication

### Strava OAuth

Strava OAuth runs automatically on first use — the app opens a browser window to authorise access. Tokens are saved to `.tokens/strava.json` and refreshed automatically.

### Garmin Setup

```bash
python auth/garmin_setup.py
```

Run once after filling in `GARMIN_EMAIL` and `GARMIN_PASSWORD`. Tokens persist in `.tokens/` until they expire; re-run if login fails.

## Running

Start the MCP servers, then the Streamlit app:

```bash
# Terminal 1 — start all MCP servers (each in its own process)
source .venv/bin/activate
python -m servers.weather_mcp &
python -m servers.routes_mcp &
python -m servers.strava_mcp &
python -m servers.garmin_mcp &
python -m servers.calendar_mcp &

# Terminal 2 — start the UI
streamlit run app.py
```

Or with Docker Compose:
```bash
docker compose up --build weather-mcp routes-mcp strava-mcp garmin-mcp calendar-mcp
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `LLM call failed: 400 AuthenticationError` | Check `OPENAI_API_KEY` in Settings. Ensure you are connected to the KIT network or VPN. |
| `LLM call failed: 400 invalid subscription key` | Wrong model name — use `kit.gpt-4.1`. |
| Chat response takes 30–60 s | Normal under gateway load. |
| Strava shows 0 activities | Account has no activities, or token expired — delete `.tokens/strava.json` and re-authorise. |
| Garmin tokens expired | Re-run `python auth/garmin_setup.py` |
| Port already in use | Kill the existing process or change the port via `STRAVA_MCP_PORT` / `GARMIN_MCP_PORT` env vars. |
| No route visible on map | Activity has no GPS stream (indoor workout or Strava privacy zone). |
| Chat returns "Garmin not connected" | Run `auth/garmin_setup.py` and confirm `.tokens/garmin_tokens.json` exists. |
| MCP server not reachable | Confirm the server process is running: `curl http://127.0.0.1:8103/mcp` should return 200. |
| New activities not visible | Use **🔄 Refresh data** in the sidebar to clear the cache. |
