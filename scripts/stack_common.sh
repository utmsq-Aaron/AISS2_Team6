#!/usr/bin/env bash
# Shared launcher helpers for FitDash's bash entrypoints.
#
# Source this from serve.sh / dev_stack.sh after defining:
#   - REPO_ROOT (defaults to the current working directory)
#   - PY        (Python interpreter to use)
#   - pids      (array for child PIDs, optional but recommended)
#
# The helpers intentionally do not call exit/cleanup themselves; the caller keeps
# ownership of process lifetime and shutdown behavior.

REPO_ROOT="${REPO_ROOT:-$(pwd)}"

fitdash_resolve_python() {
  local candidate

  if [ -n "${PY:-}" ] && command -v "$PY" >/dev/null 2>&1; then
    echo "$PY"
    return 0
  fi

  candidate="$(command -v python3 2>/dev/null || true)"
  if [ -n "$candidate" ]; then
    echo "$candidate"
    return 0
  fi

  candidate="$(command -v python 2>/dev/null || true)"
  if [ -n "$candidate" ]; then
    echo "$candidate"
    return 0
  fi

  return 1
}

fitdash_port_busy() {
  lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1
}

fitdash_env_has() {
  grep -qE "^$1=[\"']?[A-Za-z0-9]" "$REPO_ROOT/.env" 2>/dev/null
}

fitdash_env_value() {
  local key="$1" line value
  line="$(grep -E "^${key}=" "$REPO_ROOT/.env" 2>/dev/null | tail -n 1 || true)"
  value="${line#*=}"
  value="${value%%#*}"
  value="${value%$'\r'}"
  value="${value#\"}"
  value="${value%\"}"
  value="${value#\'}"
  value="${value%\'}"
  printf '%s' "$value"
}

fitdash_start_mlflow() {
  local port="${MLFLOW_PORT:-5001}"
  if fitdash_port_busy "$port"; then
    echo "✓ MLflow already on :$port"
    return 0
  fi

  echo "→ MLflow on :$port"
  "$PY" -m mlflow server --host 127.0.0.1 --port "$port" \
    --backend-store-uri "sqlite:///mlflow.db" >/tmp/mlflow.log 2>&1 &
  pids+=($!)
  echo $!
}

fitdash_start_mcp_servers() {
  local optional_set="${1:-}"
  local server name port

  for server in "${MCP_SERVERS[@]}"; do
    name="${server%%:*}"
    port="${server##*:}"
    if [[ -n "$optional_set" && " $optional_set " == *" $name "* ]]; then
      continue
    fi
    if fitdash_port_busy "$port"; then
      echo "✓ $name already on :$port"
    else
      echo "→ $name on :$port"
      "$PY" -m "servers.${name}_mcp" >"/tmp/mcp_${name}.log" 2>&1 &
      pids+=($!)
    fi
  done
}

fitdash_build_fitness_index() {
  if "$PY" -c 'import sentence_transformers' >/dev/null 2>&1; then
    echo "→ ensuring fitness RAG index"
    "$PY" -m scripts.build_fitness_index --if-missing \
      || echo "⚠ fitness index unavailable — the fitness agent will degrade gracefully"
  else
    echo "⚠ sentence_transformers missing — skipping fitness index build"
  fi
}

fitdash_start_agents() {
  local name port mod
  for server in "${AGENT_PORTS[@]}"; do
    name="${server%%:*}"
    port="${server##*:}"
    if [ "$name" = "orchestrator" ]; then mod="core.orchestrator_agent"; else mod="agents.${name}_agent"; fi
    if fitdash_port_busy "$port"; then
      echo "✓ agent $name already on :$port"
    else
      echo "→ starting agent $name on :$port  ($mod)"
      "$PY" -m "$mod" >"/tmp/agent_${name}.log" 2>&1 &
      pids+=($!)
    fi
  done
}

fitdash_start_fastapi() {
  local port="${FASTAPI_PORT:-8000}"
  if fitdash_port_busy "$port"; then
    echo "✓ FastAPI already on :$port"
  else
    echo "→ starting FastAPI on :$port"
    "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$port" --reload >/tmp/fitdash_api.log 2>&1 &
    pids+=($!)
  fi
}
