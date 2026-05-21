#!/usr/bin/env python3
"""Strava OAuth2 Manager — handles the full authorization code flow and token refresh."""

import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Optional

import requests

TOKEN_FILE = ".tokens/strava.json"
REDIRECT_URI = "http://localhost:8080/callback"
AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
SCOPE = "read,activity:read_all,activity:write"
TOKEN_REFRESH_BUFFER_SECONDS = 300  # refresh 5 min before expiry


class OAuth2Manager:
    """Manages Strava OAuth2 tokens: first-time authorization, persistence, and auto-refresh."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._state = secrets.token_urlsafe(32)
        self._auth_code: Optional[str] = None
        self._server: Optional[HTTPServer] = None

    # ── Public interface ──────────────────────────────────────────────────────

    def get_valid_access_token(self) -> str:
        """Return a valid access token, refreshing or re-authorizing as needed."""
        tokens = self._load()
        if not tokens:
            return self._authorize()

        expires_at = tokens.get("expires_at", 0)
        if time.time() >= expires_at - TOKEN_REFRESH_BUFFER_SECONDS:
            print("Access token expired — refreshing...")
            try:
                tokens = self._refresh(tokens["refresh_token"])
                self._save(tokens)
                return tokens["access_token"]
            except Exception as e:
                print(f"Token refresh failed ({e}) — re-authorizing...")
                return self._authorize()

        return tokens["access_token"]

    # ── Authorization flow ────────────────────────────────────────────────────

    def _authorize(self) -> str:
        """Run the full OAuth2 browser flow and return a fresh access token."""
        print("Starting Strava OAuth2 authorization...")
        self._start_callback_server()
        try:
            webbrowser.open(self._auth_url())
            print("Browser opened — please authorize the app.")
            print("Waiting for callback (timeout: 5 minutes)...")

            deadline = time.time() + 300
            while not self._auth_code and time.time() < deadline:
                time.sleep(0.5)

            if not self._auth_code:
                raise TimeoutError("No authorization code received within 5 minutes.")

            tokens = self._exchange(self._auth_code)
            self._save(tokens)
            print("Authorization successful — tokens saved.")
            return tokens["access_token"]
        finally:
            self._stop_callback_server()

    def _auth_url(self) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "approval_prompt": "force",
            "scope": SCOPE,
            "state": self._state,
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    # ── Token exchange & refresh ──────────────────────────────────────────────

    def _exchange(self, code: str) -> dict:
        resp = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
        })
        if resp.status_code != 200:
            raise RuntimeError(f"Token exchange failed: {resp.status_code} — {resp.text}")
        return resp.json()

    def _refresh(self, refresh_token: str) -> dict:
        resp = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        if resp.status_code != 200:
            raise RuntimeError(f"Token refresh failed: {resp.status_code} — {resp.text}")
        return resp.json()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self, tokens: dict) -> None:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump(tokens, f, indent=2)
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass  # Windows doesn't support Unix permissions

    def _load(self) -> Optional[dict]:
        if not os.path.exists(TOKEN_FILE):
            return None
        try:
            with open(TOKEN_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    # ── Callback server ───────────────────────────────────────────────────────

    def _start_callback_server(self) -> None:
        mgr = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params and params.get("state", [""])[0] == mgr._state:
                    mgr._auth_code = params["code"][0]
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"""
                        <html><body>
                        <h1>Authorization successful!</h1>
                        <p>You can close this window and return to the app.</p>
                        <script>setTimeout(window.close, 3000);</script>
                        </body></html>
                    """)
                else:
                    self.send_error(400, "Invalid callback")

            def log_message(self, *args):
                pass  # suppress server logs

        self._server = HTTPServer(("localhost", 8080), _Handler)
        Thread(target=self._server.serve_forever, daemon=True).start()

    def _stop_callback_server(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    cid = os.getenv("CLIENT_ID")
    csec = os.getenv("CLIENT_SECRET")
    if not cid or not csec:
        raise SystemExit("Set CLIENT_ID and CLIENT_SECRET in your .env file.")
    # Always force a fresh authorization when run directly so scope changes take effect.
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
        print("Removed existing token — starting fresh authorization...")
    mgr = OAuth2Manager(cid, csec)
    token = mgr._authorize()
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.ok:
        a = resp.json()
        print(f"Logged in as: {a['firstname']} {a['lastname']}")
    else:
        print(f"API test failed: {resp.status_code}")
