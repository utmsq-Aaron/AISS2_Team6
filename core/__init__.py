"""FitDash core — vendor-neutral, Streamlit-free agent runtime.

Layers:
  - core.config : declarative registry of MCP server connections
  - core.host   : ToolHost — the single MCP client/host (list_tools / call_tool)
  - core.llm    : vendor-neutral LLM seam (provider/model from config)
"""
