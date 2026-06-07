"""Google Calendar tools for HealthBot.

The app uses ``SimpleMCPServer`` in-process. ``GoogleCalendarMCPServer`` remains
available for direct remote MCP experiments.
"""

import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN_FILE = Path(".tokens/google.json")
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_MCP_REQUIRED_SCOPE = "https://www.googleapis.com/auth/calendar"


def _load_tokens() -> Dict[str, Any]:
    if not TOKEN_FILE.exists():
        return {}
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        return {}


def _save_tokens(tokens: Dict[str, Any]) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


def _token_expired(tokens: Dict[str, Any]) -> bool:
    expires_at = tokens.get("expires_at")
    if expires_at is None:
        return bool(tokens.get("refresh_token"))
    try:
        return time.time() >= float(expires_at) - 60
    except (TypeError, ValueError):
        return True


def _refresh_access_token(tokens: Dict[str, Any]) -> str:
    refresh_token = tokens.get("refresh_token")
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not refresh_token or not client_id or not client_secret:
        return tokens.get("access_token", "")

    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Google token refresh failed: {resp.status_code} {resp.text}")

    updated = {**tokens, **resp.json()}
    updated["refresh_token"] = refresh_token
    updated["expires_at"] = time.time() + int(updated.get("expires_in", 3600))
    _save_tokens(updated)
    return updated.get("access_token", "")


def get_google_token() -> str:
    """Return a valid Google access token when one is available."""
    tokens = _load_tokens()
    if not tokens:
        return ""
    if _token_expired(tokens):
        return _refresh_access_token(tokens)
    return tokens.get("access_token", "")


def _has_google_mcp_scope() -> bool:
    scope = _load_tokens().get("scope", "")
    return GOOGLE_MCP_REQUIRED_SCOPE in scope.split()


def _iso_utc(value: Optional[str]) -> Optional[str]:
    """Accept RFC3339 or YYYY-MM-DD and return an API-friendly timestamp."""
    if not value:
        return None
    if "T" in value:
        return value
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class GoogleCalendarAPI:
    """Small REST client for Google Calendar v3."""

    def __init__(self) -> None:
        self._token = ""

    def _headers(self) -> Dict[str, str]:
        self._token = get_google_token()
        if not self._token:
            raise RuntimeError("Google token not found. Run: python auth/google_oauth.py")
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{GOOGLE_CALENDAR_API}{path}"
        resp = requests.request(method, url, headers=self._headers(), timeout=20, **kwargs)
        if resp.status_code == 401:
            tokens = _load_tokens()
            if tokens.get("refresh_token"):
                _refresh_access_token(tokens)
                resp = requests.request(method, url, headers=self._headers(), timeout=20, **kwargs)
        if not resp.ok:
            raise RuntimeError(f"Google Calendar API {resp.status_code}: {resp.text}")
        return resp.json() if resp.text else {}

    async def list_calendars(self, max_results: int = 50) -> Dict[str, Any]:
        data = self._request("GET", "/users/me/calendarList", params={"maxResults": max_results})
        return {
            "calendars": [
                {
                    "id": item.get("id"),
                    "summary": item.get("summary"),
                    "description": item.get("description"),
                    "timeZone": item.get("timeZone"),
                    "primary": item.get("primary", False),
                    "accessRole": item.get("accessRole"),
                }
                for item in data.get("items", [])
            ]
        }

    async def list_events(
        self,
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 20,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(int(max_results or 20), 100)),
            "timeMin": _iso_utc(time_min) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if time_max:
            params["timeMax"] = _iso_utc(time_max)
        if query:
            params["q"] = query
        data = self._request("GET", f"/calendars/{calendar_id}/events", params=params)
        return {
            "events": [
                {
                    "id": item.get("id"),
                    "summary": item.get("summary"),
                    "description": item.get("description"),
                    "location": item.get("location"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "status": item.get("status"),
                    "htmlLink": item.get("htmlLink"),
                    "attendees": item.get("attendees", []),
                }
                for item in data.get("items", [])
            ]
        }

    async def get_event(self, calendar_id: str = "primary", event_id: str = "") -> Dict[str, Any]:
        if not event_id:
            raise ValueError("event_id is required")
        return self._request("GET", f"/calendars/{calendar_id}/events/{event_id}")


google_calendar_api = GoogleCalendarAPI()


class SimpleMCPServer:
    """Local MCP-shaped wrapper backed by the Google Calendar REST API."""

    def __init__(self) -> None:
        self.tools = [
            {
                "name": "google_calendar_list_calendars",
                "description": "List calendars available to the connected Google account.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "max_results": {"type": "integer", "description": "Maximum calendars to return (default 50)."},
                    },
                    "required": [],
                },
            },
            {
                "name": "google_calendar_list_events",
                "description": (
                    "List Google Calendar events. Use calendar_id='primary' unless the user asks "
                    "for a specific calendar. Dates may be RFC3339 timestamps or YYYY-MM-DD."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "calendar_id": {"type": "string", "description": "Calendar ID (default primary)."},
                        "time_min": {"type": "string", "description": "Start time/date, RFC3339 or YYYY-MM-DD. Defaults to now."},
                        "time_max": {"type": "string", "description": "End time/date, RFC3339 or YYYY-MM-DD."},
                        "max_results": {"type": "integer", "description": "Maximum events to return (default 20, max 100)."},
                        "query": {"type": "string", "description": "Optional text search query."},
                    },
                    "required": [],
                },
            },
            {
                "name": "google_calendar_get_event",
                "description": "Get full details for a single Google Calendar event by ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "calendar_id": {"type": "string", "description": "Calendar ID (default primary)."},
                        "event_id": {"type": "string", "description": "Google Calendar event ID."},
                    },
                    "required": ["event_id"],
                },
            },
        ]

    async def initialize(self) -> None:
        get_google_token()

    async def _dispatch(self, name: str, args: dict) -> str:
        try:
            args = args or {}
            if name == "google_calendar_list_calendars":
                result = await google_calendar_api.list_calendars(args.get("max_results", 50))
            elif name == "google_calendar_list_events":
                result = await google_calendar_api.list_events(
                    calendar_id=args.get("calendar_id", "primary"),
                    time_min=args.get("time_min"),
                    time_max=args.get("time_max"),
                    max_results=args.get("max_results", 20),
                    query=args.get("query"),
                )
            elif name == "google_calendar_get_event":
                result = await google_calendar_api.get_event(
                    calendar_id=args.get("calendar_id", "primary"),
                    event_id=args.get("event_id", ""),
                )
            else:
                result = {"error": f"Unknown Google Calendar tool: {name}"}
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

