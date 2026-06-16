"""Process-wide singletons for the API layer.

Mirrors the Streamlit `@st.cache_resource` singletons: one ToolHost (MCP client)
and one orchestrator for the whole process. The orchestrator's tool-use loop and
ToolHost are not assumed thread-safe, so chat/sync runs are serialized with a lock
— the same precaution telegram_bridge.py takes.
"""

import threading
from functools import lru_cache

from core.host import ToolHost
from core.orchestrator import FitDashOrchestrator


@lru_cache(maxsize=1)
def get_host() -> ToolHost:
    """The single MCP client used by the direct-data endpoints (/api/tools)."""
    return ToolHost()


@lru_cache(maxsize=1)
def get_orchestrator() -> FitDashOrchestrator:
    """The single tool-use engine driving /api/chat."""
    return FitDashOrchestrator()


# Serializes orchestrator.run() / long sync exports — ToolHost is shared and not
# assumed thread-safe (a single-user dashboard has trivial concurrency anyway).
orchestrator_lock = threading.Lock()
