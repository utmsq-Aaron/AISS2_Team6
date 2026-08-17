# Training Copilot

An AI training coach that unifies **Strava**, **Garmin**, weather, your calendar, maps and a
sports-science library behind one conversation. Every answer comes from live API data — no
cached summaries, no invented numbers.

Under the hood it is a **multi-agent system over the Model Context Protocol**: an orchestrator
delegates each request to six specialist agents, each of which reaches its own independent MCP
servers. Tools are *discovered, never hardcoded* — adding a data source is one new file plus one
config line.

📖 **Architecture:** [`docs/mcp-architecture.md`](docs/mcp-architecture.md) (German) — the design,
how to add a server, how to plug in external MCP servers.

---

## Quick start

```bash
git clone <repo-url> && cd AISS2_Team6
./run.sh setup      # venv, dependencies, .env, account connections
./run.sh            # start everything → http://localhost:5173
```

`./run.sh setup` walks you through it and tells you exactly what is still missing. If you would
rather do it by hand, the full path is in [Setup from scratch](#setup-from-scratch) below.

**One launcher, five verbs:**

| Command | What it does |
|---|---|
| `./run.sh` | Development: the whole stack + Vite with hot reload → **:5173** |
| `./run.sh prod` | Production: builds the SPA, serves it behind the Node BFF → **:3000** |
| `./run.sh setup` | First-time setup — dependencies, `.env`, account connections |
| `./run.sh status` | What is currently running |
| `./run.sh stop` | Stop everything |
| `./run.sh logs <service>` | Tail one service (`api`, `orchestrator`, `mcp-strava`, …) |

Everything is idempotent: a service already on its port is left alone, so re-running is safe.
Logs live in `/tmp/training-copilot/`.

Prefer containers? `./docker-up.sh up --build` runs the **entire** stack in Docker → **:3000**.
See [Docker](#docker).

`run.sh` is a bash script and expects a Unix shell (it uses `lsof` and `pgrep` for its
"reuse what is already running" logic). On **Windows**, run it from **WSL2** — `localhost:5173`
is reachable from the Windows browser as usual — or use the Docker route above.

---

## What it can do

- **Coach.** One structured A-race goal plus milestones drives a real training plan — phases,
  weeks, workouts — with hard guardrails (ramp-rate cap, taper check, injury windows). All the
  math is deterministic arithmetic you can re-check; the LLM only chooses and explains workouts.
- **Chat.** Ask anything about your training. The orchestrator decides which specialists to
  involve and answers from live data, with charts and route maps inline.
- **Proactive check-ins.** The coach schedules its own follow-ups — a daily check-in that skips
  itself if you already talked, calendar-aware nudges before and after events, and long-running
  deep analyses that report back when finished.
- **Health.** Sleep, HRV, Body Battery, stress and training load from Garmin.
- **3D flythrough.** A cinematic camera flight over any activity's GPS track, exportable as MP4
  from the browser.
- **Garmin → Strava sync.** Settings finds Garmin activities that never reached Strava and
  uploads their original FIT files. Admin-only, and it always shows you the list before it
  uploads anything.
- **Telegram.** Optionally talk to the same coach over Telegram, voice memos included.

---

## Setup from scratch

Everything below is also automated by `./run.sh setup` — this is what it does, in case you want
to do it manually or something goes wrong.

### 1. Prerequisites

| | Version | Check |
|---|---|---|
| Python | 3.11+ | `python3 --version` |
| Node.js | 18+ | `node --version` — macOS: `brew install node` |
| Docker | optional | only for the container route |

### 2. Dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
( cd web && npm install )
```

The first install pulls `sentence-transformers` (and with it torch) for the local fitness-library
embeddings — expect a few minutes and ~2 GB.

### 3. Configuration

```bash
cp .env.example .env
```

Open `.env`. The **only** thing required to boot is an LLM key:

```ini
LLM_PROVIDER=openai                                     # openai | openai_official | gemini
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://ai-gateway.dsi-experimente.de/v1
AGENT_MODEL=kit.gpt-4.1
AGENT_LLM_MODEL=kit.gpt-4.1                             # the agent layer needs a stable model
```

Three providers are supported and switchable at runtime from **Settings → OpenAI / LLM**:

| `LLM_PROVIDER` | Keys | Notes |
|---|---|---|
| `openai` *(default)* | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AGENT_MODEL` | Any OpenAI-compatible endpoint (the KIT gateway) |
| `openai_official` | `OPENAI_OFFICIAL_API_KEY`, `OPENAI_MODEL` | api.openai.com |
| `gemini` | `GEMINI_API_KEY`, `GEMINI_MODEL` | A free flash model like `gemini-2.0-flash` works |

Everything else is optional — each integration simply stays dark until you configure it.

Two flags make local development less tedious. Both are read by the SPA out of this same
repo-root `.env` (`web/vite.config.ts` points Vite's `envDir` here), and only `VITE_`-prefixed
variables ever reach the browser bundle:

```ini
VITE_SHOW_GMAIL_REGISTRATION_PAGE=false   # skip the Google/Gmail onboarding page
# VITE_DEV_AUTO_LOGIN_EMAIL=you@example.com   # skip the email-OTP login entirely
```

`VITE_DEV_AUTO_LOGIN_EMAIL` makes the app call `POST /api/auth/dev-login` once on boot and land
you in the shell as that account. **Dev only** — never set it on anything reachable from outside.

### 4. Connect your accounts

Each of these is independent; skip any you do not need. All tokens land in `.tokens/`, which is
git-ignored and never leaves your machine.

All the one-off flows share an entry point: `.venv/bin/python -m auth <garmin|strava|gmail>`, or
`-m auth all` to walk through them in order. It just calls the scripts named below, so either
form works.

#### Strava — activities

1. Create an API application at <https://www.strava.com/settings/api>.
   Set **Authorization Callback Domain** to `localhost`.
2. Put the credentials in `.env`:
   ```ini
   CLIENT_ID=12345
   CLIENT_SECRET=...
   ```
3. Nothing else to run — the OAuth flow opens your browser automatically the first time a Strava
   tool is called. To trigger it deliberately: `.venv/bin/python auth/strava_oauth.py`.
   Token: `.tokens/strava.json`, refreshed automatically.

#### Garmin — sleep, HRV, Body Battery, stress

Garmin has no public API, so this logs in as you (via the `garminconnect` library) and caches the
session. **A one-time interactive login is required** because of MFA:

```bash
# 1. Credentials in .env
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=...

# 2. Run the one-time login — enter the MFA code when prompted
.venv/bin/python auth/garmin_setup.py
```

Token: `.tokens/garmin_tokens.json`. It refreshes itself; re-run the script if Garmin invalidates
the session (you will see auth errors on the Health page).

*No Garmin account?* Set `GARMIN_MOCK_HEALTH=true` to serve realistic synthetic health data from
`scripts/garmin_health_mock.py` — useful for demos and development.

#### Google — calendar, and the login emails

One script covers both the calendar integration and the mailbox that sends one-time login codes.

1. In the [Google Cloud Console](https://console.cloud.google.com/): create a project, enable the
   **Google Calendar API** and the **Gmail API**, and create an **OAuth client ID** of type
   *Desktop app*.
2. Put it in `.env`:
   ```ini
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   ADMIN_EMAIL=you@example.com     # the account that sends login codes; also the only admin
   ```
3. Run it, signed in as that account:
   ```bash
   .venv/bin/python auth/google_oauth.py
   ```

Two token files, deliberately separate: `.tokens/google_mail.json` (sending, admin-only) and
`.tokens/google.json` (calendar, user-connectable) — so a user reconnecting their calendar can
never clobber the mail credential.

#### Maps and routes — optional

```ini
ORS_API_KEY=...            # openrouteservice.org — free tier, for route planning
GOOGLE_MAPS_API_KEY=...    # a billing-free Maps Demo Key is enough
```

Get a [Maps Demo Key](https://mapsplatform.google.com/maps-demo-key/) — no credit card. Demo keys
serve no user-generated content (reviews, photos); the server degrades gracefully.

#### Telegram — optional

Lets you chat with the coach from Telegram, and is what keeps proactive check-ins running.

1. Get `api_id` / `api_hash` from <https://my.telegram.org/apps>, put them in `.env`.
2. Generate a session string once (interactive — headless login is disabled):
   ```bash
   .venv/bin/python telegram_bridge.py --login
   ```
   Copy the printed string into `.env` as `TELEGRAM_BRIDGE_SESSION_STRING`.
3. Start it with the stack: `TELEGRAM=1 ./run.sh`

> The bridge also hosts the **proactive scheduler**, so it runs by default even without Telegram
> configured — it just degrades to a headless worker and mirrors check-ins into the web Coach chat.
> `SCHEDULER=0 ./run.sh` turns that off; then the coach never checks in on its own.

### 5. Run it

```bash
./run.sh
```

Open <http://localhost:5173>. Agent traces are at <http://localhost:5001>.

**The fitness library (RAG) needs no setup step of its own.** The Fitness Expert answers from a
local vector index over five German sports-science works, and the first `./run.sh` builds it: the
extracted corpus text ships in the repo (`data/fitness_library/corpus/`), and the launcher runs
`build_fitness_index --if-missing` over it — ~2 min, plus a one-time ~460 MB download of the local
embedding model (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`). No embedding API, no key. To force a
rebuild: `.venv/bin/python -m scripts.build_fitness_index --rebuild`.

The **source PDFs are deliberately not in the repo** (copyright + size) and are not needed at
runtime — only to regenerate the corpus text itself, which you should not have to do. If you ever
do: put them in `data/fitness_library/Literatur_Fitness/` (filenames per `SOURCES.txt`), then
`extract_literature_corpus --replace` followed by `build_fitness_index --rebuild`.

Which works are in there, with ISBN/DOI and chunk counts: [`data/fitness_library/SOURCES.txt`](data/fitness_library/SOURCES.txt).
How the retrieval works: [`docs/fitness-rag.md`](docs/fitness-rag.md).

---

## Architecture

```
   React SPA (web/)  ·  Telegram bridge
                  │
            FastAPI (api/)                      :8000
                  │
        Orchestrator agent                      :9000    ← no tools of its own
                  │  A2A
   ┌──────┬───────┼───────┬─────────┬────────┐
recovery load  context  route   fitness   coach          :9001–:9006
   │      │       │       │        │         │
   │      │       │       │     RAG index    │
   └──────┴───────┴───────┴─────────────────┘
                  │  MCP (Streamable HTTP)
   weather · routes · strava · garmin · calendar ·        :8101–:8109
   flythrough · google_maps · athlete · (telegram)
```

Each box is its own process. The orchestrator has **no** MCP access — it only delegates over the
A2A protocol. Each specialist sees only the servers in its scope (`core/config.AGENT_MCP_SCOPE`)
and discovers their tools at runtime.

| Agent | Port | Reaches | Owns |
|---|---|---|---|
| Orchestrator | 9000 | the six specialists (A2A) | Request triage, delegation, trace assembly |
| Recovery | 9001 | garmin | Sleep, HRV, Body Battery, readiness |
| Load | 9002 | strava, garmin, flythrough | Volume, intensity, training load, trends |
| Context | 9003 | weather, calendar | Conditions and schedule around a session |
| Route | 9004 | routes, google_maps | Route planning, trails, places |
| Fitness | 9005 | *(none — local RAG)* | Training theory from a sports-science library |
| Coach | 9006 | athlete, strava, garmin | Goal, plan, zones, the adaptation loop |

---

## MCP servers and tools

Every tool is called uniformly as `call_tool("server__tool_name", args)` — namespaced, no
special-casing per server anywhere in the codebase.

### Athlete (8109) — 14 tools · the coach's backbone

The structured athlete store **and** the deterministic training math. This server calls no
upstream API — the coach agent feeds it numbers it fetched from Strava/Garmin. Everything
computable lives here as plain arithmetic the athlete can re-check; the LLM never estimates it.
The rules it implements are documented in [`docs/trainingsregeln.md`](docs/trainingsregeln.md).

| Tool | What it does |
|---|---|
| `athlete__get_athlete_overview` | Full structured state in one read — goal, milestones, zones, plan, timeline |
| `athlete__set_race_goal` | Set/replace the main goal (sport, date, distance, target time) |
| `athlete__add_milestone` · `update_milestone_status` · `delete_race_goal` | Milestones on the way to it |
| `athlete__set_athlete_profile` | Stable attributes (age, …) used for zone defaults |
| `athlete__compute_zones` | HR + pace zones, computed deterministically from supplied data |
| `athlete__scaffold_plan` | The goal-driven plan skeleton (phases → weeks) with a feasibility fact block |
| `athlete__save_plan` | Validates a filled plan against the guardrails, then stores it |
| `athlete__get_plan` | The stored plan |
| `athlete__record_week_actual` | Plan-vs-actual monitoring for one week |
| `athlete__rescaffold_plan` | Re-baselines the remaining weeks on demonstrated volume |
| `athlete__add_timeline_event` · `delete_timeline_event` | Injuries, illnesses, races, notes |

### Strava (8103) — 14 tools

| Tool | What it returns |
|---|---|
| `strava__get_activities` | Recent activities: distance, pace, HR, elevation, kudos, map polyline |
| `strava__search_activities` | Activity search by name, sport or date range |
| `strava__get_activity_stats` | Aggregate totals and per-sport breakdown |
| `strava__get_athlete_profile` | Profile + official YTD / last-4-weeks / all-time stats |
| `strava__get_training_trends` | Per-week training load (distance, time, elevation, sport mix) |
| `strava__get_training_load` | ATL / CTL / TSB — acute and chronic load, form |
| `strava__analyze_performance_trends` | Trend analysis over a metric across time |
| `strava__compare_activity_to_baseline` | One activity against the athlete's own history |
| `strava__get_personal_bests` | Top 5 by distance, duration, elevation, speed; biggest week; longest streak |
| `strava__get_yearly_breakdown` | Year-over-year totals per sport |
| `strava__get_gear_info` | Bikes and shoes with accumulated mileage |
| `strava__get_activity_detail` | Single activity in depth: laps, HR, power, cadence, PRs, gear |
| `strava__get_activity_streams` | Raw GPS streams (lat/lon, altitude, HR, cadence, velocity, power) |
| `strava__delete_activity` | Delete an activity (write access) |

### Garmin (8104) — 13 tools

| Tool | What it returns |
|---|---|
| `garmin__get_garmin_activities` | Activity list with distance, pace, HR, calories, training effect |
| `garmin__get_garmin_activity_detail` | Per-lap splits and HR-zone breakdown for one activity |
| `garmin__get_garmin_daily_health` | Steps, calories, resting HR, stress, Body Battery for one day |
| `garmin__get_garmin_heart_rate_timeline` | Full-day HR in ~15-minute intervals |
| `garmin__get_garmin_sleep` | Sleep stages, sleep score, SpO₂, HRV for one night |
| `garmin__get_garmin_body_battery` | Daily highs/lows plus intraday timeline over a range |
| `garmin__get_garmin_hrv_status` | Last-night HRV, personal baseline range, readiness |
| `garmin__get_garmin_training_metrics` | VO₂max, training load (7 d / 28 d), status, race predictions |
| `garmin__get_garmin_wellness_trends` | Multi-day rollup of HR, steps, stress, sleep, Body Battery |
| `garmin__get_garmin_steps_timeline` | 15-minute step buckets with activity level |
| `garmin__get_garmin_stress_timeline` | Intraday stress (~3-min intervals) with avg, peak, category |
| `garmin__get_garmin_body_composition` | Weight, BMI, body fat %, muscle mass over a range |
| `garmin__get_activity_gps_track` | Full GPS track (lat/lon/ele/time) for one activity |

### Weather (8101) — 4 tools

`weather__get_current_weather` · `get_weather_forecast` · `get_pollen_levels` (grasses, birch,
alder, mugwort — scale 0–5) · `get_uv_index` (with WHO category). Backed by Open-Meteo, no key needed.

### Routes (8102) — 7 tools

`routes__plan_route` (A→B with waypoints, distance, duration, elevation) · `plan_circular_route`
(a loop of a target distance) · `plan_park_loop` (a loop through green space) · `geocode` ·
`get_elevation_profile` · `explore_trails` (paginated, by sport, within a radius) ·
`get_isochrone` (reachability polygon). Needs `ORS_API_KEY`.

### Calendar (8105) — 6 tools

`calendar__list_calendars` · `list_events` · `get_event` · `create_event` · `update_event` ·
`delete_event` — so the coach can see your week and place sessions in it.

### Flythrough (8107) — 1 tool

`flythrough__prepare_flythrough` validates render parameters and returns the action payload the
UI turns into a 3D flight over the activity's GPS track.

### Google Maps (8108) — 6 tools, optional

`google_maps__maps_search_places` (POIs) · `maps_search_along_route` · `maps_place_details` ·
`maps_geocode` · `maps_reverse_geocode` · `maps_directions` (walking/driving/cycling/transit
ETA). Native server
against Google's current APIs — Places (New), Geocoding v4, Routes — all of which work with a
billing-free demo key.

### Telegram (8106) — 116 tools, optional

The one server that is **not** native. [`servers/telegram_mcp.py`](servers/telegram_mcp.py) is a
proxy that runs the external [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp)
(stdio-only, pinned to Python 3.13) unmodified in its own `uv` environment and re-exposes its
tools over Streamable HTTP — so `ToolHost` sees it as just another server. Tools are discovered
live. Set `TELEGRAM_EXPOSED_TOOLS=read-only` to expose only the read tools.

---

## Adding a new server

The whole point of the design: **one new file, one config line.** No change to the host, the
agents, or the UI.

```python
# servers/example_mcp.py
mcp = FastMCP("example", host="127.0.0.1",
              port=int(os.getenv("EXAMPLE_MCP_PORT", "8110")), stateless_http=True)

@mcp.tool()
def my_tool(param: str) -> dict:
    """Clear description — the model picks this tool based solely on this text."""
    return {"result": param}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

Then add `"example": 8110` to `MCP_PORTS` in [`core/config.py`](core/config.py) (8110 is the next
free port), grant it to an agent in `AGENT_MCP_SCOPE`, and restart. `MCP_PORTS` is the **single
source of truth for every port** — `ports.sh`, `web/vite.config.ts` and `docker-compose.yml` all
read it live via `scripts/export_ports.py`, so never hardcode a port anywhere else.

---

## Docker

The full stack — 20 services — runs in containers:

```bash
./docker-up.sh up --build      # → http://localhost:3000
./docker-up.sh ps
./docker-up.sh logs -f coach-agent
./docker-up.sh down
```

**Always** go through `./docker-up.sh` rather than `docker compose` directly. It does three
things Compose cannot do for itself: regenerate the port variables from `core/config.py`, create
the bind-mount directories before the daemon creates them root-owned, and persist a stable
`AUTH_SECRET`.

You still need a filled-in `.env`, and the OAuth tokens have to be created once on the host
(`./run.sh setup`) — the containers mount `.tokens/` rather than carrying credentials in an
image. `.tokens/`, `data/`, `.cache/`, `.logs/` and `.secrets/` are all mounted, so state
survives `down`.

One exception: the Telegram MCP proxy is not containerised — it needs `uv` and Python 3.13. Run
it on the host if you want it.

---

## Serving it publicly

```bash
DO_LOCK=true APP_PIN='a-long-passphrase' FUNNEL=1 ./run.sh prod
```

- `DO_LOCK=true` + `APP_PIN` put a shared passphrase in front of everything. The gate is
  rate-limited with per-IP lockout, the PIN is compared in constant time, and the session cookie
  is HMAC-signed.
- `AUTH_SECRET` signs the login tokens. `run.sh` generates and persists one in `.secrets/` on
  first use. **Without it, tokens are signed with a public dev fallback and can be forged.**
- `ADMIN_EMAIL` names the one account with full Settings access. **Unset means nobody is admin** —
  that is deliberate, so a fresh deployment does not inherit someone else's admin.
- `FUNNEL=1` publishes it over HTTPS via Tailscale Funnel (needs `tailscale` installed and
  `tailscale up` done once).

Users log in with email + a one-time code, sent from the mailbox you connected with
`auth/google_oauth.py`. The first valid code for a new address creates that account.

For a permanent install (launchd on macOS), see [`docs/deploy-macmini.md`](docs/deploy-macmini.md)
and the template in [`deploy/`](deploy/).

---

## Project layout

```
run.sh                  the one launcher
docker-up.sh            the Docker wrapper
core/                   MCP host, LLM seam, orchestrator adapter, agent trace — no UI, no vendor lock-in
  config.py             the registry: name → MCP/A2A URL, and every port
  host.py               ToolHost — the single tool surface
  orchestrator_agent.py the LangGraph orchestrator (:9000)
agents/                 the six specialists + their prompts
servers/                one FastMCP server per data source
api/                    FastAPI seam — auth, chat SSE, charts, settings
web/                    React + Vite SPA
server/                 Node BFF — serves the SPA, proxies /api, hosts the PIN gate
auth/                   one-time OAuth setup scripts
scripts/                index builder, port exporter, Garmin mock
tests/                  unit/ (offline, what pytest runs), integration/ (live stack), tools/ (debug)
evaluation/             the quality evaluation harness — personas, scorers, reports
docs/                   architecture, training rules, RAG, deployment
external/               vendored third-party MCP server (not our code)
```

---

## Testing, linting and evaluation

Everything below runs on a fresh checkout — no accounts, no API keys, no running
stack:

```bash
pip install -r requirements-dev.txt   # ruff + pytest

pytest                                # the offline test suite (tests/unit)
ruff check .                          # lint the Python side

( cd web && npm run typecheck )       # types
( cd web && npm run lint )            # React/TS patterns — hooks, dead code
( cd web && npm run build )
```

`tests/unit/` are real tests and are what `pytest` collects; `tests/integration/`
holds the end-to-end scripts that need the live stack, an LLM gateway and real
Strava/Garmin accounts; `tests/tools/` are debug utilities. See
[`tests/README.md`](tests/README.md) for what is what.

Lint configuration lives in [`pyproject.toml`](pyproject.toml) and
[`web/eslint.config.js`](web/eslint.config.js) — both are narrow on purpose and
say in comments which rules are off and why. `ruff check .` and `pytest` are
clean; `npm run lint` reports no errors — only a handful of accepted warnings, all
of them the React Compiler's `set-state-in-effect` and `only-export-components`
advisories.

The **quality** evaluation is a separate harness in
[`evaluation/`](evaluation/README.md) — 12 simulated personas, LLM judges validated
against expert gold grades, and a [technical-robustness suite](evaluation/robustness/README.md)
that probes the live MCP stack. It drives the app through `core.orchestrator`, the
same engine the UI uses, and is never imported by it. Both need the stack running.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Chat says no agents are available | The orchestrator (:9000) is down. `./run.sh status`, then `./run.sh logs orchestrator` |
| Chat answers but never uses your data | A specialist is up but its MCP server is not. `./run.sh status` shows which |
| Garmin tools return auth errors | The session expired — re-run `.venv/bin/python auth/garmin_setup.py` |
| Strava returns 401 | Delete `.tokens/strava.json` and let the OAuth flow run again |
| Login code never arrives | Google/Gmail not connected, or the Gmail API is not enabled in the Cloud project. Run `auth/google_oauth.py`. Check spam. For local testing, `OTP_DEV_ECHO=1` prints the code to the API log |
| Settings only shows Strava/Garmin/Calendar | Expected for non-admins. Log in as `ADMIN_EMAIL` for the full page |
| Everyone logged out after a restart | `AUTH_SECRET` changed. `run.sh` persists one in `.secrets/auth_secret` — don't pass a different one over it |
| Agents behave erratically, loop, or drop tool calls | The model is too weak for multi-call loops. Set `AGENT_LLM_MODEL=kit.gpt-4.1` |
| Port already in use on startup | `./run.sh stop`, then start again |
| The fitness agent has no sources | The vector index is missing. `.venv/bin/python -m scripts.build_fitness_index` |
| MLflow unreachable warnings | Harmless — tracing is best-effort and the agents run untraced. `MLFLOW=0 ./run.sh` silences it |
