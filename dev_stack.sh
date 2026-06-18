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
PY="${PY:-/opt/miniconda3/envs/aiss/bin/python3}"

pids=()
cleanup() { echo; echo "stopping…"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; }
trap cleanup EXIT INT TERM

port_busy() { lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1; }

# 1. MCP servers (telegram is optional / manual)
for s in weather:8101 routes:8102 strava:8103 garmin:8104 calendar:8105 flythrough:8107; do
  name="${s%%:*}"; port="${s##*:}"
  if port_busy "$port"; then
    echo "✓ $name already on :$port"
  else
    echo "→ starting $name on :$port"
    "$PY" -m "servers.${name}_mcp" >"/tmp/mcp_${name}.log" 2>&1 &
    pids+=($!)
  fi
done
sleep 2

# 1b. A2A agent layer — LangGraph specialists + orchestrator (each its own server).
#     Specialists first, orchestrator (:9000) last. The orchestrator resolves the
#     specialists lazily per request, so startup order isn't load-bearing.
for a in recovery:9001 load:9002 context:9003 route:9004 orchestrator:9000; do
  name="${a%%:*}"; port="${a##*:}"
  if [ "$name" = "orchestrator" ]; then mod="core.orchestrator_agent"; else mod="agents.${name}_agent"; fi
  if port_busy "$port"; then
    echo "✓ agent $name already on :$port"
  else
    echo "→ starting agent $name on :$port  ($mod)"
    "$PY" -m "$mod" >"/tmp/agent_${name}.log" 2>&1 &
    pids+=($!)
  fi
done
sleep 2

# 2. FastAPI seam
if port_busy 8000; then echo "✓ FastAPI already on :8000"; else
  echo "→ starting FastAPI on :8000"
  "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload >/tmp/fitdash_api.log 2>&1 &
  pids+=($!)
fi

# 3. Vite dev server
echo "→ starting Vite on :5173  (open http://localhost:5173)"
( cd web && npm run dev )
