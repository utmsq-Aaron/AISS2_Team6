"""Recovery Agent — A2A server :9001.

A LangGraph ReAct agent scoped to the Garmin MCP server. Analyses sleep, HRV,
Body Battery and stress to judge recovery and readiness. Run standalone:

    python -m agents.recovery_agent
"""

from agents._base_executor import run_specialist

if __name__ == "__main__":
    run_specialist("recovery")
