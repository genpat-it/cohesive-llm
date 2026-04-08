"""Auth endpoints: login, logout, me."""
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.db_models import User
from app.services.auth import (
    COOKIE_NAME,
    JWT_EXPIRE_HOURS,
    create_access_token,
    get_current_user,
    verify_password,
)
from app.services.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

# Login rate limit (per real client IP).
# Configurable via env so prod can tighten or loosen it.
LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")

# Cookie settings driven by env so we don't break HTTP dev:
#   COOKIE_SECURE=true   → cookie sent only over HTTPS (production)
#   COOKIE_PATH=/llm     → scope cookie to a sub-path when behind a path-prefix
#                          reverse proxy (default "/")
#   COOKIE_SAMESITE=lax  → "lax" | "strict" | "none"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
COOKIE_PATH = os.getenv("COOKIE_PATH", "/")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str

    class Config:
        from_attributes = True


@router.post("/login", response_model=UserResponse)
@limiter.limit(LOGIN_RATE_LIMIT)
def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    token = create_access_token(user.id, user.username)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE,
        max_age=JWT_EXPIRE_HOURS * 3600,
        path=COOKIE_PATH,
    )
    return user


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
    return {"status": "ok"}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user
