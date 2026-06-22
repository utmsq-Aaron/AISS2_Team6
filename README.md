# FitDash

FitDash is a Streamlit sports analytics dashboard that unifies Strava activities and Garmin health data behind an agentic chat interface. Every answer comes from live API data — no cached summaries, no hallucinated numbers.

📖 **Architecture:** [`docs/mcp-architecture.md`](docs/mcp-architecture.md) — MCP-standard design, how to add a new server, and how to extend with external MCP servers.

## Highlights

- **Dashboard** — activity map, summary metrics, training charts, live weather widget (temperature, wind, UV, pollen).
- **Activity Analysis** — stream-based charts (HR, pace, elevation, cadence, power) with colourised route overlays selectable by metric.
- **Health** — Garmin wellness trends: Body Battery, sleep stages, stress, HR, steps, training metrics, HRV.
- **Chat** — AI sports analyst backed by a LangGraph + A2A multi-agent system: an orchestrator delegates to recovery / training-load / context / route specialists (each scoped to its own MCP tools) and synthesises a data-driven answer from live data only.
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
 :8102 routes    :8105 calendar  :8106 telegram*
 (native FastMCP servers — each an independent process)
 (* telegram = Streamable-HTTP proxy to the external stdio telegram-mcp, optional)
```

All data flows through the MCP servers — the UI never calls Strava or Garmin APIs directly. Each server handles auth, retries, and data formatting; the UI receives clean, ready-to-display JSON.

The **Chat** tab is powered by a **LangGraph + A2A multi-agent** system. An **Orchestrator Agent** (`core/orchestrator_agent.py`, A2A server :9000) decomposes each request and delegates over the **Agent-to-Agent (A2A) protocol** to four specialist agents — **Recovery** :9001, **Training-Load** :9002, **Context** :9003, **Route** :9004 — each a LangGraph ReAct agent scoped to just its MCP servers (recovery→Garmin, load→Strava+Garmin, context→Weather+Calendar, route→Routes). Tools are still discovered via `ToolHost`, never hardcoded — only narrowed per agent. `core/orchestrator.py` is now a thin A2A client adapter, so the Chat tab, the FastAPI layer and the Telegram bridge keep the same interface.

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
│   ├── orchestrator.py          # Thin A2A client adapter → orchestrator agent (:9000)
│   ├── orchestrator_agent.py    # Orchestrator A2A server (:9000) — LangGraph coordinator
│   ├── a2a_client.py            # A2A client helper (status + artifacts)
│   ├── mcp_langchain.py         # ToolHost → LangChain tools, scoped per agent
│   └── agent_trace.py           # Trace assembly (route_data, charts, agents)
│
├── servers/
│   ├── weather_mcp.py           # FastMCP server — weather via Open-Meteo (port 8101)
│   ├── routes_mcp.py            # FastMCP server — routes via OpenRouteService (port 8102)
│   ├── strava_mcp.py            # FastMCP server — Strava v3 API, OAuth2 (port 8103)
│   ├── garmin_mcp.py            # FastMCP server — Garmin Connect (port 8104)
│   ├── calendar_mcp.py          # FastMCP server — Google Calendar, read-only (port 8105)
│   └── telegram_mcp.py          # Proxy → external stdio telegram-mcp (port 8106, optional)
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

### Flythrough (port 8107) — 1 tool

| Tool | What it returns |
|---|---|
| `flythrough__prepare_flythrough` | Validates render params and returns a `show_flythrough` action payload for the UI |

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

### Telegram (port 8106) — optional, 116 tools

Unlike the others, this is **not** a native FastMCP server. [`servers/telegram_mcp.py`](servers/telegram_mcp.py) is a thin proxy that runs the external [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) (stdio-only) unmodified in its own `uv` environment and re-exposes its tools over Streamable HTTP, so `ToolHost` reaches them like any other server. Tools are discovered live (`telegram__send_message`, `telegram__list_chats`, `telegram__search_messages`, …) — send/edit/delete/forward/pin messages, manage chats, contacts, media and drafts. Set `TELEGRAM_EXPOSED_TOOLS=read-only` to expose only read tools. See [Telegram Setup](#telegram-setup).

## Adding a New Server

One file + one line — see [`docs/mcp-architecture.md`](docs/mcp-architecture.md) §3 for the full walkthrough.

```python
# servers/example_mcp.py
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("example", host="127.0.0.1",
              port=int(os.getenv("EXAMPLE_MCP_PORT", "8108")), stateless_http=True)

