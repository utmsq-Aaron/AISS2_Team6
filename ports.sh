#!/usr/bin/env bash
# Single source of truth for the app's ports in shell, sourced by run.sh so a new
# server/agent only needs adding in ONE place. As of this file, that place is
# genuinely singular: FASTAPI_PORT,
# TELEGRAM_MCP_PORT, MCP_SERVERS and AGENT_PORTS below are generated LIVE from
# core/config.py (via scripts/export_ports.py) — not copied by hand — so this
# file cannot drift from the Python registry, or from docker-compose.yml /
# web/vite.config.ts, which read the same generator (see docker-up.sh and
# vite.config.ts's loadPorts()).
#
#   source "$(dirname "$0")/ports.sh"

# Any python3 works — core/config.py has zero third-party dependencies, so this
# doesn't need the project's venv/conda env, just *a* Python 3 interpreter. If
# $PY points somewhere that doesn't exist (e.g. a launcher's conda default on a
# machine without that env), fall back to whatever python3 is on PATH rather
# than failing a read that any interpreter could serve.
_export_ports_py="${PY:-python3}"
command -v "$_export_ports_py" >/dev/null 2>&1 || _export_ports_py=python3
_export_ports_script="$(dirname "${BASH_SOURCE[0]}")/scripts/export_ports.py"

_ports_generated="$("$_export_ports_py" "$_export_ports_script" --format bash)" || {
  echo "✗ could not read ports from core/config.py (via $_export_ports_script)" >&2
  echo "  tried interpreter: $_export_ports_py — set PY=/path/to/python3 if that's wrong." >&2
  return 1 2>/dev/null || exit 1
}
eval "$_ports_generated"
unset _export_ports_py _export_ports_script _ports_generated

# Not part of core/config.py's registry — app code never needs the number (it
# reads the full MLFLOW_TRACKING_URI env, see core/tracing.py); only the
# launchers start/kill the server, so the literal lives here.
MLFLOW_PORT=5001

# Every port the app can claim, flattened — used by `./run.sh stop` to free them
# all. The BFF port (default 3000, see run.sh PORT=) is NOT included here since
# it's caller-configurable, not fixed; run.sh appends it separately.
ALL_PORTS=("$MLFLOW_PORT" "$FASTAPI_PORT" "$TELEGRAM_MCP_PORT")
for _s in "${MCP_SERVERS[@]}"; do ALL_PORTS+=("${_s##*:}"); done
for _a in "${AGENT_PORTS[@]}"; do ALL_PORTS+=("${_a##*:}"); done
unset _s _a
