#!/usr/bin/env python3
"""Google OAuth2 Manager — handles authorization code flow for Google Calendar MCP."""

import json
import os
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Optional

import requests

TOKEN_FILE = "../.tokens/google.json"
REDIRECT_URI = "http://localhost:8888/callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/calendar"


class GoogleOAuthManager:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._auth_code: Optional[str] = None
        self._server: Optional[HTTPServer] = None

    def get_valid_access_token(self) -> str:
        tokens = self._load()
        if not tokens:
            return self._authorize()

        # Google refresh token logic could go here if needed
        # For simplicity, we just authorize if the token is missing or if we want to ensure freshness
        # Since it's a simple setup script, we allow it to fetch anew.
        return tokens.get("access_token", self._authorize())

    def _authorize(self) -> str:
        self._start_callback_server()
        try:
            webbrowser.open(self._auth_url())
            deadline = time.time() + 300
            while not self._auth_code and time.time() < deadline:
                time.sleep(0.5)

            if not self._auth_code:
                raise TimeoutError("No authorization code received within 5 minutes.")

            tokens = self._exchange(self._auth_code)
            self._save(tokens)
            return tokens["access_token"]
        finally:
            self._stop_callback_server()

    def _auth_url(self) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def _exchange(self, code: str) -> dict:
        resp = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        }, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Token exchange failed: {resp.status_code} {resp.text}")
        return resp.json()

    def _save(self, tokens: dict) -> None:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump(tokens, f, indent=2)

    def _load(self) -> Optional[dict]:
        if not os.path.exists(TOKEN_FILE):
            return None
        try:
            with open(TOKEN_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def _start_callback_server(self) -> None:
        mgr = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    mgr._auth_code = params["code"][0]
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body><h1>Google Auth successful!</h1><script>setTimeout(window.close, 3000);</script></body></html>")
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
    from dotenv import load_dotenv
    load_dotenv()
    cid = os.getenv("GOOGLE_CLIENT_ID")
    csec = os.getenv("GOOGLE_CLIENT_SECRET")
    if not cid or not csec:
        raise SystemExit("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env file.")
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    mgr = GoogleOAuthManager(cid, csec)
    mgr._authorize()
