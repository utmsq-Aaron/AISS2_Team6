#!/usr/bin/env bash
# FitDash — one-command server launcher.
#
# Starts the whole stack (MCP servers + agents + FastAPI + the web BFF) behind the
# shared PIN gate, publishes it via Tailscale Funnel, starts the Telegram bridge,
# and uses a stable signing key so logins/PINs survive restarts.
#
#     ./server-start.sh             # everything in ONE terminal (logs to /tmp). Headless/launchd-safe.
#     ./server-start.sh --windows   # open separate macOS Terminal windows (App + Telegram bridge)
#
# PIN to enter the app:  230626
# Admin (Settings):      kit.aiss2026@gmail.com  (log in with that email)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PY="${PY:-/opt/miniconda3/envs/aiss/bin/python3}"
APP_PORTS=(5001 8101 8102 8103 8104 8105 8107 9000 9001 9002 9003 9004 9005 8000 3000)

# Stable signing secret — generated once, reused forever (so sessions persist across
# restarts instead of logging everyone out). Needed by the `app` role too.
ensure_secret() {
  mkdir -p .secrets
  SECRET_FILE=".secrets/auth_secret"
  if [ ! -s "$SECRET_FILE" ]; then
    (openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n') > "$SECRET_FILE"
    echo "→ generated a new AUTH_SECRET (stored in $SECRET_FILE)"
  fi
  chmod 600 "$SECRET_FILE" 2>/dev/null || true
}

tg_configured() { grep -qE "^TELEGRAM_API_ID=[\"']?[A-Za-z0-9]" .env 2>/dev/null; }
# A dedicated bridge session lets the bridge and the telegram MCP proxy run at the
# same time (they use DIFFERENT Telegram logins, so neither revokes the other).
tg_dedicated_session() { grep -qE "^TELEGRAM_BRIDGE_SESSION_STRING=[\"']?[A-Za-z0-9]" .env 2>/dev/null; }

# ── Internal roles (invoked in their own window by --windows) ────────────────────
case "${1:-}" in
  app)
    # Backend + agents + FastAPI + BFF + Funnel. The bridge runs in its OWN window,
    # so disable it here (TELEGRAM_BRIDGE=0). Start the telegram MCP here only when a
    # dedicated bridge session exists — otherwise the MCP (shared session) and the
    # bridge window would collide on one Telegram login.
    ensure_secret
    tg_mcp=0; tg_dedicated_session && tg_mcp=1
    exec env DO_LOCK=true APP_PIN="230626" AUTH_SECRET="$(cat "$SECRET_FILE")" \
      FUNNEL=1 TELEGRAM_BRIDGE=0 TELEGRAM_MCP="$tg_mcp" PY="$PY" ./serve.sh
    ;;
  bridge)
    if ! tg_configured; then
      echo "ℹ Telegram not configured in .env — nothing to run in this window."
      exec "${SHELL:-/bin/zsh}"
    fi
    echo "→ waiting for the orchestrator (:9000) to come up…"
    for _ in $(seq 1 40); do lsof -ti tcp:9000 -sTCP:LISTEN >/dev/null 2>&1 && break; sleep 1; done
    echo "→ Telegram bridge (userbot · users sign in with /login)"
    "$PY" telegram_bridge.py
    echo; echo "(bridge exited — window kept open; press Ctrl-C / close it)"; exec "${SHELL:-/bin/zsh}"
    ;;
esac

# ── Launcher (no role arg) ───────────────────────────────────────────────────────
WINDOWS_MODE=false
if [ "${1:-}" = "--windows" ] || [ "${WINDOWS:-0}" = "1" ]; then WINDOWS_MODE=true; fi

echo "=== FitDash · server launcher ==="
[ -x ./serve.sh ] || { echo "✗ serve.sh not found — run this from the repo root."; exit 1; }
command -v "$PY" >/dev/null 2>&1 || { echo "✗ python not found at $PY (set PY=… and retry)."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "✗ node not found — install Node 18+ (brew install node)."; exit 1; }
ensure_secret

# Preflight warnings (don't block — just tell the operator what won't work).
if [ -f .tokens/google_mail.json ] || { [ -f .tokens/google.json ] && grep -q "gmail.send" .tokens/google.json; }; then
  echo "✓ Google/Gmail connected (OTP login email can be sent)"
else
  echo "⚠ Google/Gmail not connected — OTP login emails will FAIL until you connect once:"
  echo "    python auth/google_oauth.py     # sign in as kit.aiss2026@gmail.com, approve calendar + send-email"
  echo "    (and enable the Gmail API for the project in the Google Cloud console)"
fi
command -v tailscale >/dev/null 2>&1 && echo "✓ tailscale present — the app will be published via Funnel" \
  || echo "⚠ 'tailscale' not found — the app will run LOCAL ONLY. Install: brew install tailscale && sudo tailscale up"
if tg_configured; then
  if tg_dedicated_session; then
    echo "✓ Telegram configured (+ dedicated bridge session) — the bridge AND the telegram MCP will both start"
  else
    echo "✓ Telegram configured — the bridge (userbot) will start."
    echo "  ℹ The telegram MCP proxy is skipped: it shares one Telegram login with the bridge,"
    echo "    and two clients on one login get revoked. To run BOTH, mint a separate session:"
    echo "      python telegram_bridge.py --login   # → add TELEGRAM_BRIDGE_SESSION_STRING to .env"
  fi
else
  echo "ℹ Telegram not configured in .env — the bridge will be skipped (web app still runs)."
fi

# Clean restart: free the app's ports so we always come up on current code.
echo "→ stopping any previous FitDash processes…"
for p in "${APP_PORTS[@]}"; do
  pid="$(lsof -ti "tcp:$p" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pid" ] && kill $pid 2>/dev/null || true
done
pkill -f "telegram_bridge.py" 2>/dev/null || true   # the bridge isn't a port
command -v tailscale >/dev/null 2>&1 && tailscale funnel off >/dev/null 2>&1 || true
sleep 2

# ── Windowed mode: open separate macOS Terminal windows ──────────────────────────
open_window() {  # $1 = title, $2 = role
  osascript >/dev/null <<OSA
tell application "Terminal"
  activate
  set w to do script "cd '$HERE' && ./server-start.sh $2"
  set custom title of w to "$1"
end tell
OSA
}

if $WINDOWS_MODE; then
  if ! command -v osascript >/dev/null 2>&1; then
    echo "⚠ --windows needs macOS 'osascript' (not found) — running inline instead."
  else
    echo "→ opening Terminal windows (App · Telegram bridge)…"
    open_window "FitDash · App" app
    if tg_configured; then
      sleep 1
      open_window "FitDash · Telegram bridge" bridge
    fi
    echo "Done. Two windows opened (or one if Telegram isn't configured)."
    echo "PIN 230626 · admin kit.aiss2026@gmail.com · stop by closing the windows."
    exit 0
  fi
fi

# ── Single-terminal mode (default): everything here, bridge included ─────────────
echo "→ starting FitDash (PIN 230626) …"
exec env \
  DO_LOCK=true \
  APP_PIN="230626" \
  AUTH_SECRET="$(cat "$SECRET_FILE")" \
  FUNNEL=1 \
  TELEGRAM_BRIDGE=1 \
  TELEGRAM_MCP=1 \
  PY="$PY" \
  ./serve.sh
