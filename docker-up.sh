#!/usr/bin/env bash
# Thin wrapper around `docker compose` — use this instead of calling `docker
# compose` directly.
#
# docker-compose.yml's ports are ${VAR:-default} placeholders (see its header):
# the defaults match core/config.py, but Compose resolves ${VAR} substitution
# from a real env file at PARSE time — it cannot execute code, so it can't ask
# core/config.py (the actual single source of truth) for the current values the
# way ports.sh or web/vite.config.ts do. This script closes that gap: it
# regenerates .ports.generated.env from core/config.py (via
# scripts/export_ports.py) on every run, then passes it to Compose via
# --env-file — so a port changed in core/config.py is picked up automatically,
# with no manual step and nothing to forget.
#
#   ./docker-up.sh up --build weather-mcp routes-mcp strava-mcp garmin-mcp calendar-mcp
#   PY=/path/to/python3 ./docker-up.sh ps
set -uo pipefail
cd "$(dirname "$0")"

PY="${PY:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "✗ python not found at $PY (set PY=…)"; exit 1; }

"$PY" scripts/export_ports.py --format dotenv > .ports.generated.env || {
  echo "✗ could not read ports from core/config.py"; exit 1;
}

exec docker compose --env-file .ports.generated.env "$@"
