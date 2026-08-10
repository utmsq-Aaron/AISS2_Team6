# Training Copilot — frontend

The React SPA. The Python backend (`core/` + `agents/` + `servers/`) is reached only
through the FastAPI seam in `api/` — this app never talks to an MCP server directly.
See [`../docs/mcp-architecture.md`](../docs/mcp-architecture.md) for the whole picture.

```
Browser ──▶ React (Vite + TS + Tailwind)          :5173 dev
   │ HTTP + SSE
Node BFF ──▶ Express — serves the built SPA,       :3000 prod only
   │        proxies /api, hosts the PIN gate
FastAPI ──▶ api/ — ToolHost, chat SSE, charts,     :8000
   │        settings, auth
   │ Streamable HTTP (MCP)
MCP servers                                        :8101–:8109
```

## Run

From the repo root — the launcher starts everything, this app included:

```bash
cd ..
./run.sh          # → http://localhost:5173
```

Only working on the frontend and already have the backend up? `npm run dev` here is
enough; Vite proxies `/api` to FastAPI (target read live from `core/config.py`).

```bash
npm run dev         # dev server with hot reload
npm run typecheck   # tsc --noEmit
npm run build       # → web/dist (what the BFF serves in production)
```

## Layout

| Path | What's in it |
|---|---|
| `src/App.tsx` | The shell: auth gate → onboarding → sidebar + routes |
| `src/nav.ts` | **Single source of truth** for the primary navigation — sidebar, breadcrumb and quick-search all read it |
| `src/pages/` | One file per page: Dashboard, Coach, Health, Chat, Settings, Login |
| `src/lib/api.ts` | Typed client over the FastAPI seam (`callTool`, `streamChat`, …) |
| `src/components/` | Shared kit: `MetricCard`, `PlotlyChart`, `RouteMap` (MapLibre), `PeriodSelector`, `Card` |
| `src/components/{analysis,chat,dashboard,onboarding}/` | Per-feature components |
| `src/theme/` | Colour tokens + the dark Plotly theme, kept in step with `core/viz_telegram.py` so charts look the same in the browser and in Telegram |
| `src/store/` | Global UI state (zustand): auth, chat, refresh trigger |

**Adding a page:** create it in `src/pages/`, add one entry to `src/nav.ts` *and* one
`<Route>` in `src/App.tsx`. Miss the second step and the page exists but is
unreachable — which is exactly how the old Routes and Sync pages became dead code.
