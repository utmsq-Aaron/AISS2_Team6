#!/usr/bin/env bash
# Wrapper around `docker compose` — use this instead of calling it directly.
#
#   ./docker-up.sh up --build            start the whole stack → http://localhost:3000
#   ./docker-up.sh up -d                 same, detached
#   ./docker-up.sh down                  stop everything
#   ./docker-up.sh logs -f coach-agent   tail one service
#   ./docker-up.sh ps                    what is running
#
# Why a wrapper rather than plain `docker compose`? Three preparation steps that
# Compose cannot do itself:
#
#  1. PORTS. docker-compose.yml's ports are ${VAR:-default} placeholders. Compose
#     resolves ${VAR} at PARSE time from a real env file — it cannot execute code,
#     so it can't ask core/config.py (the actual single source of truth) the way
#     ports.sh and web/vite.config.ts do. This regenerates .ports.generated.env
#     from core/config.py on every run, so a port changed there is picked up with
#     nothing to remember.
#  2. MOUNT DIRS. The state directories are bind-mounted. If they don't exist yet,
#     the Docker daemon creates them ROOT-owned, and everything that later writes
#     to them from the host fails with a permission error.
#  3. AUTH_SECRET. Without a stable one, login tokens are signed with a public
#     dev fallback (forgeable) and every restart logs everyone out.
#
#   PY=/path/to/python3 ./docker-up.sh ps
set -uo pipefail
cd "$(dirname "$0")"

# core/config.py has no third-party imports, so any Python 3 can read it.
PY="${PY:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY="$(command -v python3 || true)"; fi
fi
command -v "$PY" >/dev/null 2>&1 || { echo "✗ python not found (set PY=…)"; exit 1; }

[ -f .env ] || {
  echo "✗ .env is missing — the containers read it via env_file."
  echo "  cp .env.example .env   and fill in at least an LLM key."
  exit 1
}

# 1. Ports, live from core/config.py.
"$PY" scripts/export_ports.py --format dotenv > .ports.generated.env || {
  echo "✗ could not read ports from core/config.py"; exit 1
}

# 2. Bind-mount targets — create them as the current user, before Docker does.
mkdir -p .tokens data .cache .logs .secrets

# 3. A stable token-signing key, persisted so restarts don't log everyone out.
if [ ! -s .secrets/auth_secret ]; then
  (openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n') \
    > .secrets/auth_secret
  echo "→ generated .secrets/auth_secret"
fi
chmod 600 .secrets/auth_secret 2>/dev/null || true
export AUTH_SECRET="${AUTH_SECRET:-$(cat .secrets/auth_secret)}"

exec docker compose --env-file .ports.generated.env "$@"
