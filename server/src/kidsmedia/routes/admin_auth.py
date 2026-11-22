"""Admin login, logout and the current admin (SPEC §4.2, §7, §7.1)."""

import logging
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import Connection, delete, insert, select, update

from kidsmedia import running_version
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
from kidsmedia.oidc import OidcClient, OidcError, new_pkce
from kidsmedia.security import RateLimiter, hash_token, new_token, verify_password

log = logging.getLogger("kidsmedia")

OIDC_COOKIE = "km_oidc"
OIDC_COOKIE_PATH = "/api/admin/oidc"
OIDC_COOKIE_SECONDS = 600

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
    version: str  # the release the server runs, shown in the admin (SPEC §9)


class AuthConfig(BaseModel):
    """What the login page offers (SPEC §7.1)."""

    password: bool
    sso_label: str | None  # null = no single sign-on


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
    if settings.oidc_enabled:
        raise ApiError(403, "password_login_disabled")
    if not _limiter(request).hit(f"login:{_client_ip(request)}"):
        raise ApiError(429, "rate_limited")
    user = conn.execute(select(admin_users).where(admin_users.c.username == body.username)).first()
    if user is None or not verify_password(user.password_hash, body.password):
        raise ApiError(401, "bad_credentials")

    csrf = _open_session(conn, clock, settings, response, user.id, "password", None)
    return Me(
        username=user.username,
        csrf_token=csrf,
        locale=_locale(user.locale),
        version=running_version(),
    )


def _open_session(
    conn: Connection,
    clock: Clock,
    settings: Settings,
    response: Response,
    user_id: int,
    method: Literal["password", "sso"],
    sso_groups: list[str] | None,
) -> str:
    """Store a new session, set its cookie, return its CSRF token."""
    token, csrf = new_token(), new_token()
    now = clock.now()
    expires = now + timedelta(days=settings.session_days)
    conn.execute(delete(admin_sessions).where(admin_sessions.c.expires_at <= to_db(now)))
    conn.execute(
        insert(admin_sessions).values(
            user_id=user_id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            created_at=to_db(now),
            expires_at=to_db(expires),
            method=method,
            sso_groups=sso_groups,
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
    return csrf


@router.get("/auth", response_model=AuthConfig)
def auth_config(settings: Settings = Depends(get_settings)) -> AuthConfig:
    """Open to everyone: the login page asks which sign-in to show."""
    if settings.oidc_enabled:
        return AuthConfig(password=False, sso_label=settings.oidc_label or "SSO")
    return AuthConfig(password=True, sso_label=None)


def _oidc(request: Request) -> OidcClient:
    client: OidcClient = request.app.state.oidc
    return client


def _back_to_login(error: str) -> RedirectResponse:
    r = RedirectResponse(f"/?sso_error={error}", status_code=303)
    r.delete_cookie(OIDC_COOKIE, path=OIDC_COOKIE_PATH)
    return r


@router.get("/oidc/login", include_in_schema=False)
def oidc_login(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Start single sign-on: off to the provider with state and a PKCE challenge."""
    if not settings.oidc_enabled:
        raise ApiError(404, "sso_disabled")
    state = secrets.token_urlsafe(32)
    verifier, challenge = new_pkce()
    try:
        url = _oidc(request).authorize_url(state, challenge)
    except OidcError as e:
        log.error("Single sign-on: %s", e)
        return _back_to_login("failed")
    r = RedirectResponse(url, status_code=302)
    # Lax, not Strict: the provider sends the browser back with a cross-site navigation.
    r.set_cookie(
        OIDC_COOKIE,
        f"{state}.{verifier}",
        max_age=OIDC_COOKIE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path=OIDC_COOKIE_PATH,
    )
    return r


@router.get("/oidc/callback", include_in_schema=False)
def oidc_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> Response:
    """The provider's redirect back. On success: a session for the account, then the admin."""
    if not settings.oidc_enabled:
        raise ApiError(404, "sso_disabled")
    if error:
        log.warning("Single sign-on: the provider answered %r", error[:100])
        return _back_to_login("failed")
    sent_state, _, verifier = request.cookies.get(OIDC_COOKIE, "").partition(".")
    if not code or not sent_state or not verifier or not secrets.compare_digest(sent_state, state):
        log.warning("Single sign-on: missing or wrong state (expired, or a forged link)")
        return _back_to_login("failed")
    try:
        who = _oidc(request).identity(code, verifier)
    except OidcError as e:
        log.error("Single sign-on: %s", e)
        return _back_to_login("failed")
    if not settings.oidc_allows(who.username, who.groups):
        log.warning("Single sign-on: %r is not in OIDC_ALLOWED_USERS / _GROUPS", who.username)
        return _back_to_login("not_allowed")

    user = conn.execute(select(admin_users).where(admin_users.c.username == who.username)).first()
    if user is None:
        key = conn.execute(
            insert(admin_users).values(
                username=who.username,
                password_hash="",  # no password: verify_password never accepts ""
                created_at=to_db(clock.now()),
            )
        ).inserted_primary_key
        if key is None:  # pragma: no cover - SQLite always returns the new id
            raise ApiError(500, "internal")
        user_id = int(key[0])
        log.info("Single sign-on: first sign-in of %r", who.username)
    else:
        user_id = user.id

    # A same-site page that moves on to the admin, rather than a redirect: the session cookie
    # is SameSite=Strict, and the navigation that brought us here started at the provider.
    page = HTMLResponse(
        '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/">'
        '<title>Kids Media</title><a href="/">Kids Media</a>'
    )
    _open_session(conn, clock, settings, page, user_id, "sso", sorted(who.groups))
    page.delete_cookie(OIDC_COOKIE, path=OIDC_COOKIE_PATH)
    page.headers["Cache-Control"] = "no-store"
    return page


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
    return Me(
        username=admin.username,
        csrf_token=admin.csrf_token,
        locale=_locale(admin.locale),
        version=running_version(),
    )


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
    return Me(
        username=admin.username,
        csrf_token=admin.csrf_token,
        locale=body.locale,
        version=running_version(),
    )
