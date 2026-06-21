#!/usr/bin/env bash
# Production serve — one command to host FitDash for the web.
#
# Builds the React SPA, brings up the backend (MLflow + MCP servers + the six
# agents + FastAPI), then serves everything through the Node BFF on ONE port.
# The BFF binds 127.0.0.1 by default, so nothing is exposed directly — put a
# tunnel (Cloudflare/Tailscale) in front of it (see docs/deploy-macmini.md).
#
#   ./serve.sh                  # build + run; BFF on 127.0.0.1:3000
#   HOST=0.0.0.0 ./serve.sh     # also reachable directly on the LAN (:3000)
#   SKIP_BUILD=1 ./serve.sh     # reuse an existing web/dist (faster restarts)
#   DO_LOCK=true APP_PIN=1234 ./serve.sh   # add a shared PIN gate in front
#
# Env:
#   PY         python to use      (default /opt/miniconda3/envs/aiss/bin/python3)
#   HOST       BFF bind host       (default 127.0.0.1)
#   PORT       BFF port            (default 3000)
#   MLFLOW     "0" to skip MLflow  (default on)
#   FUNNEL     "1" to also start a public Tailscale Funnel in front of the BFF
#              (needs `tailscale` installed + `tailscale up` done once)
set -uo pipefail
cd "$(dirname "$0")"

PY="${PY:-/opt/miniconda3/envs/aiss/bin/python3}"
BFF_HOST="${HOST:-127.0.0.1}"
BFF_PORT="${PORT:-3000}"

pids=()
cleanup() {
  echo; echo "stopping…"
  for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null; done
  # tear down the public tunnel so we don't leave the app exposed after stopping
  [ "${FUNNEL:-0}" = "1" ] && command -v tailscale >/dev/null 2>&1 && tailscale funnel off >/dev/null 2>&1
}
trap cleanup EXIT INT TERM
port_busy() { lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1; }

echo "=== FitDash · production serve ==="
command -v "$PY" >/dev/null 2>&1 || { echo "✗ python not found at $PY (set PY=…)"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "✗ node not found (install Node 18+)"; exit 1; }

# 0. Build the SPA (skippable for fast restarts)
if [ "${SKIP_BUILD:-0}" = "1" ] && [ -d web/dist ]; then
  echo "✓ reusing existing web/dist (SKIP_BUILD=1)"
else
  echo "→ building React SPA → web/dist"
  ( cd web && { [ -d node_modules ] || npm ci; } && npm run build ) \
    || { echo "✗ web build failed"; exit 1; }
fi

# 1. MLflow tracking (best-effort; agents degrade gracefully if it's down)
if [ "${MLFLOW:-1}" = "1" ]; then
  if port_busy 5001; then echo "✓ MLflow already on :5001"; else
    echo "→ MLflow on :5001"
    "$PY" -m mlflow server --host 127.0.0.1 --port 5001 \
      --backend-store-uri "sqlite:///mlflow.db" >/tmp/mlflow.log 2>&1 &
    pids+=($!)
  fi
fi

# 2. MCP servers
for s in weather:8101 routes:8102 strava:8103 garmin:8104 calendar:8105 flythrough:8107; do
  name="${s%%:*}"; port="${s##*:}"
  if port_busy "$port"; then echo "✓ $name already on :$port"; else
    echo "→ $name on :$port"
    "$PY" -m "servers.${name}_mcp" >"/tmp/mcp_${name}.log" 2>&1 &
    pids+=($!)
  fi
done
sleep 2

# 2a. Fitness RAG index (built once; instant skip if present)
"$PY" -m scripts.build_fitness_index --if-missing \
  || echo "⚠ fitness index unavailable — the fitness agent will degrade gracefully"

# 3. A2A agents (specialists first, orchestrator last)
for a in recovery:9001 load:9002 context:9003 route:9004 fitness:9005 orchestrator:9000; do
  name="${a%%:*}"; port="${a##*:}"
  [ "$name" = "orchestrator" ] && mod="core.orchestrator_agent" || mod="agents.${name}_agent"
  if port_busy "$port"; then echo "✓ agent $name already on :$port"; else
    echo "→ agent $name on :$port ($mod)"
    "$PY" -m "$mod" >"/tmp/agent_${name}.log" 2>&1 &
    pids+=($!)
  fi
done
sleep 2

# 4. FastAPI (internal only — the BFF proxies to it; no --reload in production)
if port_busy 8000; then echo "✓ FastAPI already on :8000"; else
  echo "→ FastAPI on 127.0.0.1:8000"
  "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port 8000 >/tmp/fitdash_api.log 2>&1 &
  pids+=($!)
fi
sleep 2

# 5. BFF — serves the SPA + proxies /api. This is the only externally-fronted port.
( cd server && [ -d node_modules ] || npm ci ) || { echo "✗ BFF deps failed"; exit 1; }
echo "→ BFF on ${BFF_HOST}:${BFF_PORT}  (open http://localhost:${BFF_PORT} on this machine)"
( cd server && HOST="$BFF_HOST" PORT="$BFF_PORT" API_TARGET="http://127.0.0.1:8000" \
    DO_LOCK="${DO_LOCK:-false}" APP_PIN="${APP_PIN:-}" node index.js ) &
bff=$!; pids+=($bff)
sleep 2

# 6. Public tunnel (opt-in) — Tailscale Funnel in front of the BFF.
if [ "${FUNNEL:-0}" = "1" ]; then
  if command -v tailscale >/dev/null 2>&1; then
    echo "→ Tailscale Funnel → public HTTPS in front of :${BFF_PORT}"
    # --bg registers the funnel with the tailscaled daemon (persists, prints the URL).
    tailscale funnel --bg "${BFF_PORT}" || echo "⚠ funnel failed — is 'tailscale up' done and Funnel enabled in the admin console?"
    tailscale funnel status 2>/dev/null | sed -n '1,8p' || true
  else
    echo "⚠ FUNNEL=1 but 'tailscale' not found — install it (brew install tailscale) and run 'sudo tailscale up' first."
  fi
fi

echo "=== up. ${FUNNEL:+public via Tailscale Funnel · }open http://localhost:${BFF_PORT} on this machine. Ctrl-C to stop. ==="
wait "$bff"
