"""Training-Load Agent — A2A server :9002.

LangGraph ReAct agent scoped to the Strava + Garmin MCP servers. Quantifies
training load (CTL/ATL/TSB), volume/trends, activity detail, PRs and stats, and
serves GPS maps of recorded activities. Run standalone:

    python -m agents.load_agent
"""

from agents._base_executor import run_specialist

if __name__ == "__main__":
    run_specialist("load")