@mcp.tool()
def my_tool(param: str) -> dict:
    """Clear description — the model picks this tool based solely on this text."""
    return {"result": param}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

Then one line in `core/config.py`:
```python
"example": _url("example", 8108),
```

Start with `python -m servers.example_mcp`. `ToolHost` discovers the new tools automatically; the Chat agent can call them immediately — no other file needs to change.

## Setup

### Prerequisites

- Python 3.11 or later
- An LLM provider — either a KIT Gateway / OpenAI-compatible key (default), **or** a Google [Gemini](https://aistudio.google.com/apikey) key: set `LLM_PROVIDER=gemini` + `GEMINI_API_KEY` + `GEMINI_MODEL` (a free flash model, e.g. `gemini-2.0-flash`)
- *(Optional)* A Garmin Connect account for the Health tab and activity data in Chat
- *(Optional)* An [OpenRouteService](https://openrouteservice.org/dev/#/signup) key for route planning (free, no credit card)
- *(Optional)* A Strava API application — note: Strava requires a paid subscription since May 2025
- *(Optional, for Telegram)* [`uv`](https://docs.astral.sh/uv/) and Telegram API credentials from [my.telegram.org/apps](https://my.telegram.org/apps). The [telegram-mcp](https://github.com/chigwell/telegram-mcp) upstream is vendored in `./external/`.

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
| `TELEGRAM_API_ID` | No | Telegram API ID — enables the Telegram server |
| `TELEGRAM_API_HASH` | No | Telegram API hash |
| `TELEGRAM_SESSION_STRING` | No | Telegram session string (see Telegram Setup) |

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

### Telegram Setup

Optional. The Telegram tools come from the external [telegram-mcp](https://github.com/chigwell/telegram-mcp), which runs unmodified in its own `uv` environment behind `servers/telegram_mcp.py`.

**Easiest:** open **⚙️ Settings → Telegram** in the running app — enter your API ID & hash, then sign in with your phone number to generate and save the session string automatically (2FA supported). Afterwards (re)start the server: `python -m servers.telegram_mcp`. The manual / CLI route does the same thing:

```bash
# 1. The upstream server is vendored in this repo at external/telegram-mcp
#    (if missing: git clone https://github.com/chigwell/telegram-mcp external/telegram-mcp)

# 2. Put TELEGRAM_API_ID / TELEGRAM_API_HASH in .env (from my.telegram.org/apps)

# 3. Generate a session string ONCE — interactive (QR scan or phone code),
#    because login is disabled when the server runs headless:
uv run --directory external/telegram-mcp session_string_generator.py
#    Copy the printed string into .env as:  TELEGRAM_SESSION_STRING=...
#    (answer "N" to its auto-update prompt — it would write to the wrong .env)
```

The session string grants full access to your Telegram account — treat it like a password; it lives only in `.env` (gitignored). The first `python -m servers.telegram_mcp` will have `uv` install the upstream's dependencies (one-time).

### Telegram chat — talk to the agent *from* Telegram (optional)

Separate from the Telegram *tools* above (which let the agent act on your account), the **agent bridge** ([`telegram_bridge.py`](telegram_bridge.py)) lets you chat *with* the agent from Telegram: every message you receive is forwarded to the same engine as the **💬 Chat** tab, and the answer is sent back. You can also send **voice memos** — they're transcribed locally with Whisper (German/English, auto-detected) and handled like a typed message. A planned route arrives three ways: a **static map image**, a tappable **Google Maps** link in the caption (opens the Maps app — approximate, since Google re-routes between points), and a **GPX** file with the exact track (open in OsmAnd, Komoot, Organic Maps, Garmin, Strava, …). Per-chat history replaces the web UI's interactive widgets (the agent lists options as text; you pick one by replying).

It runs as a **userbot** (it replies *as you*) in its own long-running process:

```bash
# Reuses TELEGRAM_API_ID/HASH + TELEGRAM_SESSION_STRING from .env
python telegram_bridge.py
```

By default it answers **DMs only** and is open to **anyone** who messages you; restrict with `TELEGRAM_ALLOWED_USERS`, allow groups with `TELEGRAM_BRIDGE_ALLOW_GROUPS=true` (see `.env.example`). ⚠️ Over Telegram the agent keeps **all** its tools — including the ones that read/send messages on your own account — so anyone you allow effectively controls them.

Reusing your existing session string is fine. Only if you run the bridge **and** the `telegram_mcp` proxy at the same time, give the bridge its own login so Telegram doesn't revoke the shared key:

```bash
python telegram_bridge.py --login   # prints a TELEGRAM_BRIDGE_SESSION_STRING for .env
```

**Voice memos** need a local Whisper engine. `faster-whisper` (in `requirements.txt`) runs everywhere with no system `ffmpeg`. On **Apple Silicon** you can opt into the faster GPU engine with `pip install mlx-whisper` **and** `brew install ffmpeg` — the bridge auto-detects it; otherwise it uses faster-whisper. The model (`small` ≈ 0.5 GB) downloads on first use; pick another with `WHISPER_MODEL` (bigger = better German, slower).

## Running

The easiest path is a launcher that starts the MCP servers, the five A2A agents and the API/UI for you: **`./dev_stack.sh`** (React/Vite stack) or **`./start.sh`** (Terminal windows). Manually:

```bash
# Terminal 1 — MCP servers (each in its own process)
source .venv/bin/activate
python -m servers.weather_mcp &
python -m servers.routes_mcp &
python -m servers.strava_mcp &
python -m servers.garmin_mcp &
python -m servers.calendar_mcp &
python -m servers.telegram_mcp &

# Terminal 1b — A2A agent layer (the Chat engine). Specialists first, orchestrator last.
python -m agents.recovery_agent &      # :9001
python -m agents.load_agent &          # :9002
python -m agents.context_agent &       # :9003
python -m agents.route_agent &         # :9004
python -m core.orchestrator_agent &    # :9000

# Terminal 2 — the UI
streamlit run app.py

# Terminal 3 (optional) — talk to the agent FROM Telegram (userbot bridge)
python telegram_bridge.py
```

> The agent layer needs `OPENAI_*` / `AGENT_MODEL` set; for the multi-call agent loops a stable model is recommended — set `AGENT_LLM_MODEL=kit.gpt-4.1` (the agent layer uses it in preference to `AGENT_MODEL`).

Or with Docker Compose:
```bash
docker compose up --build weather-mcp routes-mcp strava-mcp garmin-mcp calendar-mcp
streamlit run app.py
```
(The Telegram proxy shells out to `uv` and isn't part of the shared image — run it on the host as above.)

Open [http://localhost:8501](http://localhost:8501).

## Serving it publicly (single host, e.g. a Mac mini)

To host the **React app** for others over the internet there's a one-command launcher:

```bash
./server-start.sh
```

It builds the SPA and starts everything (MLflow + MCP servers + agents + FastAPI + the
Node BFF), puts the app behind a shared **PIN gate** (PIN **`230626`**), and publishes it
over HTTPS via **Tailscale Funnel** — using a stable signing key persisted in
`.secrets/auth_secret` so logins survive restarts. Only the BFF (`127.0.0.1:3000`) is
fronted; FastAPI, the agents and the MCP servers stay on localhost. (`./serve.sh` is the
underlying launcher if you want to pass your own `APP_PIN` / `AUTH_SECRET` / `FUNNEL`.)

**Auth model:** login is **email + OTP** — a visitor enters their email, gets a 6-digit
code (emailed *from* the admin Gmail), and enters it; the first time registers the account
(`data/accounts.json`). The shared PIN gates *reaching* the login screen. Only the **admin**
(`kit.aiss2026@gmail.com`) sees the **Settings** tab.

**Two one-time setup steps** the launcher can't do for you (it preflight-warns if they're
missing):

1. **Connect Google/Gmail** (powers OTP email + calendar). On the host, sign in as
   `kit.aiss2026@gmail.com`:
   ```bash
   python auth/google_oauth.py
   ```
   …and enable both the **Gmail API** and **Calendar API** for the project in the
   [Google Cloud console](https://console.cloud.google.com/apis/library). Register the
   redirect URI `http://localhost:8000/api/settings/google/callback`.
   *(Chicken-and-egg note: OTP email needs Gmail connected, but in-app Connect is admin-only —
   so do this CLI step before first login, or start once with `OTP_DEV_ECHO=1` to read codes
   from `/tmp/fitdash_api.log`.)*
2. **Install Tailscale** for the public URL:
   ```bash
   brew install tailscale && sudo tailscale up   # then enable Funnel + HTTPS in the admin console
   ```
   Without it, the app still runs but **local-only** (no public URL).

Then: open the public `https://<host>.<tailnet>.ts.net` URL → enter PIN **230626** → log in
by email. Full detail (autostart on boot via `launchd`, custom domains, security notes) is in
[`docs/deploy-macmini.md`](docs/deploy-macmini.md).

**Required env for a public deploy** (in `.env`): the usual `OPENAI_*` / `AGENT_MODEL`, the
`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, and optionally `ADMIN_EMAIL` (defaults to
`kit.aiss2026@gmail.com`). `AUTH_SECRET` is generated for you on first run.

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
| Telegram tools missing / server exits | Check `external/telegram-mcp` exists, `uv` is installed, and `TELEGRAM_SESSION_STRING` is valid (regenerate with `session_string_generator.py`). Watch its stderr for `[telegram] N tool(s) ready`. |
| Telegram bridge: `AuthKeyDuplicatedError` | The bridge and the `telegram_mcp` proxy are on the same session at once. Give the bridge its own login: `python telegram_bridge.py --login` → `TELEGRAM_BRIDGE_SESSION_STRING`. |
| Telegram bridge silent / no reply | Confirm the MCP servers are up (it logs `Agent ready — N tools`), the message is a **DM** (groups are off unless `TELEGRAM_BRIDGE_ALLOW_GROUPS=true`), and the sender is allowed (`TELEGRAM_ALLOWED_USERS`). |
| Voice memo not transcribed | First memo downloads the Whisper model (wait a bit); the bridge logs `🎤 transcribed via … (lang=…)`. Ensure `faster-whisper` is installed. The mlx engine additionally needs `brew install ffmpeg`. |
| New activities not visible | Use **🔄 Refresh data** in the sidebar to clear the cache. |
| Login OTP request stuck on "Sending…" / `(pending)` | The PIN gate must scope `express.json()` to `/bff/login` only (global parsing hangs proxied POSTs). Restart the BFF (`server-start.sh`) so it picks up `server/index.js`. |
| OTP email never arrives / `502` on request-otp | Google/Gmail not connected or missing the `gmail.send` scope. Run `python auth/google_oauth.py` (as `kit.aiss2026@gmail.com`) and enable the **Gmail API** in the Cloud console. Check Spam. For local testing without email, start with `OTP_DEV_ECHO=1` and read the code from `/tmp/fitdash_api.log`. |
| "Invalid or expired code" | Codes expire in 10 min and burn after 5 wrong tries — request a fresh one. |
| No Settings tab after login | Settings is admin-only — log in as `kit.aiss2026@gmail.com` (the `ADMIN_EMAIL`). |
| Everyone logged out after a restart | `AUTH_SECRET` changed. `server-start.sh` persists a stable one in `.secrets/auth_secret`; don't pass a different `AUTH_SECRET` over it. |
