"""
BaseMCPServer — Abstract base class for all MCP data servers.

Every server in servers/ inherits from this. Guarantees that:
  - .tools        is a list of OpenAI-compatible tool specs
  - ._dispatch()  routes tool calls and returns JSON strings
  - .to_openai_tools() converts specs to OpenAI function-calling format

To add a new data source to the entire system (agent + UI):
  1. Create servers/myserver.py with class MyServer(BaseMCPServer)
  2. Implement list_tools() and call_tool()
  3. Register it in servers/registry.py
  → Done. Agent and UI pick it up automatically.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List
import json


class BaseMCPServer(ABC):
    """Shared contract every MCP server must fulfil."""

    # ── Subclasses must implement these two methods ───────────────────────────

    @abstractmethod
    def list_tools(self) -> List[Dict]:
        """Return the list of tool specs in MCP format.

        Each entry must have at minimum:
            {
                "name": str,
                "description": str,
                "inputSchema": {
                    "type": "object",
                    "properties": { ... },
                    "required": [...]
                }
            }
        """
        ...

    @abstractmethod
    async def call_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Execute one tool call and return a JSON string result.

        Must never raise — return {"error": "..."} on failure.
        """
        ...

    # ── Provided for free by the base class ──────────────────────────────────

    @property
    def tools(self) -> List[Dict]:
        """Cached tool list. Agents and shared.py read this."""
        if not hasattr(self, "_tools_cache"):
            self._tools_cache = self.list_tools()
        return self._tools_cache

    async def _dispatch(self, name: str, args: Dict[str, Any]) -> str:
        """Route a call by name. Validates the tool exists first."""
        valid = {t["name"] for t in self.tools}
        if name not in valid:
            return json.dumps({"error": f"Unknown tool '{name}'. Available: {sorted(valid)}"})
        try:
            return await self.call_tool(name, args)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def to_openai_tools(self) -> List[Dict]:
        """Convert MCP tool specs → OpenAI function-calling format."""
        result = []
        for t in self.tools:
            result.append({
                "type": "function",
                "function": {
                    "name":        t["name"],
                    "description": t.get("description", ""),
                    "parameters":  t.get("inputSchema", {
                        "type": "object", "properties": {}, "required": []
                    }),
                },
            })
        return result

    def tool_names(self) -> set:
        return {t["name"] for t in self.tools}
