# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FitDash — a Streamlit sports-analytics dashboard that unifies Strava + Garmin (plus Weather, Routes, Calendar) behind an agentic chat. Every answer comes from live API data; nothing is cached-summarized or invented. The repo root is this directory (`AISS2_Team6`); it is the git repository, `main` is the default branch.

The authoritative architecture doc is [`docs/mcp-architecture.md`](docs/mcp-architecture.md) (German). `ARCHITECTURE.md` is just a redirect to it.

## Running

There are no build, lint, or test commands — there is no test suite, no linter config, and no packaging. The app is run directly with the system Python (3.11+) after `pip install -r requirements.txt`. The 3D-flythrough **video export** additionally needs a headless Chromium: `playwright install chromium --with-deps` (one-time). The in-browser flythrough itself works without it; only the server-side MP4 render in `ui/video_renderer.py` requires it.

To run, **the MCP servers must be started first** (each is an independent process), then the Streamlit UI:

```bash
# Terminal 1 — one process per MCP server
python -m servers.weather_mcp &   # :8101
python -m servers.routes_mcp &    # :8102
python -m servers.strava_mcp &    # :8103
python -m servers.garmin_mcp &    # :8104
python -m servers.calendar_mcp &  # :8105
python -m servers.telegram_mcp &  # :8106  (optional — proxy server, see below)

# Terminal 1b — the A2A agent layer (LangGraph specialists + orchestrator).
# The chat engine runs HERE now, not in an in-process loop. Specialists first.
python -m agents.recovery_agent &      # :9001  (→ garmin MCP)
python -m agents.load_agent &          # :9002  (→ strava + garmin)
python -m agents.context_agent &       # :9003  (→ weather + calendar)
python -m agents.route_agent &         # :9004  (→ routes + google_maps)
python -m agents.fitness_agent &       # :9005  (→ RAG vector DB, no MCP)
python -m core.orchestrator_agent &    # :9000  (coordinates the five via A2A)

# Terminal 2 — the UI
streamlit run app.py              # http://localhost:8501
```

In practice use the launchers, which start the MLflow tracking server (:5001) + MCP servers + the five agents + FastAPI (+ UI) for you: **`./dev_stack.sh`** (React/Vite stack on :5173) or **`./start.sh`** (opens Terminal windows; React UI + Telegram bridge). The chat will not work until the orchestrator (:9000) and at least one specialist are up. Agent/LLM traces are browsable at http://localhost:5001 (see *Agent layer → Tracing*).

Or via Docker: `./docker-up.sh up --build` (a thin `docker compose` wrapper that first regenerates the port variables from `core/config.py` — see the script header; one container per MCP server **and** per agent; the single `Dockerfile` is reused — MCP services select their module via the `SERVER` env var, agent services override `command:`). The Streamlit/React app still runs on the host, reaching the containers over published localhost ports.

Garmin needs a one-time MFA login before the Health tab / Garmin tools work: `python auth/garmin_setup.py` (after setting `GARMIN_EMAIL`/`GARMIN_PASSWORD`). Strava OAuth runs automatically on first use. Calendar has no setup script — it reads `.tokens/google.json` (single-user dev) or a per-request `Authorization` header (multi-tenant), refreshing via optional `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`. All tokens persist in `.tokens/` (gitignored). Telegram (optional) is an *external* server bridged by a proxy (see Servers): the `chigwell/telegram-mcp` upstream is **vendored** in `external/telegram-mcp` (committed to this repo, minus its own `.git`/`.venv`). Set `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`, and generate `TELEGRAM_SESSION_STRING` once — either in the **Settings tab → Telegram** card (enter API id/hash, then phone-login; `ui/settings.py` `_setup_telegram`, uses Telethon directly) or via `uv run --directory external/telegram-mcp session_string_generator.py` (interactive — headless login is disabled). It needs `uv` on PATH. The sidebar status dot reflects *config presence* (`ui/shared.py` `telegram_connected`), not a live ping.

