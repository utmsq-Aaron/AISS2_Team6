"""Email + OTP authentication for the prototype.

Login *and* registration are by email: request a one-time code (emailed from the
admin Gmail via ``api/email_service``), enter it, and you're in. The first valid
code for a new email **creates** that account on this machine
(``data/accounts.json``). Identity only — everyone shares the same Strava/Garmin
data; the token says *who* is asking and whether they're the **admin** (only the
admin may open Settings).

Tokens are HMAC-signed ``payload.signature`` over ``{email, exp}`` (key =
``AUTH_SECRET``), so there's no server-side session table and they expire on their
own. OTPs live in memory with an expiry plus attempt/resend rate limits. The admin
is ``ADMIN_EMAIL`` (default ``kit.aiss2026@gmail.com``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, Header, HTTPException

_ROOT = Path(__file__).resolve().parent.parent
_ACCOUNTS_FILE = _ROOT / "data" / "accounts.json"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TOKEN_TTL = 30 * 24 * 60 * 60   # signed-token lifetime (30 days)
OTP_TTL = 10 * 60               # a code is valid for 10 minutes
OTP_MAX_ATTEMPTS = 5            # wrong guesses before a code is burned
OTP_RESEND_COOLDOWN = 30        # seconds between code requests for one email
OTP_MAX_PER_HOUR = 8           # codes per email per rolling hour

_otps: dict[str, dict] = {}     # email -> {code, exp, attempts, sent:[ts,…]}
_otp_lock = threading.Lock()
_acct_lock = threading.Lock()


# ── config ────────────────────────────────────────────────────────────────────

def admin_email() -> str:
    return os.getenv("ADMIN_EMAIL", "kit.aiss2026@gmail.com").strip().lower()


def is_admin(email: str) -> bool:
    return (email or "").strip().lower() == admin_email()


def normalize_email(raw: str) -> str | None:
    e = (raw or "").strip().lower()
    return e if _EMAIL_RE.match(e) else None


# ── account store (data/accounts.json) ────────────────────────────────────────

def _load_accounts() -> dict:
    try:
        return json.loads(_ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_accounts(d: dict) -> None:
    _ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ACCOUNTS_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def is_registered(email: str) -> bool:
    return email in _load_accounts()


def register_or_touch(email: str) -> bool:
    """Ensure an account row exists; stamp last_login. Returns True if newly created."""
    now = datetime.now(timezone.utc).isoformat()
    with _acct_lock:
        d = _load_accounts()
        new = email not in d
        if new:
            d[email] = {"created_at": now, "last_login": now}
        else:
            d[email]["last_login"] = now
        _save_accounts(d)
    return new


# ── token (stateless, signed) ─────────────────────────────────────────────────

def _secret() -> bytes:
    return os.getenv("AUTH_SECRET", "fitdash-dev-secret").encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())


def issue_token(email: str) -> str:
    payload = _b64(json.dumps({"e": email, "exp": int(time.time()) + TOKEN_TTL},
                              separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        obj = json.loads(_unb64(payload))
    except (ValueError, UnicodeDecodeError):
        return None
    if int(obj.get("exp", 0)) < time.time():
        return None
    email = obj.get("e")
    return email if isinstance(email, str) and _EMAIL_RE.match(email) else None


# ── OTP issuance / verification ───────────────────────────────────────────────

def request_otp(email: str) -> tuple[str, bool]:
    """Mint a 6-digit code for ``email``. Returns (code, is_new_account).

    Raises HTTPException(429) when the per-email resend cooldown or hourly cap hit.
    """
    now = time.time()
    with _otp_lock:
        rec = _otps.get(email) or {"code": None, "exp": 0.0, "attempts": 0, "sent": []}
        sent = [t for t in rec["sent"] if now - t < 3600]
        if sent and now - sent[-1] < OTP_RESEND_COOLDOWN:
            wait = int(OTP_RESEND_COOLDOWN - (now - sent[-1]))
            raise HTTPException(429, f"Please wait {wait}s before requesting another code.")
        if len(sent) >= OTP_MAX_PER_HOUR:
            raise HTTPException(429, "Too many codes requested for this email. Try again later.")
        code = f"{secrets.randbelow(10**6):06d}"
        _otps[email] = {"code": code, "exp": now + OTP_TTL, "attempts": 0, "sent": sent + [now]}
    return code, not is_registered(email)


def verify_otp(email: str, code: str) -> bool:
    """True iff ``code`` matches the live OTP for ``email``. Burns the code on success
    or after too many wrong attempts."""
    now = time.time()
    with _otp_lock:
        rec = _otps.get(email)
        if not rec or not rec["code"] or rec["exp"] < now:
            return False
        if rec["attempts"] >= OTP_MAX_ATTEMPTS:
            _otps.pop(email, None)
            return False
        rec["attempts"] += 1
        if hmac.compare_digest(rec["code"], str(code).strip()):
            _otps.pop(email, None)
            return True
        return False


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def current_user(authorization: str = Header(default="")) -> str:
    """Require a valid ``Authorization: Bearer <token>`` — returns the user's email."""
    prefix = "bearer "
    token = authorization[len(prefix):].strip() if authorization.lower().startswith(prefix) else ""
    email = verify_token(token) if token else None
    if email is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return email


def require_admin(user: str = Depends(current_user)) -> str:
    """Require the authenticated user to be the admin (gates Settings)."""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only.")
    return user
