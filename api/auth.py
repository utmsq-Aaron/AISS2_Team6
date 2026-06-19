"""Quasi user login for the prototype — stateless, name-based Bearer tokens.

There is no real user store: a fixed roster of names is the whole "directory".
Logging in = POST a known name, get back an opaque Bearer token. The token is a
signed `payload.signature` pair (HMAC-SHA256 over the canonical name), so it needs
no server-side session table and survives API restarts. Identity only — every user
sees the same shared Strava/Garmin data; the token just says *who* is asking.

Not security: the signing secret defaults to a dev constant and tokens never
expire. It exists so the demo has a login flow and a real Authorization header,
not to protect anything.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from fastapi import Header, HTTPException

# The prototype's entire "user directory".
USERS: list[str] = ["Marvin", "Max", "Lorenz", "Aaron", "Simon"]
_BY_LOWER: dict[str, str] = {u.lower(): u for u in USERS}


def _secret() -> bytes:
    # Re-read per call so an env change applies without restarting the API.
    return os.getenv("AUTH_SECRET", "fitdash-dev-secret").encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())


def canonical_user(name: str) -> str | None:
    """Map any-case input to the roster's canonical spelling, or None if unknown."""
    return _BY_LOWER.get(name.strip().lower())


def issue_token(name: str) -> str | None:
    """Mint a Bearer token for a known user (case-insensitive), else None."""
    user = canonical_user(name)
    if user is None:
        return None
    payload = _b64(user.encode())
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """Return the canonical user for a valid token, else None."""
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        user = _unb64(payload).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    return user if user in USERS else None


def current_user(authorization: str = Header(default="")) -> str:
    """FastAPI dependency: require a valid `Authorization: Bearer <token>` header."""
    prefix = "bearer "
    token = authorization[len(prefix):].strip() if authorization.lower().startswith(prefix) else ""
    user = verify_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
