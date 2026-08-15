"""One-off Google *Calendar* authorization for the single-user token path.

Saves the token to ``.tokens/google.json`` — the file ``servers/calendar_mcp.py``
reads (and refreshes) when no per-request Authorization header is present. This
is the CLI twin of the web Settings connect flow (``api/settings_service.py``):
same client, same scopes, same token shape. It exists because the web flow's
redirect URI (``<public base>/api/settings/google/callback``) must be registered
in the Google Cloud OAuth client per deployment, while this flow reuses the
already-registered loopback redirect of the Gmail sender setup
(``http://localhost:8888/callback``), so it works on any machine the client
credentials work on.

Run from the repo root (opens a browser for the consent screen):

    python -m auth calendar
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN_FILE   = Path(".tokens/google.json")        # single-user calendar token
REDIRECT_URI = "http://localhost:8888/callback"    # registered for the gmail flow
AUTH_URL     = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL    = "https://oauth2.googleapis.com/token"
# Same scopes the web Settings flow requests (api/settings_service._GOOGLE_SCOPE):
# read for list_calendars/list_events, events for the agent's write tools.
SCOPE = ("https://www.googleapis.com/auth/calendar.readonly "
         "https://www.googleapis.com/auth/calendar.events")


class CalendarOAuth:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id     = client_id
        self.client_secret = client_secret
        self._auth_code: Optional[str]        = None
        self._server:    Optional[HTTPServer] = None

    def authorize(self) -> None:
        self._start_callback_server()
        try:
            webbrowser.open(self._auth_url())
            print("Browser opened. Waiting for authorization (5-minute timeout)…")
            deadline = time.time() + 300
            while not self._auth_code and time.time() < deadline:
                time.sleep(0.5)
            if not self._auth_code:
                raise TimeoutError("No authorization code received within 5 minutes.")
            tokens = self._exchange(self._auth_code)
            tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600))
            if not tokens.get("refresh_token"):
                print("WARNING: Google returned no refresh_token — the consent screen "
                      "is probably in 'Testing' mode; the token will expire and "
                      "require a reconnect.")
            self._save(tokens)
            print(f"Google Calendar authorized. Token saved to {TOKEN_FILE}.")
        finally:
            self._stop_callback_server()

    def _auth_url(self) -> str:
        params = {
            "client_id":     self.client_id,
            "response_type": "code",
            "redirect_uri":  REDIRECT_URI,
            "scope":         SCOPE,
            "access_type":   "offline",
            "prompt":        "consent",
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def _exchange(self, code: str) -> dict:
        resp = requests.post(TOKEN_URL, data={
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  REDIRECT_URI,
        }, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Token exchange failed: {resp.status_code} {resp.text}")
        return resp.json()

    def _save(self, tokens: dict) -> None:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(tokens, indent=2))

    def _start_callback_server(self) -> None:
        mgr = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    mgr._auth_code = params["code"][0]
                    self.send_response(200)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><head><meta charset='utf-8'></head>"
                        b"<body style='font-family:sans-serif;text-align:center;padding:60px'>"
                        b"<h1 style='color:#34A853'>&#10003; Google Calendar connected!</h1>"
                        b"<p>You can close this window and return to the app.</p>"
                        b"<script>setTimeout(window.close, 3000);</script>"
                        b"</body></html>"
                    )
                else:
                    self.send_error(400, "Invalid callback")

            def log_message(self, *args): pass

        self._server = HTTPServer(("localhost", 8888), _Handler)
        Thread(target=self._server.serve_forever, daemon=True).start()

    def _stop_callback_server(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


if __name__ == "__main__":
    cid  = os.getenv("GOOGLE_CLIENT_ID")
    csec = os.getenv("GOOGLE_CLIENT_SECRET")
    if not cid or not csec:
        raise SystemExit("ERROR: Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env")
    CalendarOAuth(cid, csec).authorize()
