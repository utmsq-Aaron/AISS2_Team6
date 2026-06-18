"""Context Agent — A2A server :9003.

LangGraph ReAct agent scoped to the Weather + Calendar MCP servers. Combines
forecast with calendar availability to surface trainable time windows. Run:

    python -m agents.context_agent
"""

from agents._base_executor import run_specialist

if __name__ == "__main__":
    run_specialist("context")
