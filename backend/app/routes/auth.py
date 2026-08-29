"""Auth endpoints: login, logout, me."""
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
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
LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")

# Cookie settings driven by env:
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
COOKIE_PATH = os.getenv("COOKIE_PATH", "/")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")
AUTH_BYPASS = os.getenv("AUTH_BYPASS", "false").lower() in ("1", "true", "yes")


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str


@router.post("/login", response_model=UserResponse)
@limiter.limit(LOGIN_RATE_LIMIT)
def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    if AUTH_BYPASS:
        user = db.query(User).filter(User.username == "local_dev").first()
        if not user:
            user = User(username="local_dev", password_hash="disabled")
            db.add(user)
            db.commit()
            db.refresh(user)
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

    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password[:72], user.password_hash):
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
def me(response: Response, user: User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return user
