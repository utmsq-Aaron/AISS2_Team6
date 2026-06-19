#!/usr/bin/env bash
# FitDash launcher — opens THREE macOS Terminal windows:
#   1. MCP servers (+ FastAPI seam on :8000)   — the backend
#   2. React UI (Vite dev server)              — the frontend
#   3. Telegram bridge                         — the userbot
#
# Every window activates the `aiss` conda env first. Ports already in use are
# detected and skipped (the running instance is reused) so re-running this is safe.
#
#   ./start.sh            # open the three windows
#   ./start.sh mcps       # (used internally) run just the backend in this terminal
#   ./start.sh web        # (used internally) run just the Vite dev server
#   ./start.sh bridge     # (used internally) run just the Telegram bridge
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

CONDA_SH="${CONDA_SH:-/opt/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-aiss}"

# --- activate the conda env (works in a fresh non-login Terminal) -------------
activate_env() {
  if [ -f "$CONDA_SH" ]; then
    # shellcheck disable=SC1090
    source "$CONDA_SH" && conda activate "$CONDA_ENV"
  fi
  echo "python: $(command -v python)"
}

port_busy() { lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1; }

# first free port at-or-after $1
free_port() { local p="$1"; while port_busy "$p"; do p=$((p+1)); done; echo "$p"; }

# =============================================================================
# role: backend — MCP servers + FastAPI seam
# =============================================================================
run_mcps() {
  activate_env
  pids=()
  cleanup() { echo; echo "stopping backend…"; for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null; done; }
  trap cleanup EXIT INT TERM

  echo "=== MLflow tracking server ==="
  # Port 5001, not 5000 — macOS Control Center/AirPlay Receiver squats on :5000.
  if port_busy 5001; then
    echo "✓ MLflow already on :5001 (reusing)"
  else
    echo "→ starting MLflow on :5001  (UI at http://localhost:5001)"
    python -m mlflow server --host 127.0.0.1 --port 5001 \
      --backend-store-uri "sqlite:///mlflow.db" >/tmp/mlflow.log 2>&1 &
    pids+=($!)
    for _ in $(seq 1 40); do
      curl -sf http://127.0.0.1:5001/health >/dev/null 2>&1 && { echo "  MLflow ready"; break; }
      sleep 0.5
    done
  fi

  echo "=== MCP servers ==="
  for s in weather:8101 routes:8102 strava:8103 garmin:8104 calendar:8105 flythrough:8107; do
    name="${s%%:*}"; port="${s##*:}"
    if port_busy "$port"; then
      echo "✓ $name already on :$port (reusing)"
    else
      echo "→ starting $name on :$port"
      python -m "servers.${name}_mcp" >"/tmp/mcp_${name}.log" 2>&1 &
      pids+=($!)
    fi
  done
  sleep 2

  echo "=== A2A agents (LangGraph specialists + orchestrator) ==="
  for a in recovery:9001 load:9002 context:9003 route:9004 orchestrator:9000; do
    name="${a%%:*}"; port="${a##*:}"
    if [ "$name" = "orchestrator" ]; then mod="core.orchestrator_agent"; else mod="agents.${name}_agent"; fi
    if port_busy "$port"; then
      echo "✓ agent $name already on :$port (reusing)"
    else
      echo "→ starting agent $name on :$port"
      python -m "$mod" >"/tmp/agent_${name}.log" 2>&1 &
      pids+=($!)
    fi
  done
  sleep 2

  echo "=== FastAPI seam ==="
  if port_busy 8000; then
    echo "✓ FastAPI already on :8000 (reusing)"
  else
    echo "→ starting FastAPI on :8000"
    python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload &
    pids+=($!)
  fi

  echo
  echo "Backend up. Logs: /tmp/mcp_*.log   (Ctrl-C to stop the ones started here)"
  wait
}

# =============================================================================
# role: frontend — Vite dev server
# =============================================================================
run_web() {
  activate_env
  cd "$HERE/web"
  [ -d node_modules ] || { echo "→ installing web deps (first run)…"; npm install; }
  port="$(free_port 5173)"
  [ "$port" = 5173 ] || echo "⚠ :5173 busy → using :$port"
  echo "→ Vite on http://localhost:$port"
  exec npm run dev -- --port "$port" --strictPort
}

# =============================================================================
# role: bridge — Telegram userbot
# =============================================================================
run_bridge() {
  activate_env
  if pgrep -f "telegram_bridge.py" >/dev/null 2>&1; then
    echo "✓ telegram bridge already running (not starting a second one)."
    echo "  A second client on the same Telegram session can get the login revoked."
    exec "${SHELL:-/bin/zsh}"
  fi
  echo "→ starting Telegram bridge"
  exec python telegram_bridge.py
}

# --- in-terminal roles -------------------------------------------------------
case "${1:-}" in
  mcps)   run_mcps   ;;
  web)    run_web    ;;
  bridge) run_bridge ;;
  "" )    ;;  # fall through to the launcher below
  * ) echo "usage: $0 [mcps|web|bridge]"; exit 1 ;;
esac
[ -n "${1:-}" ] && exit 0

# =============================================================================
# launcher (no arg) — open three Terminal.app windows
# =============================================================================
open_window() {  # $1 = title, $2 = role
  osascript >/dev/null <<OSA
tell application "Terminal"
  activate
  set w to do script "cd '$HERE' && ./start.sh $2"
  set custom title of w to "$1"
end tell
OSA
}

echo "Opening three Terminal windows (backend · web · telegram bridge)…"
open_window "FitDash · MCP + API"  mcps
sleep 1   # let the backend grab its ports before the bridge/web start
open_window "FitDash · React UI"   web
open_window "FitDash · Telegram"   bridge
echo "Done. Backend logs in /tmp/mcp_*.log. Re-run ./start.sh anytime — busy ports are reused."
