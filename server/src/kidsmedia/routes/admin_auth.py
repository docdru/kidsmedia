"""Admin login, logout and the current admin (SPEC §4.2, §7)."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import Connection, delete, insert, select, update

from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import admin_sessions, admin_users
from kidsmedia.deps import (
    SESSION_COOKIE,
    Admin,
    get_clock,
    get_conn,
    get_settings,
    require_admin,
    require_admin_csrf,
)
from kidsmedia.errors import ApiError
from kidsmedia.security import RateLimiter, hash_token, new_token, verify_password

router = APIRouter(prefix="/api/admin", tags=["admin: auth"])

Locale = Literal["en", "de", "el"]
LOCALES: tuple[Locale, ...] = ("en", "de", "el")


def _limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.login_limiter
    return limiter


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class Me(BaseModel):
    username: str
    csrf_token: str
    locale: Locale | None


class MePatch(BaseModel):
    locale: Locale | None


def _locale(value: str | None) -> Locale | None:
    return value if value in LOCALES else None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=Me)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> Me:
    if not _limiter(request).hit(f"login:{_client_ip(request)}"):
        raise ApiError(429, "rate_limited")
    user = conn.execute(select(admin_users).where(admin_users.c.username == body.username)).first()
    if user is None or not verify_password(user.password_hash, body.password):
        raise ApiError(401, "bad_credentials")

    token, csrf = new_token(), new_token()
    now = clock.now()
    expires = now + timedelta(days=settings.session_days)
    conn.execute(delete(admin_sessions).where(admin_sessions.c.expires_at <= to_db(now)))
    conn.execute(
        insert(admin_sessions).values(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            created_at=to_db(now),
            expires_at=to_db(expires),
        )
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="strict",
        secure=settings.secure_cookies,
        path="/",
    )
    return Me(username=user.username, csrf_token=csrf, locale=_locale(user.locale))


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    admin: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> None:
    conn.execute(delete(admin_sessions).where(admin_sessions.c.id == admin.session_id))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=Me)
def me(admin: Admin = Depends(require_admin)) -> Me:
    return Me(username=admin.username, csrf_token=admin.csrf_token, locale=_locale(admin.locale))


@router.patch("/me", response_model=Me)
def patch_me(
    body: MePatch,
    admin: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> Me:
    """Remember the admin's language choice (decision 13). null = follow the browser."""
    conn.execute(
        update(admin_users).where(admin_users.c.id == admin.user_id).values(locale=body.locale)
    )
    return Me(username=admin.username, csrf_token=admin.csrf_token, locale=body.locale)
