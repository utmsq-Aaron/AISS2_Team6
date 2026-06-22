#!/usr/bin/env bash
# FitDash — one-command server launcher.
#
# Starts the whole stack (MCP servers + agents + FastAPI + the web BFF) behind the
# shared PIN gate, publishes it publicly via Tailscale Funnel, and uses a stable
# signing key so logins/PINs survive restarts. Just run:
#
#     ./server-start.sh
#
# PIN to enter the app:  230626
# Admin (Settings):      kit.aiss2026@gmail.com  (log in with that email)
set -uo pipefail
cd "$(dirname "$0")"

PY="${PY:-/opt/miniconda3/envs/aiss/bin/python3}"
APP_PORTS=(5001 8101 8102 8103 8104 8105 8107 9000 9001 9002 9003 9004 9005 8000 3000)

echo "=== FitDash · Mac mini launcher ==="

# 1. Sanity: are we in the repo and is the tooling here?
[ -x ./serve.sh ] || { echo "✗ serve.sh not found — run this from the repo root."; exit 1; }
command -v "$PY" >/dev/null 2>&1 || { echo "✗ python not found at $PY (set PY=… and retry)."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "✗ node not found — install Node 18+ (brew install node)."; exit 1; }

# 2. Stable signing secret — generated once, reused forever (so sessions persist
#    across restarts instead of logging everyone out).
mkdir -p .secrets
SECRET_FILE=".secrets/auth_secret"
if [ ! -s "$SECRET_FILE" ]; then
  (openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n') > "$SECRET_FILE"
  echo "→ generated a new AUTH_SECRET (stored in $SECRET_FILE)"
fi
chmod 600 "$SECRET_FILE" 2>/dev/null || true

# 3. Preflight warnings (don't block — just tell the operator what won't work).
if [ -f .tokens/google.json ] && grep -q "gmail.send" .tokens/google.json; then
  echo "✓ Google connected with gmail.send (OTP email can be sent)"
else
  echo "⚠ Google/Gmail not connected — OTP login emails will FAIL until you connect once:"
  echo "    python auth/google_oauth.py     # sign in as kit.aiss2026@gmail.com, approve calendar + send-email"
  echo "    (and enable the Gmail API for the project in the Google Cloud console)"
fi
if command -v tailscale >/dev/null 2>&1; then
  echo "✓ tailscale present — the app will be published via Funnel"
else
  echo "⚠ 'tailscale' not found — the app will run LOCAL ONLY (no public URL)."
  echo "    Install: brew install tailscale && sudo tailscale up   (then enable Funnel/HTTPS in the admin console)"
fi
if grep -qE "^TELEGRAM_API_ID=[\"']?[A-Za-z0-9]" .env 2>/dev/null; then
  echo "✓ Telegram configured — the bridge (userbot) will start"
else
  echo "ℹ Telegram not configured in .env — the bridge will be skipped (web app still runs)."
fi

# 4. Clean restart: free the app's ports so we always come up on current code.
echo "→ stopping any previous FitDash processes…"
for p in "${APP_PORTS[@]}"; do
  pid="$(lsof -ti "tcp:$p" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pid" ] && kill $pid 2>/dev/null || true
done
pkill -f "telegram_bridge.py" 2>/dev/null || true   # the bridge isn't a port
command -v tailscale >/dev/null 2>&1 && tailscale funnel off >/dev/null 2>&1 || true
sleep 2

# 5. Go. serve.sh builds the SPA and starts everything: backend + PIN gate + Funnel
#    + the Telegram bridge. (TELEGRAM_MCP=1 too, but serve.sh keeps just the bridge
#    if they'd share one Telegram session — set TELEGRAM_BRIDGE_SESSION_STRING to run both.)
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
