#!/usr/bin/env bash
# Dev launcher for the React + Node + FastAPI stack (the Streamlit replacement).
#
# Starts (only if their port is free): the MCP servers, the FastAPI seam, and the
# Vite dev server. The React app is then at http://localhost:5173 (Vite proxies
# /api → FastAPI :8000 → MCP servers). The legacy Streamlit app on :8501 can run
# in parallel during the migration.
#
#   PY=/path/to/python ./dev_stack.sh
set -uo pipefail
cd "$(dirname "$0")"
source ./ports.sh || exit 1
source ./scripts/stack_common.sh
PY="${PY:-$(fitdash_resolve_python || true)}"
if [ -z "${PY:-}" ]; then
  echo "✗ python not found (activate a conda env or set PY=…)"; exit 1
fi

pids=()
cleanup() { echo; echo "stopping…"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; }
trap cleanup EXIT INT TERM

fitdash_start_mlflow >/dev/null
fitdash_start_mcp_servers "telegram"
sleep 2
fitdash_build_fitness_index
fitdash_start_agents
sleep 2
fitdash_start_fastapi

# 2b. Telegram bridge — hosts the durable, cross-chat PROACTIVE SCHEDULER (self-
#     scheduled wake-ups, calendar auto-schedule, deep-report delivery). It also
#     mirrors the Telegram DM into the web "Coach" chat. Proactivity is paused when
#     this is down. Exits fast (harmless) if no Telegram session is configured.
if pgrep -f "telegram_bridge.py" >/dev/null 2>&1; then
  echo "✓ telegram bridge already running (proactive scheduler)"
else
  echo "→ starting telegram bridge + proactive scheduler"
  "$PY" telegram_bridge.py >/tmp/telegram_bridge.log 2>&1 &
  pids+=($!)
fi

# 3. Vite dev server
echo "→ starting Vite on :5173  (open http://localhost:5173)"
( cd web && npm run dev )
