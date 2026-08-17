# Training Copilot — architecture review and rebuild recommendation

**Reviewer stance:** external, critical
**Yardstick (target picture):** a public website · multiple users · user-extensible MCP servers · a cross-domain lifestyle assistant (training × weather × calendar …)
**State reviewed:** branch `main` as it stood *before* the rebuild

> **Status note:** this review assesses the **old** `main` architecture and gives the reasons for the rebuild. Since then, phases 1–3 have been implemented in full: a **uniform MCP host** (`core/host.ToolHost`), a **tool-agnostic core** (`core/orchestrator`), **all five servers as native FastMCP services** (weather/routes/strava/garmin/calendar), a **vendor-neutral LLM seam** (`core/llm`), and complete **legacy removal** (registry, `BaseMCPServer`, the agents pipeline, `api.py`). **Still open (phases 4–5):** the tenancy/security layer (per-user identity, token vault, session isolation) and observability/logging. This document is kept as the "why" — the before picture.

---

## 1. Management summary

| Question | Answer |
|---|---|
| Is `main` usable as a prototype / learning state? | **Yes.** |
| Is `main` a viable foundation for the target picture? | **No — three fundamental rebuilds are needed.** |
| Biggest risk | **No tenancy or security model** — not releasable today as a public, user-extensible platform. |
| Common misreading | The problem is **not** "if/else dispatching" (on `main` that correctly uses the registry loop), but **hardcoded domain knowledge** plus the **absence of multi-tenancy**. |

**Three axes of rebuild:**
1. **A tool-agnostic core** — no code may know a tool by name.
2. **A uniform MCP host** — our servers = external servers, one call path.
3. **A tenancy and security model** — per-user identity, isolation, sandboxing of third-party servers.

---

## 2. What is good (keep it)

- **A registry instead of hand-wiring** (`servers/registry.py`): `dispatch()` is a loop over names, not an if/else chain. The right pattern — do **not** delete it (as the `strava` branch did).
- **MCP-shaped tool specs** (`name/description/inputSchema`) plus `to_openai_tools()` → usable provider-neutrally.
- **FastMCP** is already used inside the agents → halfway to a real MCP host.
- **A defensive orchestrator** (try/except plus per-agent timeouts).

---

## 3. Findings by severity

### 🔴 CRITICAL — blocks public multi-user operation

**C-1 · No tenant separation (identity/state).**
Tokens live as **single shared files** (`.tokens/strava.json`, `.tokens/google.json`). That is *one* user on disk. Several website users ⇒ data bleeding between them.
→ *Per-user identity plus a per-user token/secret vault. State must never be global at the file level.*

**C-2 · No session isolation.**
A single Streamlit process with `@st.cache_resource` singletons ⇒ server instances are **shared across all sessions**. User A would get user B's instance and data.
→ *Separate the core from the UI as a multi-tenant service; state per request/user.*

**C-3 · No security for user-added MCP servers.**
Third-party servers are a large attack surface: SSRF, **command execution** over the stdio transport, data exfiltration, and **prompt injection** through tool *descriptions* and tool *outputs* (which flow unfiltered into the LLM context). `main` has **no** allowlist, **no** sandboxing, **no** egress control, and does **not** treat tool output as untrusted.
→ *An approval/allowlist flow, a sandbox plus egress limits, tool output treated as untrusted by default, injection defences.*

**C-4 · Secrets handling.**
OAuth client secrets in env, tokens in plaintext on disk. For multi-user there is no per-user secret store. (See also the calendar scope: over-privileged with a write scope for read-only features.)
→ *An encrypted per-user vault; minimal OAuth scopes.*

### 🟠 HIGH — blocks user-extensible tools

**H-1 · A fixed four-agent pipeline.** (`ui/orchestrator.py`)
"Fetch → viz ∥ flyover → chat" assumes every request means: fetch fitness data, then visualise it. Calendar, finance or smart-home tools do not fit that shape, and cross-domain chaining is not provided for.
→ *A tool-agnostic **tool-use loop** where the model chains arbitrary tools itself. A draft already exists on `feature/tool-use-loop`.*

**H-2 · Per-tool code: `_extract_key_findings`.** (`servers/agents/fetching.py`, ~170 lines)
An `elif tool == "get_garmin_sleep" …` chain. It **breaks for every user-added tool** — unknown tool ⇒ no findings.
→ *A generic, schema-driven summary — no code that names tools.*

**H-3 · Per-tool rendering.** (`ui/viz.py`)
Renders only known tool outputs. User tools render **nothing**.
→ *Schema/type-driven rendering (table, time series or map depending on output shape) with a generic fallback.*

**H-4 · Domain special cases.** A weather fast path (keyword → fixed tools) and flythrough routing (a special path for *one* feature) inside the planner/orchestrator.
→ *Remove both; everything goes through the generic loop.*

