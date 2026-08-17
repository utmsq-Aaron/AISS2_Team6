# Training Copilot — MCP architecture

**Purpose of this document:** describe the current architecture as it actually stands in the code, why it follows the Anthropic/MCP standard, and how it extends to **external MCP servers**.

> This is the authoritative *what/how*, and it governs every new server. The subsystems
> built on top of this foundation — the coach, proactivity, the flythrough, the
> Garmin→Strava sync — are described in [`CLAUDE.md`](../CLAUDE.md); the training maths and
> its sources in [`trainingsregeln.md`](trainingsregeln.md).

---

## 1. Design principles (the Anthropic/MCP standard)

The architecture deliberately follows the model Anthropic describes for MCP hosts: **one** uniform client talks to **many** independent servers, tools are **discovered rather than wired in**, and **auth is separated from the tool declaration**.

| Principle | How the code implements it |
|---|---|
| **Tool-agnostic** — no code knows a tool by name | Each specialist in `agents/` discovers its tools via `list_tools()` (narrowed to its scope) and decides for itself what to call. |
| **One call path** — our servers = external servers | `core/host.ToolHost.call_tool()` / `list_tools()` — the *only* tool surface for the agents, the API and the bridge. |
| **Servers are standalone services** | `servers/*_mcp.py`: native FastMCP servers over Streamable HTTP, each its own process, port and container. |
| **Discovery, not hardcoding** | Tools come from the servers; an unreachable server is skipped, never hardcoded around. |
| **Namespacing** | Tool names are `server__tool` (safe as OpenAI function names; separator `SEP = "__"`). |
| **Auth separate from the declaration (the vault pattern)** | Credentials are **connection headers** per server — never a tool argument, never in model context. |
| **Vendor-neutral** | `core/llm.py`: provider and model come from config/env; switching provider is a config change, not code. |

---

## 1a. The agent layer — LangGraph + A2A

The chat engine is a **multi-agent system** built on **LangGraph** and the **A2A protocol** (the official `a2a-sdk`, pydantic/tutorial API). The MCP layer and the principles in §1 are unchanged — the agents are simply a new tier **above** `ToolHost`.

- **Orchestrator agent** (`core/orchestrator_agent.py`, A2A server `:9000`): a LangGraph agent (`langchain.agents.create_agent`) whose only tools are `ask_<specialist>` — each call is an A2A request to a specialist. It decomposes the request, delegates (in parallel when the model emits several tool calls), collects the specialists' DataPart artifacts and assembles the `trace` via `core/agent_trace.build_trace`. It has **no** MCP access of its own.
- **Specialists** (`agents/{recovery,load,context,route,fitness,coach}_agent.py`, `:9001`–`:9006`): each a LangGraph ReAct agent over a **ToolHost narrowed to its own MCP servers** (`core/mcp_langchain.scoped_host`; scope map in `core/config.AGENT_MCP_SCOPE`): recovery→garmin, load→strava+garmin+flythrough, context→weather+calendar, route→routes+google_maps, coach→athlete+strava+garmin, fitness→no MCP at all (a local RAG vector index). Tools are still **discovered, never hardcoded** — only narrowed per agent. Each returns its raw MCP results (complete, as JSON strings) as a DataPart artifact, so the orchestrator can build maps, charts and the trace.
- **`core/orchestrator.py`** is a thin **A2A client adapter** to the orchestrator agent. It preserves the public contract `run()/refresh_tools()`, which is why the FastAPI SSE layer and the Telegram bridge needed no changes.
- **Registry and operation**: `core/config.A2A_AGENTS` (name → URL, env-overridable as `RECOVERY_A2A_URL=…`); every agent is its own process, port and container, with an Agent Card at `/.well-known/agent-card.json`. Model override for the agent layer: `AGENT_LLM_MODEL` (recommended `kit.gpt-4.1`; `glm-4.7` is unreliable for the multi-call loops). Agents run **non-streaming** (`ainvoke`); progress arrives as A2A status updates, not as a token stream.

The data path is still **agent → `ToolHost` → MCP server**. The chat path is **React UI → FastAPI → `FitDashOrchestrator` → (A2A) orchestrator `:9000` → (A2A) specialists `:9001`–`:9006` → `ToolHost` → MCP**.

---

## 2. Components

```
        ┌───────────────────── Frontends ───────────────────────┐
        │  React SPA (web/) → Node BFF (server/)  ·  Telegram   │
        │  bridge  ·  tests / CLI                               │
        └───────────────────────┬───────────────────────────────┘
                                │  HTTP  (api/ — the FastAPI seam)
        ┌───────────────────────▼───────────────────────────────┐
        │  core/  — UI-framework-free, vendor-neutral           │
        │                                                       │
        │  orchestrator.py       A2A client adapter (run/trace)  │
        │  orchestrator_agent.py LangGraph orchestrator  :9000   │
        │  llm.py                LLM seam (provider/config)      │
        │  host.py               ToolHost  list_tools/call_tool  │
        │  config.py             registry: name → MCP/A2A URL    │
        └───────────────────────┬───────────────────────────────┘
                                │  A2A (Agent Cards, JSON-RPC)
        ┌───────────────────────▼───────────────────────────────┐
        │  agents/ — 6 specialists  :9001–:9006                 │
        │  recovery · load · context · route · fitness · coach   │
        └───────────────────────┬───────────────────────────────┘
                                │  uniform MCP client (Streamable HTTP)
        ┌───────────────────────┼───────────────────────────────┐
        ▼                       ▼                               ▼
  servers/*_mcp.py        servers/telegram_mcp.py        external MCP servers
  (8 native servers)      (proxy to a stdio server)      (user-added, same treatment)
```

