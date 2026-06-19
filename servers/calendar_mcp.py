"""Google Calendar — native FastMCP server (Streamable HTTP).

Standardized per the Anthropic/MCP model:
  - Native MCP server (FastMCP) over Streamable HTTP — own vs external servers are
    identical to the host; this one just happens to be ours.
  - Prescriptive tool descriptions so the model picks the right one.
  - Auth is SEPARATE from the tool: the access token is provided to the *server*
    (per-request ``Authorization: Bearer`` header injected by the host's vault, or a
    local token for single-user dev) and used only for the upstream Google call. It
    never enters a tool's arguments or the model's context — the vault pattern.

Scopes: the read tools (``list_calendars`` / ``list_events`` / ``get_event``) need
only ``calendar.readonly``; ``create_event`` writes, so the connected token must
carry ``calendar.events`` (or ``calendar``). The OAuth start-flow requests the
write scope (see ``api/settings_service._GOOGLE_SCOPE``); a read-only token will
get a 403 on create until you reconnect.

Run locally:   python -m servers.calendar_mcp
Endpoint:      http://127.0.0.1:8105/mcp
"""

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

CAL_API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKEN_FILE = Path(".tokens/google.json")          # fixed: project-root relative (branch used ../)
SCOPE_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_EVENTS = "https://www.googleapis.com/auth/calendar.events"  # needed by create_event

