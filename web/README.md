# Training Copilot — React + Node + FastAPI frontend

A professional React frontend replacing the Streamlit UI, while keeping the whole
Python backend (`core/` + `servers/`) untouched. See
[`../docs/`](../docs/) for the MCP architecture.

```
Browser ──▶ React (Vite + TS + Tailwind, :5173 dev)
   │ HTTP + SSE
Node BFF ──▶ Express (:3000, serves built SPA, proxies /api)   ← prod only
   │
FastAPI ──▶ api/ (:8000, wraps core/: ToolHost, orchestrator, charts, settings, sync)
   │ Streamable HTTP (MCP)
MCP servers ──▶ :8101–8107 (unchanged Python)
```

## Run (dev)

The simplest path is the repo-root launcher, which starts the MCP servers + the
FastAPI seam + Vite:

```bash
cd ..            # AISS2_Team6/
./dev_stack.sh   # → http://localhost:5173
```

Or start pieces individually:

```bash
# FastAPI seam (from AISS2_Team6/)
python -m uvicorn api.main:app --port 8000 --reload
# React (from web/) — Vite proxies /api → :8000
npm run dev
```

## Run (production preview)

```bash
cd web && npm run build           # → web/dist
cd ../server && npm install && npm start   # Node BFF on :3000 serves dist + proxies /api
```

## Layout

- `src/lib/api.ts` — typed client over the FastAPI seam (`callTool`, `streamChat`, …)
- `src/theme/` — colour tokens + Plotly dark theme mirrored from `ui/styles.py`
- `src/components/` — shared kit: `MetricCard`, `PlotlyChart`, `RouteMap` (MapLibre),
  `StatusDots`, `PeriodSelector`, `Card`
- `src/pages/` — one file per tab (Dashboard, Health, Routes, Analysis, Chat, Sync, Settings)
- `src/store/uiStore.ts` — global UI state (sport filter, refresh)
