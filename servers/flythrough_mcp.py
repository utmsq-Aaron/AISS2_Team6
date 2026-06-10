"""Flythrough — native FastMCP server for 3D GPS flythrough render requests.

Returns a show_flythrough action payload that tells the UI which activity to
render and with which parameters. Rendering (Playwright + MapLibre) happens
at the output layer — Streamlit for the web UI, Telegram bridge for chat.
This server handles only parameter validation and packaging.

Run locally:   python -m servers.flythrough_mcp
Endpoint:      http://127.0.0.1:8107/mcp   (override via FLYTHROUGH_MCP_PORT)
"""

import os
import sys
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

HOST = os.getenv("FLYTHROUGH_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("FLYTHROUGH_MCP_PORT", "8107"))

mcp = FastMCP(
    "flythrough",
    instructions=(
        "3D cinematic GPS flythrough: prepare a render request for a Strava activity. "
        "Use prepare_flythrough ONLY after the user has confirmed orientation, map style, "
        "and duration — and you have a concrete activity_id from a Strava tool call."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
)

_VALID_ORIENTATIONS = {"landscape", "portrait"}
_VALID_MODES = {"satellite_3d", "dark_3d", "satellite_flat"}
_VALID_RESOLUTIONS = {"HD", "2K", "4K"}


@mcp.tool()
def prepare_flythrough(
    activity_id: int,
    orientation: str,
    mode: str,
    duration_sec: int,
    activity_name: Optional[str] = None,
    resolution: str = "2K",
) -> Dict[str, Any]:
    """Prepare a 3D cinematic GPS flythrough for a Strava activity.

    Returns a render payload the UI turns into an MP4. The video shows only
    the animated GPS route over a map — no stats, no overlays.

    STRICT WORKFLOW — follow exactly:
    1. You must have a concrete activity_id from strava__get_activities or
       strava__get_activity_detail. If you don't have one, call those first.
    2. Ask the user to confirm ALL THREE before calling this tool:
       - ORIENTATION: 'landscape' (16:9, widescreen) or 'portrait' (9:16, phone)
       - MAP STYLE: 'satellite_3d' (real terrain + imagery) or 'dark_3d' (dark minimal)
       - DURATION: seconds between 30 and 120
    3. Only call once all three are confirmed.

    Args:
        activity_id: Strava numeric activity ID (required — must be known).
        orientation: 'landscape' (default) or 'portrait'.
        mode: 'satellite_3d' (default), 'dark_3d', or 'satellite_flat'.
        duration_sec: Video length in seconds (30–120).
        activity_name: Display name for the activity (pass if known).
        resolution: 'HD', '2K' (default), or '4K'. Only change when user asks.
    """
    if orientation not in _VALID_ORIENTATIONS:
        return {"error": f"orientation must be one of {sorted(_VALID_ORIENTATIONS)}"}
    if mode not in _VALID_MODES:
        return {"error": f"mode must be one of {sorted(_VALID_MODES)}"}
    if resolution not in _VALID_RESOLUTIONS:
        return {"error": f"resolution must be one of {sorted(_VALID_RESOLUTIONS)}"}

    duration_sec = max(30, min(120, int(duration_sec)))

    return {
        "action":        "show_flythrough",
        "activity_id":   int(activity_id),
        "activity_name": (activity_name or f"Activity {activity_id}").strip(),
        "mode":          mode,
        "duration_sec":  duration_sec,
        "orientation":   orientation,
        "resolution":    resolution,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