**H-5 · A static server list.** (`registry._setup()`)
Servers are hardwired in code — users cannot add anything at runtime.
→ *A per-user connection registry, fillable at runtime from config/DB instead of code.*

### 🟡 MEDIUM — uniformity and cleanliness

**M-1 · Two server styles.** `strava`/`garmin` are legacy classes, `routes`/`weather` use `BaseMCPServer`. They are treated differently in the call path.
→ *One interface for all; ours and external indistinguishable.*

**M-2 · Several call surfaces.** `shared.call_tool`, `registry.dispatch`, and a separate `_find_server_key` in `api.py`. The same job in three places.
→ *One `ToolHost` facade (`list_tools` / `call_tool`) for agents, API and UI.*

**M-3 · The core is coupled to Streamlit.** Agents reach into Streamlit via `ui.shared`, so the core is not standalone, not testable, not serviceable.
→ *A `core/` package with a vendor-neutral LLM seam; Streamlit as just one frontend.*

**M-4 · No tool namespaces.** Tool names are globally flat (`get_activities`). With many servers — especially external ones — they collide.
→ *Namespacing as `server.tool`.*

### 🟢 LOW — quality

- **L-1 · `except Exception: pass`** in many places → swallowed errors, painful debugging. → use `logging`.
- **L-2 · Almost no tests** (only `test_routes.py`) → refactoring without a net. → contract tests at the seams.
- **L-3 · The progress callback duplicates** status entries (cosmetic).

---

## 4. Target architecture

```
                    ┌───────────── Frontends ─────────────┐
                    │  Streamlit UI   ·   web client/API   │
                    └───────────────────┬─────────────────┘
                                        │  (one facade)
                    ┌───────────────────▼─────────────────┐
                    │   CORE (multi-tenant, vendor-neutral)
                    │   • tool-use loop  (tool-AGNOSTIC)   │
                    │   • LLM seam       (provider by config)
                    │   • ToolHost.call_tool / list_tools  │
                    └───────────────────┬─────────────────┘
                                        │  uniform MCP client
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
  our own MCP servers          external MCP servers (user)      per-user registry
  (stdio / in-proc, fast)      (HTTP + OAuth, sandboxed)         + token/secret vault
```

**Principles:** every server treated alike (ours = external) · no code knows tool names · state and secrets per user · tool output = untrusted.

---

## 5. Prioritised rebuild plan

> Rule: **extend, never delete** (the registry stays and grows). Every phase keeps the working `inproc` path.

**Phase 0 — a safety net (immediately)**
- Contract tests at the seams: `call_tool`, `orchestrator.run`, the LLM client. *(L-2)*

**Phase 1 — a tool-agnostic core** *(H-1…H-4)*
- The tool-use loop (from `feature/tool-use-loop`) as the default path, contract-compatible.
- `_extract_key_findings` → generic; per-tool rendering → schema-driven; domain special cases removed.
- **Effect:** arbitrary tools, including unknown ones, work end to end.

**Phase 2 — decoupling plus one facade** *(M-2, M-3)*
- A `core/` package with a vendor-neutral LLM seam (no `@st.cache_resource` in the core).
- `core/host.py` (`ToolHost`) as the **only** `list_tools`/`call_tool` surface for agents, API and UI.

**Phase 3 — a uniform MCP host** *(M-1, M-4, H-5)*
- `ServerEntry` → a connection (`transport=inproc|stdio|http`, endpoint, auth). Existing servers wrapped as `inproc`.
- Tool namespacing. External servers attachable over HTTP — treated exactly like ours.

**Phase 4 — a tenancy and security model** *(C-1…C-4, H-5)* — **mandatory before a public launch**
- Per-user identity; an encrypted per-user token/secret vault; state per request instead of global.
- Split the core out of the Streamlit monolith as a standalone service (session isolation).
- Sandboxing/allowlist/egress limits for user-added servers; tool output as untrusted; prompt-injection defences; minimal OAuth scopes.

**Phase 5 — hardening** *(L-1, L-3)*
- `logging` instead of `except: pass`; observability; fix the duplicated progress entries.

| Phase | Focus | Blocker for … |
|---|---|---|
| 0 | tests | safe refactoring |
| 1 | tool-agnostic core | user-extensible tools |
| 2 | decoupling + facade | a clean call path |
| 3 | uniform MCP host | ours = external servers |
| 4 | tenancy + security | **a public launch** |
| 5 | hardening | operation |

---

## 6. Decision: rebuild or start over

`main` contains the right **building blocks** (registry, MCP specs, FastMCP), so an **incremental rebuild** along phases 0→5 is feasible and lower-risk than starting over. **But** phase 4 (tenancy/security) is not an afterthought: it is the precondition for the product being allowed to go public at all, and therefore belongs early in the plan rather than at the end.

**Recommendation:** an incremental rebuild in the order 0 → 1 → 2 → 3 → **4** → 5. Do not treat phase 4 as "later" — it decides whether the prototype can become a product.
