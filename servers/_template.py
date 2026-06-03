"""
TEMPLATE für einen neuen MCP-Server.

Kopiere diese Datei, passe sie an, registriere sie in servers/registry.py.
Mehr ist nicht nötig — der Agent und die UI entdecken den Server automatisch.

Checkliste:
  [ ] Datei umbenennen: servers/myserver.py
  [ ] Klasse umbenennen: MyMCPServer
  [ ] list_tools() mit echten Tool-Specs füllen
  [ ] call_tool() mit echter Logik füllen
  [ ] In servers/registry.py eintragen
"""

import json
from typing import Any, Dict, List

from servers._base_server import BaseMCPServer


class TemplateMCPServer(BaseMCPServer):

    def list_tools(self) -> List[Dict]:
        return [
            {
                "name": "my_tool",
                "description": "Was dieses Tool macht — je konkreter desto besser für den Agenten.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "param_a": {
                            "type": "string",
                            "description": "Was dieser Parameter bedeutet"
                        },
                        "param_b": {
                            "type": "integer",
                            "description": "Zweiter Parameter",
                            "default": 10
                        },
                    },
                    "required": ["param_a"],
                },
            },
            # weitere Tools hier...
        ]

    async def call_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "my_tool":
            return await self._my_tool(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    # ── Private Implementierungen ─────────────────────────────────────────────

    async def _my_tool(self, args: Dict) -> str:
        param_a = args["param_a"]
        param_b = args.get("param_b", 10)
        # ... echte Logik hier ...
        return json.dumps({"result": f"{param_a} x {param_b}"})
