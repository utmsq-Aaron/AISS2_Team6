"""Auth endpoints — email + OTP login/registration.

Mounted WITHOUT the Bearer guard (you can't be logged in yet when you log in).
Flow: POST /auth/request-otp {email} → a code is emailed; POST /auth/verify-otp
{email, code} → on success you're registered (if new) and get a Bearer token.

Set OTP_DEV_ECHO=1 to also log the code to the server console (local testing
without a working Gmail connection). Never enable that on a public deployment.
"""

import os

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from api import auth as A
from api import email_service as mail
from api.auth import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_secure() -> bool:
    """Whether to set the Secure flag on the session cookie. Read from
    COOKIE_SECURE (default off) rather than sniffed from the request scheme —
    FastAPI sits behind the Node BFF over plain http and can't infer https."""
    return os.getenv("COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes")


class EmailRequest(BaseModel):
    email: str


class VerifyRequest(BaseModel):
    email: str
    code: str


class TokenResponse(BaseModel):
    token: str
    user: str
    is_admin: bool
    new_account: bool


def _dev_echo() -> bool:
    return os.getenv("OTP_DEV_ECHO", "0").strip().lower() in ("1", "true", "yes")


@router.post("/request-otp")
def request_otp(req: EmailRequest) -> dict:
    email = A.normalize_email(req.email)
    if email is None:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    code, new_account = A.request_otp(email)  # raises 429 on rate limit

    try:
        mail.send_otp_email(email, code)
    except mail.EmailError as exc:
        if _dev_echo():
            print(f"[auth] OTP for {email}: {code}  (email send failed: {exc})", flush=True)
            return {"ok": True, "new_account": new_account, "dev_echo": True}
        raise HTTPException(status_code=502, detail=str(exc))

    if _dev_echo():
        print(f"[auth] OTP for {email}: {code}", flush=True)
    return {"ok": True, "new_account": new_account}


@router.post("/verify-otp", response_model=TokenResponse)
def verify_otp(req: VerifyRequest, response: Response) -> TokenResponse:
    email = A.normalize_email(req.email)
    if email is None:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if not A.verify_otp(email, req.code):
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    new_account = A.register_or_touch(email)
    token = A.issue_token(email)

    # Primary auth for the browser: the SAME signed token as an httpOnly cookie so
    # JS can't read it (an XSS can no longer steal a durable 30-day credential from
    # localStorage). SameSite=Lax blocks cross-site CSRF; Secure comes from
    # COOKIE_SECURE (default off — the app sits behind the BFF over plain http in
    # dev). We STILL return the token in the JSON body below for non-browser
    # clients and as an instant frontend-only rollback path.
    response.set_cookie(
        A.SESSION_COOKIE,
        token,
        max_age=A.TOKEN_TTL,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    return TokenResponse(
        token=token,
        user=email,
        is_admin=A.is_admin(email),
        new_account=new_account,
    )


@router.post("/logout")
def logout(response: Response) -> dict:
    """Clear the session cookie. PUBLIC (no auth guard) so an already-expired or
    otherwise-unauthenticated session can still clear its stale cookie."""
    response.delete_cookie(A.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(current_user)) -> dict:
    """Echo the authenticated user — the frontend uses this to validate a stored token."""
    return {"user": user, "is_admin": A.is_admin(user)}
