"""Send OTP emails via the Gmail API, as the connected Google account (the admin).

Reuses the OAuth token in ``.tokens/google.json`` — the same connection the
calendar uses — which must additionally carry the ``gmail.send`` scope. Connect or
reconnect Google in Settings (the start-flow now requests ``gmail.send``) and make
sure the **Gmail API** is enabled in the Cloud project. The "From" is therefore
always the connected admin mailbox (``kit.aiss2026@gmail.com``).

Best-effort and self-contained: a tiny token reader/refresher mirrors the calendar
server's, so the API process doesn't depend on an MCP server being up.
"""

from __future__ import annotations

import base64
import json
import os
import time
from email.mime.text import MIMEText
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = _ROOT / ".tokens" / "google.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class EmailError(RuntimeError):
    """Raised when an OTP email could not be sent (surfaced to the client)."""


def _access_token() -> str:
    """Read .tokens/google.json, refreshing the access token if expired."""
    if not TOKEN_FILE.exists():
        return ""
    try:
        tokens = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
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
                upd = {**tokens, **r.json(), "refresh_token": tokens["refresh_token"]}
                upd["expires_at"] = time.time() + int(upd.get("expires_in", 3600))
                TOKEN_FILE.write_text(json.dumps(upd, indent=2), encoding="utf-8")
                return upd.get("access_token", "")
        except requests.RequestException:
            return ""
    return tokens.get("access_token", "")


def email_ready() -> bool:
    """Whether a Google token is present (does not prove the gmail.send scope)."""
    return bool(_access_token())


def send_email(to: str, subject: str, body_text: str, *, from_addr: str | None = None) -> None:
    """Send a plain-text email via Gmail. Raises EmailError on any failure."""
    token = _access_token()
    if not token:
        raise EmailError("Google is not connected — the admin must connect Google (with Gmail) in Settings.")

    msg = MIMEText(body_text, "plain", "utf-8")
    msg["To"] = to
    msg["From"] = from_addr or os.getenv("ADMIN_EMAIL", "kit.aiss2026@gmail.com").strip().lower()
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        resp = requests.post(
            GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"raw": raw}, timeout=20,
        )
    except requests.RequestException as exc:
        raise EmailError(f"Could not reach Gmail: {exc}") from exc

    if resp.status_code == 403:
        raise EmailError(
            "Gmail rejected the send (403) — the connected token lacks the gmail.send "
            "scope or the Gmail API is disabled. Reconnect Google in Settings and enable "
            "the Gmail API in the Cloud project.")
    if resp.status_code == 401:
        raise EmailError("Google token expired/invalid — reconnect Google in Settings.")
    if not resp.ok:
        raise EmailError(f"Gmail API {resp.status_code}: {resp.text[:200]}")


def send_otp_email(to: str, code: str) -> None:
    """Email a login code. Raises EmailError if it can't be sent."""
    body = (
        f"Your FitDash login code is: {code}\n\n"
        "It expires in 10 minutes. If you didn't request this, you can ignore this email.\n"
    )
    send_email(to, "Your FitDash login code", body)
