"""Route Agent — A2A server :9004.

LangGraph ReAct agent scoped to the Routes (OpenRouteService) MCP server. Plans
point-to-point and circular routes, finds trails, and computes isochrones. Run:

    python -m agents.route_agent
"""

from agents._base_executor import run_specialist

if __name__ == "__main__":
    run_specialist("route")
