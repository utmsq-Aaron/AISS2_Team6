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
python -m agents.route_agent &         # :9004  (→ routes)
python -m core.orchestrator_agent &    # :9000  (coordinates the four via A2A)

# Terminal 2 — the UI
streamlit run app.py              # http://localhost:8501
```

In practice use the launchers, which start MCP servers + the five agents + FastAPI (+ UI) for you: **`./dev_stack.sh`** (React/Vite stack on :5173) or **`./start.sh`** (opens Terminal windows; React UI + Telegram bridge). The chat will not work until the orchestrator (:9000) and at least one specialist are up.

Or via Docker: `docker compose up --build` (one container per MCP server **and** per agent; the single `Dockerfile` is reused — MCP services select their module via the `SERVER` env var, agent services override `command:`). The Streamlit/React app still runs on the host, reaching the containers over published localhost ports.

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
- **Specialists** — `agents/{recovery,load,context,route}_agent.py` (:9001–:9004). Each is a LangGraph ReAct agent over a **ToolHost scoped to its MCP servers** (`core/mcp_langchain.scoped_host` + `build_tools`; scope map in `core/config.AGENT_MCP_SCOPE`): recovery→garmin, load→strava+garmin, context→weather+calendar, route→routes. Tools are still *discovered, never hardcoded* — just narrowed per agent. Each returns its raw MCP calls (FULL results, JSON strings) as a DataPart artifact so the orchestrator can build route maps / charts / the debug trace.
- **Glue** — `core/a2a_client.py` (A2A client used by both the orchestrator's ask-tools and the run() adapter); `core/mcp_langchain.py` (ToolHost→LangChain tool wrapper that records the full result and clips the model's copy); `core/agent_trace.py` (the trace helpers + `build_trace` — the exact UI/chart/route contract, so preserve its keys); `agents/prompts.py` (the old `_SYSTEM` split into per-domain prompts + the orchestrator routing prompt).
- Agents run **non-streaming** (`ainvoke`) for robustness against the KIT gateway; progress is surfaced as A2A status messages, not token streaming. `core/llm.get_chat_model()` builds the LangChain `ChatOpenAI` on the same gateway; **`AGENT_LLM_MODEL`** overrides the model for the agent layer (recommend `kit.gpt-4.1` — `glm-4.7` is flaky for the multi-call loops). Registry: `core/config.A2A_AGENTS` (name→URL, env-overridable like `RECOVERY_A2A_URL=…`); `ORCHESTRATOR_SPECIALISTS` selects which specialists are enabled (unreachable ones degrade gracefully).

## Servers (`servers/*_mcp.py`)

Each is a self-contained native **FastMCP** server over Streamable HTTP — no shared base class, no dispatch indirection; tools call their upstream API directly and return clean JSON. Tool inventory (and ports) is documented in `README.md`. Server-level instructions and the `@mcp.tool()` docstrings are what the model uses to pick tools — **the docstring is the tool's interface**, so write it precisely.

**`servers/telegram_mcp.py` is the one exception — a proxy, not a native server.** The Telegram tools come from the external [`chigwell/telegram-mcp`](https://github.com/chigwell/telegram-mcp), which is stdio-only and pins Python 3.13. Rather than fork its 116 tools, the proxy runs that repo *unmodified* via `uv run` (isolated env, so Telethon never touches the app's deps) and bridges it onto the Streamable-HTTP bus: it holds **one** persistent upstream stdio session (a single Telegram login + cache warm) and re-exposes its tools — discovered live, never hardcoded — via a low-level `mcp` `Server` hosted with `StreamableHTTPSessionManager` (stateless front, persistent back). To `ToolHost` it looks identical to every other server: one URL in `MCP_SERVERS`, creds passed as connection env (forwarded to the subprocess), separate from tool definitions. This is the template for bridging *any* external stdio MCP server into the app.

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

Then add `"example": _url("example", 8106),` to `MCP_SERVERS` in `core/config.py`, start it with `python -m servers.example_mcp`, and the Chat agent can call it immediately.

## UI (`ui/*.py`)

`app.py` is the entry point: PIN gate → sidebar (live connection dots from `MCP_SERVERS`, sport filter, cache-clear refresh) → tabs (Dashboard, Health, Routes, Chat, Sync, Settings), each delegating to its `ui/<tab>.py` `render_*` function. `ui/shared.py` holds the cached `ToolHost` singleton (`get_host`), the `call_tool` wrapper, connection checks, and config validation. UI is bilingual: tab labels and user-facing strings are often German — match the surrounding language of the file you edit.

**3D flythrough subsystem** (`ui/flythrough_3d.py` + `ui/video_renderer.py`): a MapLibre GL cinematic camera animation over an activity's GPS track, with an in-browser WebCodecs MP4 export and a server-side render path (headless Chromium via Playwright). `ui/chat.py` renders flythroughs and inline charts (`ui/viz.py`) from a `trace["actions"]` list (action types `viz` and `flythrough`), in addition to the route map from `trace["route_data"]`. Caveat: the current `FitDashOrchestrator` initializes `trace["actions"]` to `[]` and never populates it, so those inline chat actions are **dormant** — `strava__launch_flythrough` returns an `{"action": "show_flythrough", …}` payload that nothing currently lifts into a trace action. Route maps are fully wired; flythrough today is invoked directly from the Dashboard tab (`ui/dashboard.py` → `show_flythrough`), not via the Chat agent.

`requirements.txt` lists `fastapi`/`uvicorn` and the `core/` docstrings reference a "FastAPI layer", but no API module exists in the tree yet — it's the intended multi-tenant seam, not present code. Don't go looking for it.

## Telegram agent bridge (`telegram_bridge.py`)

A second app entry point besides `app.py`: a long-running **userbot** that exposes the agent over Telegram chat. Each incoming message is forwarded to the *same* `core.orchestrator.FitDashOrchestrator` the Chat tab uses — so the bridge imports `core/`, never `ui/`, and needs no Streamlit. The answer is sent back, and `trace["route_data"]` is delivered three ways: a static PNG via **`core/route_render.py`** (the `staticmap` package — flat image, no browser, unlike the interactive folium maps in `ui/chat.py`); a tappable Google Maps directions link in the photo caption; and a GPX file — the last two built by **`core/route_export.py`** (`google_maps_url` / `route_gpx`, both pure-stdlib). Incoming **voice memos** are transcribed locally by **`core/transcribe.py`** (Whisper, multilingual; auto-detects mlx-whisper on Apple Silicon and falls back to faster-whisper — and in auto mode skips the ffmpeg-dependent backends when `ffmpeg` is absent, since faster-whisper decodes via PyAV) and then handled exactly like a typed message. Per-chat history (a deque keyed by `chat_id`) makes multi-turn conversation stand in for the web UI's interactive widgets — the agent lists options as text and the user replies to pick.

Mind these: the synchronous `orchestrator.run()` is offloaded to a thread (`run_in_executor`) and serialized by one global `asyncio.Lock` (ToolHost is shared and not assumed thread-safe; a personal userbot has trivial concurrency). It reuses `TELEGRAM_SESSION_STRING` by default; the only hazard is running it *while* the `servers/telegram_mcp.py` proxy is also connected on that same session (two live clients on one login key → `AuthKeyDuplicatedError`, which revokes it) — `--login` mints a dedicated `TELEGRAM_BRIDGE_SESSION_STRING` for that case. Defaults: DMs only (`TELEGRAM_BRIDGE_ALLOW_GROUPS`), open to anyone (`TELEGRAM_ALLOWED_USERS` empty). This is the **opposite direction** from the proxy: the proxy gives the *agent* Telegram tools; the bridge gives *Telegram users* the agent.

## Conventions

- Add a new data source as a new MCP server + a config line — do **not** add direct API calls in the UI or special-case a tool by name anywhere.
- Per-server credentials are passed as connection headers via `ToolHost(headers=…)`, never as tool arguments and never into model context.
- Keep `core/` free of Streamlit imports and of any hardcoded tool name.
