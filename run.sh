#!/usr/bin/env bash
# ==============================================================================
# Training Copilot — the one launcher.
#
#   ./run.sh            start everything for development  → http://localhost:5173
#   ./run.sh prod       build the SPA and serve it behind the BFF  → :3000
#   ./run.sh setup      first-time setup: check tools, create .env, connect accounts
#   ./run.sh status     show what is currently running
#   ./run.sh stop       stop everything this project started
#   ./run.sh logs [x]   tail a service log (e.g. ./run.sh logs orchestrator)
#
# Optional switches (env vars, all default off unless noted):
#   PY=/path/to/python    interpreter to use      (default: .venv, then python3)
#   MLFLOW=0              skip the tracing server (default on, :5001)
#   SCHEDULER=0           skip the proactive scheduler (default ON — it lives in
#                         telegram_bridge.py and runs headless without Telegram;
#                         with it off the coach never checks in on its own)
#   TELEGRAM=1            also start the Telegram bridge (needs .env config)
#   TELEGRAM_MCP=1        also start the Telegram tool proxy (needs `uv`)
#   FUNNEL=1              prod only: publish via Tailscale Funnel
#   PORT=3000             prod only: the BFF port
#
# Everything is idempotent: a service already listening on its port is left
# alone, so re-running this is always safe.
# ==============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Force UTF-8 for every Python subprocess this script spawns. Without it, Python
# on Windows falls back to the system ANSI codepage (e.g. cp1252) whenever stdout
# isn't a real console — which is always true here, since every service's output
# is redirected to a log file — and any print() with a non-Latin-1 character (✓,
# →, ⚠, …) crashes the process with a UnicodeEncodeError. No-op on Linux/macOS,
# which already default to UTF-8.
export PYTHONUTF8=1

MODE="${1:-dev}"

