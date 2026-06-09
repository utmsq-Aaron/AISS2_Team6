# FitDash — Architektur

> Dieses Dokument ist ersetzt durch **[`docs/mcp-architecture.md`](docs/mcp-architecture.md)**.
>
> Die aktuelle Architektur basiert auf nativen **FastMCP-Servern** über Streamable HTTP,
> einem uniformen **MCP-Client** (`core/host.ToolHost`) und einem **tool-agnostischen
> Tool-Use-Loop** (`core/orchestrator`). Neuen Server anlegen: `servers/*_mcp.py`-Muster.
