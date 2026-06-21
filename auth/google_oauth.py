#!/usr/bin/env python3
"""Google OAuth2 — one-time authorization for Google Calendar access.

Run once from the project root:
    python auth/google_oauth.py

Saves tokens to .tokens/google.json. After a successful run, the
calendar MCP server (servers/calendar_mcp.py) loads the token automatically.
"""

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

TOKEN_FILE   = Path(".tokens/google.json")
REDIRECT_URI = "http://localhost:8888/callback"
AUTH_URL     = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL    = "https://oauth2.googleapis.com/token"
SCOPE        = ("https://www.googleapis.com/auth/calendar.readonly "
                "https://www.googleapis.com/auth/calendar.events "
                "https://www.googleapis.com/auth/gmail.send")  # calendar r/w + send OTP mail


class GoogleOAuthManager:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id     = client_id
        self.client_secret = client_secret
        self._auth_code: Optional[str]    = None
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
                        b"<h1 style='color:#4285F4'>&#10003; Google Calendar verbunden!</h1>"
                        b"<p>Du kannst dieses Fenster schlie&szlig;en und zur App zur&uuml;ckkehren.</p>"
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
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    GoogleOAuthManager(cid, csec).authorize()
