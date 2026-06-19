"""Auth endpoints — name-based login that returns a Bearer token (see api/auth.py).

These routes are mounted WITHOUT the `current_user` dependency (you can't be
logged in yet when you log in). Every other /api router is gated on the token.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import USERS, canonical_user, current_user, issue_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    name: str


class LoginResponse(BaseModel):
    token: str
    user: str


@router.get("/users")
def list_users() -> dict[str, list[str]]:
    """The known account names — used by the login screen to offer quick picks."""
    return {"users": USERS}


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest) -> LoginResponse:
    user = canonical_user(req.name)
    if user is None:
        raise HTTPException(status_code=401, detail=f"Unknown user '{req.name.strip()}'.")
    token = issue_token(user)
    assert token is not None  # canonical_user already validated membership
    return LoginResponse(token=token, user=user)


@router.get("/me")
def me(user: str = Depends(current_user)) -> dict[str, str]:
    """Echo the authenticated user — the frontend uses this to validate a stored token."""
    return {"user": user}