Config comes from `.env` (copy `.env.example`). Required: `OPENAI_API_KEY`, `OPENAI_BASE_URL` (KIT gateway), `AGENT_MODEL` (e.g. `kit.gpt-4.1`). **Model provider:** `LLM_PROVIDER` switches the whole app between `openai` (KIT / any OpenAI-compatible endpoint, default — `OPENAI_*` + `AGENT_MODEL`/`AGENT_LLM_MODEL`), `openai_official` (official OpenAI at api.openai.com — `OPENAI_OFFICIAL_API_KEY` + `OPENAI_MODEL`), and `gemini` (Google Gemini — `GEMINI_API_KEY` + `GEMINI_MODEL`, a free flash model like `gemini-2.0-flash`). All three are selectable from the React Settings → "OpenAI / LLM" card. Optional integrations: `GARMIN_*`, `ORS_API_KEY`, `CLIENT_ID`/`CLIENT_SECRET` (Strava). All are also editable at runtime in the Settings tab. An optional `APP_PIN` (in `.streamlit/secrets.toml` or env) gates the whole app.

## Architecture — the load-bearing idea

The entire design is **MCP-standard, tool-agnostic**: one uniform client talks to many independent servers, tools are *discovered, never hardcoded*, and auth is separated from tool declarations. Internalize this before touching `core/` — most "where do I add X" questions answer themselves once you do.

The data path is **agent → `core/host.ToolHost` → MCP servers**: nothing calls Strava/Garmin/etc. APIs directly; everything goes through `call_tool("server__tool", args)`. The chat path layers on top: **UI → `FitDashOrchestrator` → (A2A) orchestrator agent → (A2A) specialist agents → `ToolHost` → MCP** — see *Agent layer* below.