# ── Interpreter ───────────────────────────────────────────────────────────────
# The repo's own venv first — that is what the README tells you to create. A
# conda env only wins if it is already active. Never hardcode a personal path.
if [ -z "${PY:-}" ]; then
  if   [ -x "$HERE/.venv/bin/python" ];              then PY="$HERE/.venv/bin/python"
  elif [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python3" ]; then PY="$CONDA_PREFIX/bin/python3"
  else PY="$(command -v python3 || true)"
  fi
fi
export PY

source ./ports.sh || exit 1

BFF_PORT="${PORT:-3000}"
LOG_DIR="${LOG_DIR:-/tmp/training-copilot}"
mkdir -p "$LOG_DIR"

pids=()
cleanup() {
  echo; echo "stopping…"
  for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null; done
  [ "${FUNNEL:-0}" = "1" ] && command -v tailscale >/dev/null 2>&1 && tailscale funnel reset >/dev/null 2>&1
  return 0
}
port_busy() { lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1; }
env_has()   { grep -qE "^$1=[\"']?[A-Za-z0-9]" .env 2>/dev/null; }
say()       { printf "  %s\n" "$*"; }
ok()        { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn()      { printf "  \033[33m⚠\033[0m %s\n" "$*"; }
bad()       { printf "  \033[31m✗\033[0m %s\n" "$*"; }
head1()     { printf "\n\033[1m%s\033[0m\n" "$*"; }

# ── stop / status / logs ──────────────────────────────────────────────────────
if [ "$MODE" = "stop" ]; then
  head1 "Stopping Training Copilot"
  n=0
  for p in "${ALL_PORTS[@]}" "$BFF_PORT"; do
    pid="$(lsof -ti "tcp:$p" -sTCP:LISTEN 2>/dev/null || true)"
    [ -n "$pid" ] && { kill $pid 2>/dev/null && n=$((n+1)); }
  done
  pkill -f "telegram_bridge.py" 2>/dev/null && n=$((n+1))
  command -v tailscale >/dev/null 2>&1 && tailscale funnel reset >/dev/null 2>&1
  ok "stopped $n process group(s)"
  exit 0
fi

if [ "$MODE" = "status" ]; then
  head1 "Training Copilot — running services"
  probe() { if port_busy "$2"; then ok "$1 on :$2"; else say "· $1 (:$2) — down"; fi; }
  probe "MLflow"  "$MLFLOW_PORT"
  for s in "${MCP_SERVERS[@]}"; do probe "MCP ${s%%:*}" "${s##*:}"; done
  for a in "${AGENT_PORTS[@]}"; do probe "agent ${a%%:*}" "${a##*:}"; done
  probe "FastAPI" "$FASTAPI_PORT"
  probe "Vite"    "$VITE_PORT"
  probe "BFF"     "$BFF_PORT"
  pgrep -f telegram_bridge.py >/dev/null 2>&1 && ok "Telegram bridge + scheduler" \
    || say "· Telegram bridge — down (proactive check-ins are paused)"
  exit 0
fi

if [ "$MODE" = "logs" ]; then
  svc="${2:-}"
  [ -z "$svc" ] && { head1 "Available logs in $LOG_DIR"; ls "$LOG_DIR" 2>/dev/null; exit 0; }
  f="$LOG_DIR/${svc}.log"
  [ -f "$f" ] || f="$(ls "$LOG_DIR"/*"$svc"*.log 2>/dev/null | head -1)"
  [ -f "${f:-}" ] || { bad "no log matching '$svc' in $LOG_DIR"; exit 1; }
  exec tail -f "$f"
fi

# ── Preflight: everything both dev and prod need ──────────────────────────────
preflight() {
  local fatal=0
  head1 "Checking prerequisites"

  if [ -z "${PY:-}" ] || ! command -v "$PY" >/dev/null 2>&1; then
    bad "no Python found. Create the venv:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    fatal=1
  else
    ok "python  $("$PY" --version 2>&1)  [$PY]"
    "$PY" -c "import fastapi, mcp, langchain" 2>/dev/null \
      || { bad "dependencies missing. Run:  $PY -m pip install -r requirements.txt"; fatal=1; }
  fi

  if command -v node >/dev/null 2>&1; then
    ok "node    $(node --version)"
  else
    bad "node not found (need 18+). macOS: brew install node"
    fatal=1
  fi

  [ -d web/node_modules ] && ok "frontend dependencies installed" \
    || { bad "web/node_modules missing. Run:  ./run.sh setup   (or: cd web && npm install)"; fatal=1; }

  if [ -f .env ]; then
    ok ".env present"
  else
    bad ".env missing. Run:  ./run.sh setup"
    fatal=1
  fi
  # A key counts whether it comes from .env or from the environment — someone may
  # legitimately export it in their shell or inject it from a secret store.
  have_key() { for k in "$@"; do [ -n "${!k:-}" ] && return 0; env_has "$k" && return 0; done; return 1; }
  have_key OPENAI_API_KEY GEMINI_API_KEY OPENAI_OFFICIAL_API_KEY \
    || { bad "no LLM key found — the chat cannot work. See README → Configuration."; fatal=1; }

  [ "$fatal" = "1" ] && { echo; bad "fix the above, then re-run."; exit 1; }

  # Non-fatal: integrations that simply stay dark when unconfigured.
  head1 "Optional integrations"
  [ -f .tokens/strava.json ]        && ok "Strava connected"          || say "· Strava not connected — the OAuth flow runs on first use"
  [ -f .tokens/garmin_tokens.json ] && ok "Garmin connected"          || say "· Garmin not connected — $PY auth/garmin_setup.py"
  [ -f .tokens/google.json ]        && ok "Google Calendar connected" || say "· Google Calendar not connected — $PY auth/google_oauth.py"
  have_key TELEGRAM_API_ID          && ok "Telegram configured"       || say "· Telegram not configured (optional)"
  have_key GOOGLE_MAPS_API_KEY      && ok "Google Maps key set"       || say "· Google Maps key missing (route agent degrades)"
  have_key ORS_API_KEY              && ok "OpenRouteService key set"  || say "· ORS key missing (route planning degrades)"
}

# ── setup: interactive first run ──────────────────────────────────────────────
if [ "$MODE" = "setup" ]; then
  head1 "Training Copilot — first-time setup"

  if [ ! -d .venv ] && [ ! -n "${CONDA_PREFIX:-}" ]; then
    say "Creating the virtualenv (.venv)…"
    python3 -m venv .venv || { bad "could not create .venv"; exit 1; }
    PY="$HERE/.venv/bin/python"
  fi
  say "Installing Python dependencies…"
  "$PY" -m pip install -q -r requirements.txt || { bad "pip install failed"; exit 1; }
  ok "Python dependencies installed"

  if command -v npm >/dev/null 2>&1; then
    say "Installing frontend dependencies…"
    ( cd web && npm install --silent ) && ok "frontend dependencies installed"
  else
    warn "npm not found — install Node 18+ and re-run this"
  fi

  if [ ! -f .env ]; then
    cp .env.example .env
    ok "created .env from .env.example"
    warn "EDIT .env NOW — at minimum an LLM key (OPENAI_API_KEY + OPENAI_BASE_URL + AGENT_MODEL)."
    say  "Then re-run ./run.sh setup to continue with the account connections."
    exit 0
  fi
  ok ".env present"

  head1 "Connecting accounts"
  say "Each of these is optional and can also be done later in the app's Settings page."
  echo
  # Report what is ALREADY connected rather than printing the same four lines every
  # run: a cached token in .tokens/ is exactly what the servers check at call time,
  # so its presence is the same truth the app uses. Only the missing ones get a
  # command to run, which is what the README promises this step does.
  if [ -f .tokens/strava.json ]; then
    ok  "Strava    — connected"
  elif env_has CLIENT_ID && env_has CLIENT_SECRET; then
    say "Strava    — OAuth opens in the browser on first use; nothing to do here."
  else
    warn "Strava    — set CLIENT_ID / CLIENT_SECRET in .env to enable it."
  fi

  if [ -f .tokens/garmin_tokens.json ]; then
    ok  "Garmin    — connected"
  else
    say "Garmin    — needs a one-time login (MFA capable):"
    say "              $PY -m auth garmin"
  fi

  if [ -f .tokens/google.json ] || [ -f .tokens/google_mail.json ]; then
    ok  "Google    — connected"
  else
    say "Google    — Calendar + the OTP login mailer:"
    say "              $PY -m auth gmail"
  fi

  if env_has TELEGRAM_SESSION_STRING || env_has TELEGRAM_BRIDGE_SESSION_STRING; then
    ok  "Telegram  — session configured"
  else
    say "Telegram  — optional chat bridge; put TELEGRAM_API_ID/HASH in .env, then:"
    say "              $PY telegram_bridge.py --login"
  fi
  echo
  say "Run all the one-off flows in sequence with:  $PY -m auth all"
  echo
  say "Building the fitness knowledge index (one-time, downloads a small model)…"
  "$PY" -m scripts.build_fitness_index --if-missing \
    && ok "fitness index ready" || warn "index build failed — the fitness agent will degrade gracefully"

  head1 "Done"
  say "Start the app with:   ./run.sh"
  exit 0
fi

# ── Telegram decisions (shared by dev and prod) ───────────────────────────────
TG_BRIDGE_ON=false; TG_MCP_ON=false
if [ "${TELEGRAM:-0}" = "1" ] || [ "${TELEGRAM_BRIDGE:-0}" = "1" ]; then
  if env_has TELEGRAM_API_ID && env_has TELEGRAM_API_HASH \
     && { env_has TELEGRAM_SESSION_STRING || env_has TELEGRAM_BRIDGE_SESSION_STRING; }; then
    TG_BRIDGE_ON=true
  else
    warn "TELEGRAM=1 but .env lacks API_ID / API_HASH / a session string — skipping the bridge."
  fi
fi
if [ "${TELEGRAM_MCP:-0}" = "1" ]; then
  if command -v uv >/dev/null 2>&1 && env_has TELEGRAM_API_ID && env_has TELEGRAM_SESSION_STRING; then
    TG_MCP_ON=true
  else
    warn "TELEGRAM_MCP=1 but 'uv' or the TELEGRAM_* vars are missing — skipping the tool proxy."
  fi
fi
# One Telegram login cannot serve two clients — the key gets revoked. Keep the
# bridge, drop the proxy, unless a dedicated bridge session separates them.
if $TG_BRIDGE_ON && $TG_MCP_ON && ! env_has TELEGRAM_BRIDGE_SESSION_STRING; then
  warn "bridge + MCP proxy would share one Telegram login — starting the BRIDGE only."
  say  "To run both:  $PY telegram_bridge.py --login   → TELEGRAM_BRIDGE_SESSION_STRING in .env"
  TG_MCP_ON=false
fi

# ── Backend: identical for dev and prod ───────────────────────────────────────
start_backend() {
  head1 "Starting backend"

  if [ "${MLFLOW:-1}" = "1" ]; then
    if port_busy "$MLFLOW_PORT"; then ok "MLflow already on :$MLFLOW_PORT"; else
      say "MLflow → :$MLFLOW_PORT"
      "$PY" -m mlflow server --host 127.0.0.1 --port "$MLFLOW_PORT" \
        --backend-store-uri "sqlite:///mlflow.db" >"$LOG_DIR/mlflow.log" 2>&1 &
      pids+=($!)
      for _ in $(seq 1 40); do
        curl -sf "http://127.0.0.1:${MLFLOW_PORT}/health" >/dev/null 2>&1 && break; sleep 0.5
      done
    fi
  fi

  for s in "${MCP_SERVERS[@]}"; do
    name="${s%%:*}"; port="${s##*:}"
    if port_busy "$port"; then ok "MCP $name already on :$port"; else
      say "MCP $name → :$port"
      "$PY" -m "servers.${name}_mcp" >"$LOG_DIR/mcp-${name}.log" 2>&1 &
      pids+=($!)
    fi
  done
  if $TG_MCP_ON && ! port_busy "$TELEGRAM_MCP_PORT"; then
    say "MCP telegram → :$TELEGRAM_MCP_PORT"
    "$PY" -m servers.telegram_mcp >"$LOG_DIR/mcp-telegram.log" 2>&1 &
    pids+=($!)
  fi
  sleep 2

  # The fitness agent reads this index; building it is a no-op once it exists.
  "$PY" -m scripts.build_fitness_index --if-missing >"$LOG_DIR/fitness-index.log" 2>&1 \
    && ok "fitness index ready" \
    || warn "fitness index unavailable — the fitness agent degrades gracefully"

  # Specialists then the orchestrator. Order is not load-bearing (the orchestrator
  # resolves specialists per request), but it makes the log read sensibly.
  for a in "${AGENT_PORTS[@]}"; do
    name="${a%%:*}"; port="${a##*:}"
    [ "$name" = "orchestrator" ] && mod="core.orchestrator_agent" || mod="agents.${name}_agent"
    if port_busy "$port"; then ok "agent $name already on :$port"; else
      say "agent $name → :$port"
      "$PY" -m "$mod" >"$LOG_DIR/agent-${name}.log" 2>&1 &
      pids+=($!)
    fi
  done
  sleep 2

  # The bridge also hosts the durable proactive scheduler — without it the coach
  # never checks in on its own. It runs headless when Telegram isn't configured.
  if $TG_BRIDGE_ON || [ "${SCHEDULER:-1}" = "1" ]; then
    if pgrep -f "telegram_bridge.py" >/dev/null 2>&1; then
      ok "bridge + proactive scheduler already running"
    else
      say "bridge + proactive scheduler"
      "$PY" telegram_bridge.py >"$LOG_DIR/bridge.log" 2>&1 &
      pids+=($!)
    fi
  fi
}

wait_for_api() {
  for _ in $(seq 1 60); do
    curl -sf "http://127.0.0.1:${FASTAPI_PORT}/api/ping" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

# ── prod ──────────────────────────────────────────────────────────────────────
if [ "$MODE" = "prod" ]; then
  preflight
  trap cleanup EXIT INT TERM

  if [ "${DO_LOCK:-false}" = "true" ] && [ -z "${APP_PIN:-}" ]; then
    APP_PIN="$(sed -n 's/^APP_PIN=["'\'']\{0,1\}\([^"'\'']*\)["'\'']\{0,1\}[[:space:]]*$/\1/p' .env | tail -1)"
    [ -z "$APP_PIN" ] && { bad "DO_LOCK=true but APP_PIN is set nowhere (.env or env)."; exit 1; }
  fi
  # A stable signing key, so logins survive a restart instead of logging everyone out.
  mkdir -p .secrets
  [ -s .secrets/auth_secret ] || \
    (openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n') > .secrets/auth_secret
  chmod 600 .secrets/auth_secret 2>/dev/null || true
  export AUTH_SECRET="${AUTH_SECRET:-$(cat .secrets/auth_secret)}"

  if [ "${SKIP_BUILD:-0}" = "1" ] && [ -d web/dist ]; then
    ok "reusing web/dist (SKIP_BUILD=1)"
  else
    head1 "Building the SPA"
    ( cd web && { [ -d node_modules ] || npm ci; } && npm run build ) || { bad "web build failed"; exit 1; }
  fi

  start_backend

  head1 "Starting API + BFF"
  if port_busy "$FASTAPI_PORT"; then ok "FastAPI already on :$FASTAPI_PORT"; else
    say "FastAPI → 127.0.0.1:$FASTAPI_PORT"
    "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$FASTAPI_PORT" \
      >"$LOG_DIR/api.log" 2>&1 &
    pids+=($!)
  fi
  wait_for_api || { bad "FastAPI did not come up — see $LOG_DIR/api.log"; exit 1; }
  ok "FastAPI ready"

  ( cd server && [ -d node_modules ] || npm ci ) || { bad "BFF deps failed"; exit 1; }
  say "BFF → ${HOST:-127.0.0.1}:${BFF_PORT}"
  ( cd server && HOST="${HOST:-127.0.0.1}" PORT="$BFF_PORT" \
      API_TARGET="http://127.0.0.1:${FASTAPI_PORT}" \
      DO_LOCK="${DO_LOCK:-false}" APP_PIN="${APP_PIN:-}" AUTH_SECRET="$AUTH_SECRET" \
      node index.js ) &
  pids+=($!)
  sleep 2

  if [ "${FUNNEL:-0}" = "1" ] && command -v tailscale >/dev/null 2>&1; then
    tailscale funnel --bg "$BFF_PORT" >/dev/null 2>&1 \
      && ok "published via Tailscale Funnel" || warn "tailscale funnel failed — running local only"
  fi

  head1 "Running"
  ok "open http://localhost:${BFF_PORT}"
  say "logs: $LOG_DIR   ·   stop: Ctrl-C (or ./run.sh stop from another shell)"
  wait
  exit 0
fi

# ── dev (default) ─────────────────────────────────────────────────────────────
if [ "$MODE" != "dev" ]; then
  bad "unknown mode '$MODE'"
  say "usage: ./run.sh [dev|prod|setup|status|stop|logs <service>]"
  exit 1
fi

preflight
trap cleanup EXIT INT TERM
start_backend

head1 "Starting API"
if port_busy "$FASTAPI_PORT"; then ok "FastAPI already on :$FASTAPI_PORT"; else
  say "FastAPI → :$FASTAPI_PORT (auto-reload)"
  "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$FASTAPI_PORT" --reload \
    >"$LOG_DIR/api.log" 2>&1 &
  pids+=($!)
fi
wait_for_api || warn "FastAPI slow to start — see $LOG_DIR/api.log"

head1 "Starting the web app"
ok "open http://localhost:${VITE_PORT}"
say "logs: $LOG_DIR   ·   MLflow traces: http://localhost:$MLFLOW_PORT"
say "stop everything with Ctrl-C"
echo
# Vite runs in the foreground: its output is what you watch while developing,
# and Ctrl-C here tears the whole stack down via the EXIT trap.
( cd web && npm run dev )