### `core/config.py` — the registry
A declarative `name → URL` table. Our own servers and external ones have the same shape; the URL is the only difference. **Adding a server is one line** (or one env variable). Every URL is env-overridable: `WEATHER_MCP_URL=http://weather-mcp:8101/mcp` — as used in docker-compose, where the service name is the host.

```python
# core/config.py — MCP_PORTS is the single numeric source; MCP_SERVERS derives from it.
MCP_PORTS: dict[str, int] = {
    "weather": 8101, "routes": 8102, "strava": 8103, "garmin": 8104,
    "calendar": 8105, "telegram": 8106, "flythrough": 8107,
    "google_maps": 8108, "athlete": 8109,
}

MCP_SERVERS = {name: _url(name) for name in MCP_PORTS}   # _url reads <NAME>_MCP_URL
```

The same table feeds `ports.sh`, `web/vite.config.ts` and `docker-compose.yml` — all three read it live via `scripts/export_ports.py` instead of keeping their own copies of the numbers.

### `core/host.py` — `ToolHost`
The **only** MCP client in the app. One uniform code path for every tool, whichever server provides it:

- `alist_tools()` / `list_tools()` — discovers every tool of every **reachable** server in OpenAI tool format; names are namespaced `server__tool`. A server that is down, unauthorised or unreachable is **skipped** — it never breaks the others.
- `acall_tool(name, args)` / `call_tool(...)` — splits `server__tool`, routes to that server, returns text or JSON; tool errors come back as `{"error": …}` rather than as exceptions.
- **Async core, sync facade:** the real implementation is async (`mcp.client`); `_run()` bridges it for synchronous callers (agents, bridge, tests) with a fresh event loop per call, which is safe inside thread-pool workers too.
- **Per-server auth:** `headers={"calendar": {"Authorization": "Bearer …"}}` is passed as a connection header — separate from the tool declaration, never in tool context. `default_host` uses the global servers; **per-user hosts** are constructed explicitly with additional servers and headers.

