"""FitDash HTTP API — a thin FastAPI seam over the Streamlit-free core.

Exposes ToolHost (list_tools / call_tool), the FitDashOrchestrator (chat, SSE),
LLM-generated charts, settings, and the Garmin→Strava sync to the React + Node
frontend. The brain stays in Python: this layer only adapts core/ to HTTP/SSE.
"""
