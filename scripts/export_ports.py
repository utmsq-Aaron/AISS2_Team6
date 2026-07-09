#!/usr/bin/env python3
"""Project core/config.py's port registry into whatever non-Python tooling needs.

core/config.py is the single source of truth for every server/agent port (see
its module docstring). This script is the ONLY place that turns it into a
shape a shell script, Vite (Node), or docker-compose's ${VAR} interpolation
can consume — none of those should ever carry their own copy of a port number.

  python3 scripts/export_ports.py --format bash    # source'd by ports.sh
  python3 scripts/export_ports.py --format json    # read by web/vite.config.ts (execSync)
  python3 scripts/export_ports.py --format dotenv  # read by docker-compose.yml via --env-file
                                                     # (see docker-up.sh — Compose can't run
                                                     # code, so it needs a materialized file)

Has zero third-party dependencies on purpose (core/config.py only uses `os`),
so ANY python3 on PATH can run it — callers don't need the project's venv/conda
env just to read port numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import AGENT_PORTS, FASTAPI_PORT, MCP_PORTS  # noqa: E402

# docker-compose.yml's per-agent env-var naming convention — derived, not listed,
# so a new agent in AGENT_PORTS is exported automatically instead of KeyError-ing.
def _a2a_env_name(agent: str) -> str:
    return f"{agent.upper()}_A2A_PORT"


def _bash() -> str:
    mcp = " ".join(f"{name}:{port}" for name, port in MCP_PORTS.items() if name != "telegram")
    # Specialists before the orchestrator — matches the launcher scripts' documented
    # (non-load-bearing but log-readability-driven) start order. AGENT_PORTS in
    # core/config.py lists "orchestrator" first, so reorder it to last here.
    ordered_agents = [(n, p) for n, p in AGENT_PORTS.items() if n != "orchestrator"]
    ordered_agents.append(("orchestrator", AGENT_PORTS["orchestrator"]))
    agents = " ".join(f"{name}:{port}" for name, port in ordered_agents)
    return (
        f"FASTAPI_PORT={FASTAPI_PORT}\n"
        f"TELEGRAM_MCP_PORT={MCP_PORTS['telegram']}\n"
        f"ORCHESTRATOR_PORT={AGENT_PORTS['orchestrator']}\n"
        f"MCP_SERVERS=({mcp})\n"
        f"AGENT_PORTS=({agents})\n"
    )


def _json() -> str:
    return json.dumps(
        {"FASTAPI_PORT": FASTAPI_PORT, "MCP_PORTS": MCP_PORTS, "AGENT_PORTS": AGENT_PORTS},
        indent=2,
    )


def _dotenv() -> str:
    lines = [f"FASTAPI_PORT={FASTAPI_PORT}"]
    lines += [f"{name.upper()}_MCP_PORT={port}" for name, port in MCP_PORTS.items()]
    lines += [f"{_a2a_env_name(name)}={port}" for name, port in AGENT_PORTS.items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--format", choices=["bash", "json", "dotenv"], required=True)
    args = parser.parse_args()
    sys.stdout.write({"bash": _bash, "json": _json, "dotenv": _dotenv}[args.format]())


if __name__ == "__main__":
    main()