### `core/llm.py` — the LLM seam
The single place that builds the chat client and resolves the model, both from env (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AGENT_MODEL`). Today that is an OpenAI-compatible endpoint (the KIT gateway). It deliberately imports **no** UI framework, so the core runs standalone — CLI, API, tests, or a separate service.

### `core/orchestrator.py` — the A2A adapter
Not a tool-use loop. The single native loop that once replaced the old four-agent pipeline is itself gone; the engine is the LangGraph + A2A system described in §1a, and this class is a thin client to it:

1. Flatten the conversation history into one A2A message and send it to the orchestrator agent on `:9000`.
2. Relay the agent's A2A status updates to `progress_cb`, so the UI keeps showing progress even though the agents run non-streaming.
3. Return `(answer, trace)` — the trace is assembled by the orchestrator agent (`core/agent_trace.build_trace`) and merely passed through here. Runs are appended to `.logs/agent_interactions.jsonl`.

The public contract (`run()`, `refresh_tools()`) is unchanged from the loop era, which is precisely why the API seam and the Telegram bridge did not have to be touched when the engine was replaced.

---

## 3. Adding your own MCP server (the `*_mcp.py` pattern)

Each of our servers is a single self-contained file — no `BaseMCPServer`, no dispatch indirection, no registry class. Use `servers/weather_mcp.py` as the template.

```python
# servers/example_mcp.py
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "example",
    instructions="One line on what this server can do.",
    host=os.getenv("EXAMPLE_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("EXAMPLE_MCP_PORT", "8110")),
    stateless_http=True,
)

@mcp.tool()
def do_something(value: str) -> dict:
    """A crisp, prescriptive description — the model picks this tool from this text
    alone. Say WHEN to call it and what the arguments mean."""
    return {"echo": value}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

Then **one line** in `core/config.py`:

```python
"example": 8110,   # added to MCP_PORTS; MCP_SERVERS derives the URL
```

Start it with `python -m servers.example_mcp`. That is all — `ToolHost` discovers the tools on the next `list_tools()` and the agents can call them immediately. **No** code in the host, the orchestrator or the UI names the new tool.

**Conventions** (see weather/routes/calendar):
- Keep tools **read-only** where possible and return dicts (FastMCP serialises them as JSON text).
- Return errors as `{"error": "…"}` instead of raising.
- **Never take auth as a tool argument.** Read the per-request token from the connection's `Authorization` header (see `servers/calendar_mcp.py::_bearer_from_request`), or from a token file in single-user development.
- Request minimal scopes (calendar uses only `calendar.readonly`).

---

## 4. Attaching external MCP servers (the extension point)

This is the decisive payoff of the standardisation: **an external, user-added server is nothing special to the host** — like our own, it is just another Streamable-HTTP endpoint with optional auth headers.

```python
from core.host import ToolHost
from core.config import MCP_SERVERS

# A per-user host: the global built-in servers plus the external ones this user added
user_host = ToolHost(
    servers={**MCP_SERVERS, "notion": "https://mcp.example.com/notion/mcp"},
    headers={"notion": {"Authorization": f"Bearer {user_token}"}},
)
```

`FitDashOrchestrator` takes a host in its constructor (`FitDashOrchestrator(host=user_host)`) — same engine, same tool surface, and the user gains the external tools without a single line in the core knowing that server exists. In multi-tenant operation, `servers`/`headers` are filled per user from a config/DB or a secret vault instead of from the global default.

> **Scope.** What is shown above is the *mechanism*, and it is where this design stops. Third-party servers are outside the trust boundary the current implementation establishes, so attaching them is a capability for a controlled setting — a developer adding a server they operate — not for arbitrary user input. Opening it to end users additionally requires the tenancy layer plus an approval model for which server may be attached at all, with tool output treated as untrusted input to the model. That layer is deliberately out of scope here (see *Scope and limitations* below).

---

## 5. Operation and deployment

Each of our servers is a standalone FastMCP service — today on one host, later movable anywhere, since only its `*_MCP_URL` changes and no code does.

Normally **one** script starts the whole stack; starting individual processes is only needed for debugging:

```bash
./run.sh                 # everything: MLflow, 8 MCP servers, 7 agents, FastAPI, bridge, Vite
./run.sh status          # what is running right now
./run.sh stop            # stop everything

# Individually (debugging only):
python -m servers.weather_mcp      # :8101
python -m servers.athlete_mcp      # :8109

# Fully containerised (all 20 services, app on :3000):
./docker-up.sh up --build
```

On the host, `ToolHost` runs alongside the servers and reaches them over `localhost`. Under Compose the services address each other by **name** — which is why every server binds `0.0.0.0` there and every `*_MCP_URL` / `*_A2A_URL` is set. That is exactly what the URL-based registry in `core/config.py` is for: same code, different addresses, no code change.

| Server | Port | Backend | Auth |
|---|---|---|---|
| `weather` | 8101 | Open-Meteo | none (free) |
| `routes` | 8102 | OpenRouteService + Overpass | `ORS_API_KEY` |
| `strava` | 8103 | Strava v3 REST API | OAuth2 (`.tokens/strava.json`) |
| `garmin` | 8104 | Garmin Connect (garminconnect) | session token (`.tokens/garmin_tokens.json`) |
| `calendar` | 8105 | Google Calendar | bearer (header or `.tokens/google.json`) |
| `telegram` | 8106 | proxy to `chigwell/telegram-mcp` (stdio) | `TELEGRAM_*` (session string) |
| `flythrough` | 8107 | ours (GPS track → 3D flight) | none |
| `google_maps` | 8108 | Places (New) / Geocoding v4 / Routes API | `GOOGLE_MAPS_API_KEY` (a demo key suffices) |
| `athlete` | 8109 | ours — structured athlete store + training maths | user via the `X-FitDash-User` header |

---

## 6. Scope and limitations

The system implements the uniform MCP host (`ToolHost`) with a tool-agnostic core, **eight** native FastMCP servers (weather/routes/strava/garmin/calendar/flythrough/google_maps/athlete) plus telegram as a proxy to an external stdio server, the A2A agent layer (orchestrator + six specialists), a vendor-neutral LLM seam, tool namespacing, observability via MLflow (`core/tracing.py`), and an identity layer (`api/auth.py`: email + OTP, signed tokens, per-user state under `data/user_memory/<slug>/`). Contract tests at the seams live in `tests/unit/` — the agent-trace contracts, the deterministic training maths and the route export — and run offline via `pytest`.

Two boundaries are worth stating explicitly, because they shape what the architecture is designed for:

- **Identity is per user; upstream credentials are per deployment.** The auth layer separates users, but the Strava and Garmin tokens in `.tokens/` are shared by the whole instance. The design therefore targets a single-athlete deployment — one person's accounts, optionally reachable by that person from several devices. Serving several athletes from one instance is a token-vault question (per-user encrypted credential storage), not a question about the MCP layer, which is already per-request capable through `ToolHost(headers=…)`.
- **User-attachable MCP servers are a mechanism, not an offering.** §4 shows how an external server plugs in, and the uniform host makes that cheap. Exposing it to end users is a separate problem — which servers may be attached, under whose authority, and with tool output treated as untrusted model input — and is out of scope for this implementation.