HOST = os.getenv("CALENDAR_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("CALENDAR_MCP_PORT", "8105"))

# Default IANA time zone for events the model creates with a naive wall-clock time
# (e.g. "08:00" means 08:00 *here*). Google rejects a dateTime that has neither an
# offset nor a timeZone, so we always supply one. Override with CALENDAR_TZ.
CAL_TZ = os.getenv("CALENDAR_TZ", "Europe/Berlin")

mcp = FastMCP(
    "calendar",
    instructions="Read-only Google Calendar: list calendars, list events in a time range, get one event.",
    host=HOST,
    port=PORT,
    stateless_http=True,
)


# ── Auth (vault pattern) ──────────────────────────────────────────────────────
#
# Single-user dev path: read/refresh a token stored in .tokens/google.json.
# Multi-tenant path: the host injects the user's token as an Authorization header
# on the MCP connection; _bearer_from_request() picks it up. Either way the token
# is never a tool argument and never reaches the model.

def _token_from_file() -> str:
    if not TOKEN_FILE.exists():
        return ""
    try:
        import json
        tokens = json.loads(TOKEN_FILE.read_text())
    except Exception:
        return ""
    # Refresh if expired and we have the bits to do so.
    exp = tokens.get("expires_at")
    expired = exp is None or time.time() >= float(exp) - 60
    if expired and tokens.get("refresh_token") and os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
        try:
            r = requests.post(TOKEN_URL, data={
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            }, timeout=15)
            if r.ok:
                import json
                upd = {**tokens, **r.json(), "refresh_token": tokens["refresh_token"]}
                upd["expires_at"] = time.time() + int(upd.get("expires_in", 3600))
                TOKEN_FILE.write_text(json.dumps(upd, indent=2))
                return upd.get("access_token", "")
        except Exception:
            pass
    return tokens.get("access_token", "")


def _bearer_from_request() -> str:
    """Per-request token from the MCP connection's Authorization header (vault path)."""
    try:
        ctx = mcp.get_context()
        request = ctx.request_context.request           # Starlette Request, if HTTP transport
        auth = request.headers.get("authorization", "") if request else ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
    except Exception:
        pass
    return ""


def _access_token() -> str:
    return _bearer_from_request() or os.getenv("GOOGLE_ACCESS_TOKEN", "") or _token_from_file()


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    token = _access_token()
    if not token:
        return {"error": "Not authorized for Google Calendar. Connect a read-only Google account first."}
    resp = requests.get(f"{CAL_API}{path}", headers={"Authorization": f"Bearer {token}"},
                        params=params or {}, timeout=20)
    if resp.status_code == 401:
        return {"error": "Google Calendar token expired or invalid — reconnect."}
    if not resp.ok:
        return {"error": f"Google Calendar API {resp.status_code}: {resp.text[:200]}"}
    return resp.json() if resp.text else {}


def _post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    token = _access_token()
    if not token:
        return {"error": "Not authorized for Google Calendar. Connect a Google account (with write access) first."}
    resp = requests.post(f"{CAL_API}{path}",
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                         json=body, timeout=20)
    if resp.status_code == 401:
        return {"error": "Google Calendar token expired or invalid — reconnect."}
    if resp.status_code == 403:
        # Almost always an insufficient-scope token: the connected account was
        # authorized read-only. Reconnect to mint a calendar.events token.
        return {"error": "Google Calendar 403 — the connected token is read-only "
                         "(needs the calendar.events scope). Reconnect Google to grant write access. "
                         f"Detail: {resp.text[:160]}"}
    if not resp.ok:
        return {"error": f"Google Calendar API {resp.status_code}: {resp.text[:200]}"}
    return resp.json() if resp.text else {}


def _iso(value: Optional[str]) -> Optional[str]:
    """Accept RFC3339 or YYYY-MM-DD; return a tz-aware RFC3339 timestamp.

    Google's events.list rejects a naive ``timeMin``/``timeMax`` ("Bad Request"),
    so a bare "…T…" with no offset is treated as UTC (append ``Z``).
    """
    if not value:
        return None
    if "T" in value:
        tail = value[11:]  # the part after "YYYY-MM-DDT"
        has_tz = value.endswith("Z") or "+" in tail or "-" in tail
        return value if has_tz else value + "Z"
    from datetime import datetime, timezone
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _event_time(value: str, tz: str) -> Dict[str, str]:
    """Build a Google event start/end object: all-day (date) vs timed (dateTime).

    A timed value is passed as wall-clock + an explicit ``timeZone`` so "08:00"
    lands at 08:00 local. If the value already carries an offset, ``timeZone`` is
    still accepted by Google and is harmless.
    """
    if "T" in value:
        return {"dateTime": value, "timeZone": tz}
    return {"date": value}                  # date-only → all-day event


# ── Tools (read-only) ─────────────────────────────────────────────────────────

@mcp.tool()
def list_calendars(max_results: int = 50) -> Dict[str, Any]:
    """List the calendars on the connected Google account (id, name, primary flag).

    Call this when the user asks which calendars exist, or before listing events from
    a non-primary calendar.
    """
    data = _get("/users/me/calendarList", {"maxResults": max(1, min(int(max_results or 50), 250))})
    if "error" in data:
        return data
    return {"calendars": [
        {"id": i.get("id"), "summary": i.get("summary"), "primary": i.get("primary", False),
         "timeZone": i.get("timeZone"), "accessRole": i.get("accessRole")}
        for i in data.get("items", [])
    ]}


@mcp.tool()
def list_events(
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    calendar_id: str = "primary",
    max_results: int = 20,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """List calendar events in a time window (sorted by start). Read-only.

    Call this whenever the user asks what is on their schedule / calendar / agenda —
    e.g. "what's on Friday?", "am I free tomorrow morning?", "any meetings this week?".
    Compute explicit dates yourself and pass them as time_min / time_max.

    Args:
        time_min: Window start, RFC3339 or YYYY-MM-DD. Defaults to now.
        time_max: Window end, RFC3339 or YYYY-MM-DD.
        calendar_id: Which calendar (default "primary").
        max_results: Max events to return (default 20, max 100).
        query: Optional free-text search.
    """
    from datetime import datetime, timezone
    params: Dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(int(max_results or 20), 100)),
        "timeMin": _iso(time_min) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if time_max:
        params["timeMax"] = _iso(time_max)
    if query:
        params["q"] = query
    data = _get(f"/calendars/{calendar_id}/events", params)
    if "error" in data:
        return data
    return {"events": [
        {"id": i.get("id"), "summary": i.get("summary"), "location": i.get("location"),
         "start": i.get("start"), "end": i.get("end"), "status": i.get("status"),
         "attendees": [a.get("email") for a in i.get("attendees", [])]}
        for i in data.get("items", [])
    ]}


@mcp.tool()
def get_event(event_id: str, calendar_id: str = "primary") -> Dict[str, Any]:
    """Get full details for one event by ID. Read-only.

    Call this after list_events when the user wants more detail on a specific event.
    """
    if not event_id:
        return {"error": "event_id is required"}
    return _get(f"/calendars/{calendar_id}/events/{event_id}")


@mcp.tool()
def create_event(
    summary: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: Optional[str] = None,
    location: Optional[str] = None,
    time_zone: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new calendar event. WRITES to the user's calendar.

    Call this when the user asks to add / schedule / book something — e.g.
    "add a long run Saturday 8am", "block 2 hours tomorrow afternoon", "put my
    race on the calendar". Compute explicit start/end yourself.

    Times: pass a wall-clock "YYYY-MM-DDTHH:MM:SS" for a timed event (interpreted
    in ``time_zone``), or a bare "YYYY-MM-DD" for an all-day event. ``start`` and
    ``end`` must be the same kind (both timed or both all-day).

    Requires a Google token with the calendar.events (write) scope — if the
    account was connected read-only, this returns a 403 asking you to reconnect.

    Args:
        summary: Event title (required).
        start: Start time — "YYYY-MM-DDTHH:MM:SS" (timed) or "YYYY-MM-DD" (all-day).
        end: End time — same kind as start.
        calendar_id: Target calendar (default "primary").
        description: Optional event notes.
        location: Optional location text.
        time_zone: IANA zone for timed events (default server's CALENDAR_TZ).
    """
    if not summary or not start or not end:
        return {"error": "summary, start and end are required"}
    tz = time_zone or CAL_TZ
    body: Dict[str, Any] = {
        "summary": summary,
        "start": _event_time(start, tz),
        "end": _event_time(end, tz),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    data = _post(f"/calendars/{calendar_id}/events", body)
    if "error" in data:
        return data
    return {
        "created": True,
        "id": data.get("id"),
        "summary": data.get("summary"),
        "start": data.get("start"),
        "end": data.get("end"),
        "htmlLink": data.get("htmlLink"),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
