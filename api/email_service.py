"""Send OTP emails via the Gmail API, as the admin Google account.

The email sender uses its OWN token, ``.tokens/google_mail.json`` (scope
``gmail.send``), deliberately SEPARATE from the calendar token
(``.tokens/google.json``). This is so a regular user (re)connecting Google
*Calendar* in Settings can never overwrite or downgrade the admin's email-sending
credential. The admin connects this token via ``python auth/google_oauth.py``
(writes ``google_mail.json``); the **Gmail API** must be enabled in the Cloud
project. The "From" is always the connected admin mailbox (``kit.aiss2026@gmail.com``).

For convenience on an existing deployment, if ``google_mail.json`` is missing but
the legacy ``google.json`` still carries ``gmail.send``, it's copied over once.

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
TOKEN_FILE = _ROOT / ".tokens" / "google_mail.json"          # admin email sender (gmail.send)
_LEGACY_TOKEN_FILE = _ROOT / ".tokens" / "google.json"       # shared calendar token (migrate from)
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class EmailError(RuntimeError):
    """Raised when an OTP email could not be sent (surfaced to the client)."""


def _migrate_legacy_token() -> None:
    """One-time: seed google_mail.json from a legacy google.json that has gmail.send,
    so existing deployments keep sending mail and the email token is then isolated
    from later calendar reconnects."""
    if TOKEN_FILE.exists() or not _LEGACY_TOKEN_FILE.exists():
        return
    try:
        data = json.loads(_LEGACY_TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if "gmail.send" in (data.get("scope") or ""):
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("[email] migrated gmail.send credential → .tokens/google_mail.json", flush=True)


def _access_token() -> str:
    """Read the mail token (google_mail.json), refreshing the access token if expired."""
    _migrate_legacy_token()
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
