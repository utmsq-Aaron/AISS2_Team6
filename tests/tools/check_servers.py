"""Show which MCP servers are answering on their ports right now.

Ports come from core.config.MCP_SERVERS — the single source of truth — so a
newly added server shows up here without touching this file.
"""

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # noqa: E402

from core.config import MCP_SERVERS  # noqa: E402

for name, url in MCP_SERVERS.items():
    parsed = urlparse(url)
    where = f"{parsed.hostname}:{parsed.port}"
    try:
        # Any HTTP status proves something is listening and speaking HTTP; the
        # MCP handshake itself is ToolHost's job, not this probe's.
        r = requests.get(url, timeout=2)
        print(f"{name:<12} {where:<22} -> {r.status_code}")
    except Exception:
        print(f"{name:<12} {where:<22} -> DOWN")