class GoogleCalendarMCPServer:
    """Wrapper that holds a remote MCP connection to Google's Calendar MCP server."""

    def __init__(self):
        self.endpoint = "https://calendarmcp.googleapis.com/mcp/v1"
        self.tools: List[Dict] = []
        self._session = None
        self._exit_stack = None

    async def _open_session(self, exit_stack):
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        token = get_google_token()
        if not token:
            raise RuntimeError("No Google token found. Google Calendar features disabled.")

        http_client = await exit_stack.enter_async_context(
            create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        )
        read_stream, write_stream, _get_session_id = await exit_stack.enter_async_context(
            streamable_http_client(self.endpoint, http_client=http_client)
        )
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        return session

    def _normalize_args(self, name: str, args: dict) -> dict:
        """Accept legacy local-wrapper argument names for hosted Google MCP tools."""
        args = dict(args or {})
        if name == "list_events":
            aliases = {
                "calendar_id": "calendarId",
                "time_min": "startTime",
                "time_max": "endTime",
                "max_results": "pageSize",
                "query": "fullText",
            }
            for old, new in aliases.items():
                if old in args and new not in args:
                    args[new] = args.pop(old)
        elif name == "get_event":
            aliases = {"calendar_id": "calendarId", "event_id": "eventId"}
            for old, new in aliases.items():
                if old in args and new not in args:
                    args[new] = args.pop(old)
        elif name == "list_calendars" and "max_results" in args and "pageSize" not in args:
            args["pageSize"] = args.pop("max_results")
        return args

    async def initialize(self):
        from contextlib import AsyncExitStack

        try:
            print(f"Initializing hosted Google Calendar MCP server at {self.endpoint}")
            async with AsyncExitStack() as exit_stack:
                session = await self._open_session(exit_stack)
                remote_tools = await session.list_tools()
            self.tools = [
                {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                for t in getattr(remote_tools, "tools", [])
            ]
            print(
                "Initialized hosted Google Calendar MCP server with tools: "
                + ", ".join(tool["name"] for tool in self.tools)
            )
        except Exception as e:
            self._exit_stack = None
            self._session = None
            self.tools = []
            print(f"Failed to initialize Google Calendar MCP at {self.endpoint}: {e}")

    async def _dispatch(self, name: str, args: dict) -> str:
        from contextlib import AsyncExitStack

        if not self.tools:
            await self.initialize()

        if not self.tools:
            return json.dumps({"error": "Google Calendar Server not initialized."})

        try:
            if not _has_google_mcp_scope():
                return json.dumps({
                    "error": (
                        "Google Calendar MCP permission denied: token was authorized with "
                        "calendar.readonly, but the hosted Google MCP server requires the full "
                        "calendar scope. Re-run: python auth/google_oauth.py"
                    )
                })
            args = self._normalize_args(name, args)
            print(f"Calling hosted Google Calendar MCP tool {name} with args: {json.dumps(args)}")
            async with AsyncExitStack() as exit_stack:
                session = await self._open_session(exit_stack)
                result = await session.call_tool(name, arguments=args)
            content = [getattr(c, 'text', '') for c in result.content if getattr(c, 'type', '') == 'text']
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                return json.dumps(structured)
            return json.dumps({"result": "\n".join(content) if content else "Success"})
        except Exception as e:
            print(f"Hosted Google Calendar MCP tool {name} failed: {type(e).__name__}: {e!r}")
            return json.dumps({"error": str(e) or type(e).__name__})
