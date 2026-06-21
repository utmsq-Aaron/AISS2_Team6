"""Auth endpoints — email + OTP login/registration.

Mounted WITHOUT the Bearer guard (you can't be logged in yet when you log in).
Flow: POST /auth/request-otp {email} → a code is emailed; POST /auth/verify-otp
{email, code} → on success you're registered (if new) and get a Bearer token.

Set OTP_DEV_ECHO=1 to also log the code to the server console (local testing
without a working Gmail connection). Never enable that on a public deployment.
"""

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import auth as A
from api import email_service as mail
from api.auth import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


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
def verify_otp(req: VerifyRequest) -> TokenResponse:
    email = A.normalize_email(req.email)
    if email is None:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if not A.verify_otp(email, req.code):
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    new_account = A.register_or_touch(email)
    return TokenResponse(
        token=A.issue_token(email),
        user=email,
        is_admin=A.is_admin(email),
        new_account=new_account,
    )


@router.get("/me")
def me(user: str = Depends(current_user)) -> dict:
    """Echo the authenticated user — the frontend uses this to validate a stored token."""
    return {"user": user, "is_admin": A.is_admin(user)}