- **`core/config.py`** — the registry: a flat `name → MCP URL` dict (`MCP_SERVERS`). Each URL is env-overridable (`WEATHER_MCP_URL=…`). Tool names are namespaced `server__tool`; the separator is `SEP = "__"` (dots aren't legal in OpenAI function names).
- **`core/host.py` — `ToolHost`** — the *single* MCP client for the whole app (UI, orchestrator, any future API). `list_tools()` discovers every tool from every *reachable* server in OpenAI-tool format; an unreachable/unauthorized server is silently **skipped, never fatal**. `call_tool()` splits `server__tool`, routes it, and returns text/JSON — tool errors come back as `{"error": …}` strings, not exceptions. Real impl is async; a sync facade (`_run`, fresh event loop per call) bridges it for the synchronous Streamlit/thread code.
- **`core/llm.py`** — the vendor-neutral LLM seam. The only place a chat client is constructed and the model resolved (both from env). Deliberately imports **no Streamlit** so `core/` runs standalone. Swapping provider/model is a config change, not code: `LLM_PROVIDER` (`openai` = KIT/compat | `openai_official` = api.openai.com | `gemini`) selects, for `get_chat_model()`, between `ChatOpenAI` (KIT gateway or official OpenAI, with per-provider key/base/model) and native Gemini (`ChatGoogleGenerativeAI`); the raw OpenAI-SDK path (`get_llm_client()`, used by the chart service) follows the same switch, reaching Gemini via its OpenAI-compatible endpoint. The seam re-reads `.env` per call so Settings-UI changes apply without restarting the agent processes.
- **`core/orchestrator.py` — `FitDashOrchestrator`** — **no longer a tool-use loop**; it is now a thin **A2A client adapter** to the orchestrator agent (:9000). It preserves the exact public contract every caller depends on — `run(user_input, history, progress_cb, text_cb) -> (answer, trace)` and `refresh_tools()` — so the Streamlit Chat tab, the FastAPI SSE endpoint (`api/`) and `telegram_bridge.py` are unchanged. It flattens history into one A2A message, relays A2A status updates → `progress_cb`, and returns the `trace` the orchestrator assembled. Runs are logged to `.logs/agent_interactions.jsonl`.

`core/` is Streamlit-free and vendor-neutral by design — keep it that way. UI concerns belong in `ui/`.

## Agent layer — LangGraph specialists over A2A (`core/orchestrator_agent.py`, `agents/`)

The chat engine is a multi-agent system. Each agent is its own **A2A server** (official `a2a-sdk`, the pydantic 0.3.x API — chosen for tutorial parity) with an Agent Card at `/.well-known/agent-card.json`:

- **Orchestrator** — `core/orchestrator_agent.py` (:9000). A LangGraph agent (`langchain.agents.create_agent`) whose only tools are `ask_<specialist>` — each performs an A2A call to a specialist. It decomposes the request, delegates (in parallel when the model emits multiple tool calls), collects each specialist's DataPart artifact, and assembles the `trace` via `core/agent_trace.build_trace`. It has **no MCP access** of its own.
- **Specialists** — `agents/{recovery,load,context,route}_agent.py` (:9001–:9004). Each is a LangGraph ReAct agent over a **ToolHost scoped to its MCP servers** (`core/mcp_langchain.scoped_host` + `build_tools`; scope map in `core/config.AGENT_MCP_SCOPE`): recovery→garmin, load→strava+garmin, context→weather+calendar, route→routes+google_maps. Tools are still *discovered, never hardcoded* — just narrowed per agent. Each returns its raw MCP calls (FULL results, JSON strings) as a DataPart artifact so the orchestrator can build route maps / charts / the debug trace.
- **Fitness Expert** — `agents/fitness_agent.py` (:9005). The one specialist with **no MCP server**: it answers training / technique / exercise-science questions from a local **vector DB of public-domain fitness books** via RAG (`core/fitness_rag.py`; built by `scripts/build_fitness_index.py`; executor `agents/_rag_executor.py`). Its single `search_fitness_literature` tool is recorded into the same artifact shape, so it flows into the trace like any MCP call. Embeddings use a small **local** model (`sentence-transformers`, all-MiniLM-L6-v2) — no embedding API. See [`docs/fitness-rag.md`](docs/fitness-rag.md).
- **Glue** — `core/a2a_client.py` (A2A client used by both the orchestrator's ask-tools and the run() adapter); `core/mcp_langchain.py` (ToolHost→LangChain tool wrapper that records the full result and clips the model's copy); `core/agent_trace.py` (the trace helpers + `build_trace` — the exact UI/chart/route contract, so preserve its keys); `agents/prompts.py` (the old `_SYSTEM` split into per-domain prompts + the orchestrator routing prompt).
- Agents run **non-streaming** (`ainvoke`) for robustness against the KIT gateway; progress is surfaced as A2A status messages, not token streaming. `core/llm.get_chat_model()` builds the LangChain `ChatOpenAI` on the same gateway; **`AGENT_LLM_MODEL`** overrides the model for the agent layer (recommend `kit.gpt-4.1` — `glm-4.7` is flaky for the multi-call loops). Registry: `core/config.A2A_AGENTS` (name→URL, env-overridable like `RECOVERY_A2A_URL=…`); `ORCHESTRATOR_SPECIALISTS` selects which specialists are enabled (unreachable ones degrade gracefully).

### The coach — persona, goals, proactivity, deep analysis

On top of the multi-agent engine, the assistant behaves as a **personal coach** — more precisely, a **buddy-coach**: warm and casual like a supportive training partner (not a clinical assistant or a drill sergeant), still concise, data-grounded and willing to hold the user accountable, stays anchored to a persistent goal across every chat, and is **proactive** — it schedules its own future check-ins, including a daily one.

- **Persona + name + goal injection.** The coach voice lives in `agents/prompts.py` `_base()` (shared by every agent) as a BUDDY-COACH block; the user-facing challenge concentrates in the orchestrator's SYNTHESIS, and the "text like a friend, 1–3 sentences" constraint lives only in the orchestrator's PROACTIVE section + the check-in note text (kept OUT of `_base()` so specialist data handoffs and deep reports stay precise). The user's name (`core/user_profile.py`, set during onboarding) and all of a user's active goals are injected into **every** turn (web + Telegram) via `core/user_memory.context_block()` (name first, then `goal_block()`), so voice + goal feel consistent across chats. There is **no separate injection site** — extending `context_block` covers both callers.
- **Multiple, freeform goals + agent-authored dashboard panels.** `core/goal_store.py` — goals are just TEXT (sport-specific goals are common), any number per user, at `data/user_memory/<slug>/goals.json`, authored by BOTH a Dashboard/Settings text box (`api/routers/goals.py`, `source="user"`) AND the coach in chat (`add_goal`/`update_goal` tools, `source="coach"`). Each goal gets a dashboard **panel whose content the agent decides** — not a hardcoded metric/ring: a structured spec (`headline`, a health `status`, 2-4 `tiles`, an optional `progress`/`chart`) plus a free markdown `note`, normalized server-side (`goal_store.normalize_panel`) so the coach's inline authoring (`set_goal_panel` tool) and the background builder can't diverge. Building/refreshing a panel is a bounded background agent job (`core/goal_panel.py`, mirrors `deep_analysis.py` — its own `create_agent`, never `orchestrator.run()`, so panel builds don't pollute conversation memory) with three triggers: the coach's `add_goal` spawns it directly (orchestrator process is durable); a form-created/refreshed goal is **enqueued** (`core/goal_build_queue.py`) since FastAPI's `--reload` can't host a durable thread, drained by a fast bridge loop (`telegram_bridge._goal_build_loop`, ~5s) so it resolves in seconds; and an hourly staleness sweep (inside the existing calendar scan) re-enqueues any panel older than `GOAL_PANEL_STALE_HOURS` (~20h) — the "daily-ish" auto-refresh. `goals.json` is written by three processes, so every mutation goes through the shared cross-process lock in `core/jsonstore.py` (also used by `core/chat_store.py`). The React Dashboard renders one `<GoalPanel>` per active goal (a single generic component driven entirely by the agent's spec) plus a freeform `<AddGoalInput>`; Settings adds an archive/restore/delete manager.
- **Proactivity (durable, cross-chat, dedup'd, adaptive).** The coach schedules its own re-activation with the `schedule_followup` tool → `core/schedule_store.py` (`data/schedules.json` + a `data/schedule_fired.json` fire-once log keyed by `email|reason_key|minute-slot`; write-time dedup by `reason_key`; recurrence re-arm). The **durable poll loop lives in `telegram_bridge.py`** (`_scheduler_loop`, the only always-on process — FastAPI runs `--reload` and can't host it): each tick it fires due wake-ups (composed live by re-running the note through the orchestrator, **coalesced** so the user is pinged once), drains the deep-report outbox, and hourly auto-schedules pre/post-calendar-event nudges + a **daily** buddy check-in (`daily_checkin`, replacing the old weekly one — migrated in place via `schedule_store.cancel`). The daily check-in is **adaptive**: `core/chat_store.last_user_message_ts()` (covers Telegram too, via the mirrored Coach chat) lets `_fire_due` silently skip it (re-arming for tomorrow, no delivery) when the user already chatted within `DAILY_CHECKIN_SKIP_HOURS`; on Mondays it also weaves in a goal-progress review, composed fresh each time (never persisted back to the stored note). `dev_stack.sh` now starts the bridge; **proactivity is paused when the bridge is down.**
- **Delivery + the pinned Coach chat.** `core/delivery.py` `deliver_to_user` ALWAYS mirrors to the web-visible **Coach chat** (`core/coach_mirror.py` → a reserved `"coach"` chat id, pinned + specially marked, id-gate widened in `core/chat_store.py`) and ALSO pushes to Telegram when the account is linked (`core/telegram_link.get_telegram_id`, the new reverse lookup). The bridge also mirrors every Telegram DM turn into that same Coach chat, so the Telegram conversation shows up pinned at the top of the web chat list.
- **Truly agentic (triage → deep analysis).** The orchestrator triages each request: STRAIGHTFORWARD → today's fast single-round answer; DEEP → the `start_deep_analysis` tool (fire-and-return) records a job (`core/deep_jobs.py`) and spawns `core/deep_analysis.py` on a daemon thread — a bounded multi-round worker (raised `recursion_limit`, wall-clock `DEEP_JOB_TIMEOUT`, concurrency semaphore) that reuses the `ask_*` tools, then writes its report to `core/proactive_outbox.py` for the bridge to deliver. The fast path is unchanged; a `background_job` trace action tells the UI to show an "I'll report back" card.
- **Onboarding + profile.** `core/user_profile.py` — `data/user_memory/<slug>/profile.json` (name, `onboarding_complete`, an optional `avatar.<ext>` file beside it). A first-login user sees a skippable 4-step wizard (`web/src/components/onboarding/OnboardingWizard.tsx`): name → photo → goals (reuses `AddGoalInput`) → connect services (compact Strava/Garmin/Google cards reusing the same flows as Settings). `GET /api/profile` never 404s; `App.tsx` gates on `onboarding_complete` (falling through to the shell on a query *error*, never locking a user out). The avatar can't use a plain `<img src>` (no cookie auth) — the frontend fetches it as an authenticated blob and renders an object URL.
- **Feedback button.** A red "Report a problem" button (`FeedbackButton.tsx`, a `variant` prop) — an inline button in the `Header.tsx` right-side cluster for the main app, and a floating bottom-right pill in the onboarding wizard (which has no header); it deliberately does NOT float over the main app so it can't overlap the Chat composer) — lets a tester report a problem; `api/feedback_service.py` snapshots log tails (redacted), MLflow trace *references* (never the raw `mlflow.db`), and the reporting user's own state (soul, goals, schedules, recent chats — never another user's, never `.tokens`/`.secrets`) into one JSON bundle under `data/feedback/`, then best-effort emails a short admin notification (text + bundle id only, never the bundle). Admin-only list/get endpoints. The MLflow-refs capture runs in a **daemon thread with a hard wall-clock timeout** — mlflow's REST client retries with backoff for minutes against an unreachable tracking server, which must never stall the button (see also the `core/tracing.py` fix below).

### Tracing (MLflow) — `core/tracing.py`

Every agent run is traced to an **MLflow** tracking server (UI on `:5001`, started by `./dev_stack.sh` / `./start.sh`). `core/tracing.py` is the seam: each agent process calls `setup_tracing(name)` once (inside `run_agent_server`, so all five are covered; the API process calls it in `api/main.py`), which points the process at the server and enables `mlflow.langchain.autolog()` (+ `mlflow.openai.autolog()` for the chart service). Each agent then wraps its `ainvoke` in `trace_span(name, service=…, role=…, question=…)`, so the autologged LLM + tool spans nest under one labelled root span and the tags export **with** the trace (no post-hoc tagging that would race MLflow's async export). Result: one tagged trace per agent run in the shared `fitdash` experiment — filterable by `service` (orchestrator / recovery / load / context / route), with the LLM call and each `ask_*` / MCP tool call as child spans. Everything is **best-effort** — a missing `mlflow` or an unreachable server logs once and is ignored; the agents run untraced. **This is enforced, not just hoped for:** `setup_tracing`'s `mlflow.set_experiment(...)` call is bounded by `_set_experiment_bounded()` (a daemon thread + a 5 s hard timeout) — without it, an unreachable tracking server makes mlflow's REST client retry with backoff for *minutes*, silently hanging the whole process's startup (this actually happened and was fixed; the daemon thread is deliberate — `ThreadPoolExecutor`'s workers are non-daemon and get joined at interpreter shutdown even after `shutdown(wait=False)`, so a plain `threading.Thread(daemon=True)` is the only way to make an abandoned retry-storm truly non-blocking). Config is live from `.env` like `core/llm`: `MLFLOW_TRACKING_URI` (default `http://127.0.0.1:5001`), `MLFLOW_EXPERIMENT` (default `fitdash`), `MLFLOW_TRACING=0` to disable. Store is a local `mlflow.db` (sqlite, gitignored). Keep `core/tracing.py`'s `mlflow` imports lazy/guarded — `core/` must not hard-depend on the tracking server being up.

## Servers (`servers/*_mcp.py`)

Each is a self-contained native **FastMCP** server over Streamable HTTP — no shared base class, no dispatch indirection; tools call their upstream API directly and return clean JSON. Tool inventory (and ports) is documented in `README.md`. Server-level instructions and the `@mcp.tool()` docstrings are what the model uses to pick tools — **the docstring is the tool's interface**, so write it precisely.

**`servers/telegram_mcp.py` and `servers/google_maps_mcp.py` are the two proxy exceptions — not native servers.** The Telegram tools come from the external [`chigwell/telegram-mcp`](https://github.com/chigwell/telegram-mcp), which is stdio-only and pins Python 3.13. Rather than fork its 116 tools, the proxy runs that repo *unmodified* via `uv run` (isolated env, so Telethon never touches the app's deps) and bridges it onto the Streamable-HTTP bus: it holds **one** persistent upstream stdio session (a single Telegram login + cache warm) and re-exposes its tools — discovered live, never hardcoded — via a low-level `mcp` `Server` hosted with `StreamableHTTPSessionManager` (stateless front, persistent back). To `ToolHost` it looks identical to every other server: one URL in `MCP_SERVERS`, creds passed as connection env (forwarded to the subprocess), separate from tool definitions. This is the template for bridging *any* external stdio MCP server into the app.

**`servers/google_maps_mcp.py`** is a native FastMCP server (it *used* to proxy the upstream `@modelcontextprotocol/server-google-maps` npm package, but that is archived/unsupported and called Google's legacy APIs, which require billing). It now calls the current APIs directly — Places API (New), Geocoding API v4, Routes API — all of which work with a billing-free **Maps Demo Key** (`GOOGLE_MAPS_API_KEY`; demo keys serve no user-generated content, so the field masks retry without rating fields on refusal). Tool names were kept identical to the old proxy (`maps_search_places`, `maps_place_details`, `maps_geocode`, `maps_reverse_geocode`, `maps_directions`) so the route agent's prompt kept working; `maps_distance_matrix` and `maps_elevation` were dropped (ORS covers elevation via `routes__get_elevation_profile`). Registered in `MCP_SERVERS` (port 8108), scoped to the `route` specialist. `routes__geocode` (in `servers/routes_mcp.py`) calls the same Geocoding v4 endpoint.

### Adding a server (the whole point of the design)

One new file + one registry line. No change to the host, orchestrator, or UI:

```python
# servers/example_mcp.py
mcp = FastMCP("example", host="127.0.0.1",
              port=int(os.getenv("EXAMPLE_MCP_PORT", "8106")), stateless_http=True)

@mcp.tool()
def my_tool(param: str) -> dict:
    """Clear description — the model picks this tool based solely on this text."""
    return {"result": param}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

Then add `"example": 8106,` to `MCP_PORTS` in `core/config.py` (`MCP_SERVERS` derives from it), start it with `python -m servers.example_mcp`, and the Chat agent can call it immediately. `MCP_PORTS`/`AGENT_PORTS` in `core/config.py` are the **single source of truth for every port**: `ports.sh` (sourced by all launcher scripts), `web/vite.config.ts` (dev proxy target) and `docker-up.sh` (compose `${VAR}` interpolation) all read them live via `scripts/export_ports.py` — never hardcode a port anywhere else.

## UI (`ui/*.py`)

`app.py` is the entry point: PIN gate → sidebar (live connection dots from `MCP_SERVERS`, sport filter, cache-clear refresh) → tabs (Dashboard, Health, Routes, Chat, Sync, Settings), each delegating to its `ui/<tab>.py` `render_*` function. `ui/shared.py` holds the cached `ToolHost` singleton (`get_host`), the `call_tool` wrapper, connection checks, and config validation. UI is bilingual: tab labels and user-facing strings are often German — match the surrounding language of the file you edit.

**3D flythrough subsystem** (`ui/flythrough_3d.py` + `ui/video_renderer.py`): a MapLibre GL cinematic camera animation over an activity's GPS track, with an in-browser WebCodecs MP4 export and a server-side render path (headless Chromium via Playwright). `ui/chat.py` renders flythroughs and inline charts (`ui/viz.py`) from a `trace["actions"]` list (action types `viz` and `flythrough`), in addition to the route map from `trace["route_data"]`. Caveat: the current `FitDashOrchestrator` initializes `trace["actions"]` to `[]` and never populates it, so those inline chat actions are **dormant** — `strava__launch_flythrough` returns an `{"action": "show_flythrough", …}` payload that nothing currently lifts into a trace action. Route maps are fully wired; flythrough today is invoked directly from the Dashboard tab (`ui/dashboard.py` → `show_flythrough`), not via the Chat agent. **React app:** the Streamlit-free engine (the self-contained MapLibre + WebCodecs page + track prep) now lives in [`core/flythrough_html.py`](core/flythrough_html.py); FastAPI serves it at `GET /api/flythrough/{activity_id}` ([`api/routers/flythrough.py`](api/routers/flythrough.py)) and the React Dashboard renders it in an `<iframe srcdoc>` ([`web/src/components/FlythroughModal.tsx`](web/src/components/FlythroughModal.tsx)) with an in-page **Export** button that encodes the MP4 client-side — no Playwright. `ui/flythrough_3d.py` now imports the engine from `core/` (single source of truth; it still re-exports `_build_html` for `ui/video_renderer.py`).

The FastAPI seam lives in `api/` (`api/main.py`, `api/routers/`, `api/auth.py`, `api/settings_service.py`, `api/sync_service.py`, `api/chart_service.py`, `api/email_service.py`, `api/connections.py`, `api/deps.py`) — it fronts `core/` for both the React app (`web/`, via the Node BFF in `server/`) and the Streamlit app, and is the multi-tenant seam (per-request `Authorization` header instead of the single-user `.tokens/` files).

## Telegram agent bridge (`telegram_bridge.py`)

A second app entry point besides `app.py`: a long-running **userbot** that exposes the agent over Telegram chat. Each incoming message is forwarded to the *same* `core.orchestrator.FitDashOrchestrator` the Chat tab uses — so the bridge imports `core/`, never `ui/`, and needs no Streamlit. The answer is sent back, and `trace["route_data"]` is delivered three ways: a static PNG via **`core/route_render.py`** (the `staticmap` package — flat image, no browser, unlike the interactive folium maps in `ui/chat.py`); a tappable Google Maps directions link in the photo caption; and a GPX file — the last two built by **`core/route_export.py`** (`google_maps_url` / `route_gpx`, both pure-stdlib). Incoming **voice memos** are transcribed locally by **`core/transcribe.py`** (Whisper, multilingual; auto-detects mlx-whisper on Apple Silicon and falls back to faster-whisper — and in auto mode skips the ffmpeg-dependent backends when `ffmpeg` is absent, since faster-whisper decodes via PyAV) and then handled exactly like a typed message. Per-chat history (a deque keyed by `chat_id`) makes multi-turn conversation stand in for the web UI's interactive widgets — the agent lists options as text and the user replies to pick.

Mind these: the synchronous `orchestrator.run()` is offloaded to a thread (`run_in_executor`) and serialized by one global `asyncio.Lock` (ToolHost is shared and not assumed thread-safe; a personal userbot has trivial concurrency). It reuses `TELEGRAM_SESSION_STRING` by default; the only hazard is running it *while* the `servers/telegram_mcp.py` proxy is also connected on that same session (two live clients on one login key → `AuthKeyDuplicatedError`, which revokes it) — `--login` mints a dedicated `TELEGRAM_BRIDGE_SESSION_STRING` for that case. Defaults: DMs only (`TELEGRAM_BRIDGE_ALLOW_GROUPS`), open to anyone (`TELEGRAM_ALLOWED_USERS` empty). This is the **opposite direction** from the proxy: the proxy gives the *agent* Telegram tools; the bridge gives *Telegram users* the agent.

## Conventions

- Add a new data source as a new MCP server + a config line — do **not** add direct API calls in the UI or special-case a tool by name anywhere.
- Per-server credentials are passed as connection headers via `ToolHost(headers=…)`, never as tool arguments and never into model context.
- Keep `core/` free of Streamlit imports and of any hardcoded tool name.

## Seminar Paper (`aiss2026/`)

The seminar paper lives in `aiss2026/` and compiles with MiKTeX. Build: `pdflatex → bibtex → pdflatex → pdflatex → pdflatex` (4 passes after bibtex for cross-refs), or just run `aiss2026/build.bat`. The product is called **Training Copilot** everywhere (never "FitDash" in the paper).

**`build.bat` is shared/committed and machine-agnostic on purpose** — it resolves the MiKTeX `bin` directory itself (PATH → `MIKTEX_BIN` env var → a few common install locations), it does not hardcode any one contributor's install path. If it can't find `pdflatex`/`bibtex` on a given machine, the fix is to `set MIKTEX_BIN=...` in that shell (or add MiKTeX's `bin\x64` to PATH) — **never** hardcode that machine's path into `build.bat` itself, since that would break the build for every other contributor the next time they pull. If a contributor needs something genuinely machine-specific beyond what `MIKTEX_BIN` covers, add a new untracked script (e.g. `build.local.bat`) rather than editing the shared one.

### Citation Knowledge Base — MANDATORY

**The four-artifact invariant.** Every cite key used in the paper (`\cite{key}`) MUST have, all sharing the identical basename `{key}`:

1. a **bib entry** `@type{key, …}` in `aiss2026/references.bib`;
2. a **KB file** `aiss2026/citation_kb/{key}.txt` (claim + source-verified);
3. a **real source PDF** `aiss2026/sources/{key}.pdf` — of the actual paper or the actual website. **Not a text stub, not a screenshot, not a link-only placeholder.** This is non-negotiable: a citation with no PDF is not allowed to exist. Papers → download the PDF (e.g. arXiv `https://arxiv.org/pdf/<id>`). Websites/press releases/product pages → print to PDF with Playwright headless Chromium (see the capture script pattern used for the competitor sources: `goto` → scroll to load lazy content → `page.pdf(...)`). Paywalled papers we cannot access → find an open-access alternative that supports the same claim; **never cite what we have not read.**
4. an **entry in `aiss2026/REFERENCES.md`** (the Zotero import list) — add it to both the cite-key mapping table and the full-reference list, bump the `Total: N references` header, and append it to the "New References" section at the bottom with a DOI/URL link. Also add a row to `aiss2026/citation_map.md` mapping the key → the exact sentence/claim.

Do these BEFORE (or in the same change as) adding the `\cite{}`. After adding, **verify the PDF actually supports the claim** by extracting its text (PyMuPDF `fitz` is available in the `aiss2026` conda env, or `pypdf`) and quoting the supporting passage into the KB file's `SOURCE CONFIRMS` block. If the source does not support a specific claim, do not force-cite it — drop the citation instead (a real example: `strava_year_2024` was removed because its promotional page supported no specific number).

**When you touch a citation:**
- **Add**: create all four artifacts above; verify against the PDF.
- **Change a claim**: update the KB file's claim text and re-verify against the source; update `citation_map.md`.
- **Rename a key** (e.g. you corrected the year): rename all four in lockstep — the bib key, `citation_kb/{key}.txt`, `sources/{key}.pdf`, and every `\cite{key}` — plus the `REFERENCES.md`/`citation_map.md` rows. A key mismatch across these is a bug.
- **Remove**: delete the bib entry, the KB file, **and the source PDF**; drop the `REFERENCES.md`/`citation_map.md` rows and decrement the count; ensure no `\cite{key}` remains.

**Always verify the build after citation changes.** Run `aiss2026/build.bat`, then check the log for `undefined` (`grep -i "undefined" aiss2026/build/thesis.log`) and the PDF for the literal `??` marker (an unresolved `\ref`/`\cite`). Both must be zero. Gotcha that will waste your time: `*.aux`/`*.bbl`/chapter `*.aux` are **gitignored build artifacts**, but stray *committed* copies in the source root (`aiss2026/*.aux`, `aiss2026/thesis.bbl`, `aiss2026/chapters/*.aux`) will **shadow** the fresh ones `build.bat` writes under `aiss2026/build/`, producing phantom "undefined citation/reference" warnings and `??` in the PDF even though the bbl is correct. If you see that, delete the stray root-level `*.aux`/`*.bbl` and rebuild — never commit them. New `\label`s (e.g. a new table) also need a warm rebuild: the label lands in the aux one pass late, so run `build.bat` a second time, and add `\FloatBarrier` after a new float if it refuses to settle.

**Pending/unbuilt content.** Describe only what the code actually does — verify features against the source before writing them in present tense (grep the repo; a feature with no code is *planned*, not shipped, and must be framed that way). For results that are genuinely outstanding (e.g. the evaluation run), ship explicit `TBD` placeholder tables and describe the methodology fully, rather than inventing numbers.

KB file format:
```
Citation: {key}
Cited N time(s)
============================================================
Chapter: {chapter}, Line: {line}
Claim: {the sentence from our paper, with [CITE] replacing \cite{}}
============================================================
VERIFICATION STATUS: CONFIRMED | ISSUE: {description}
SOURCE CONFIRMS: {actual text from the source supporting the claim}
Verified: {YYYY-MM-DD}
```
