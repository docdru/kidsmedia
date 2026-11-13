"""Shared FastAPI dependencies: database connection, clock, authentication."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import Connection, select, update

from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import admin_sessions, admin_users, connect, devices
from kidsmedia.errors import ApiError
from kidsmedia.security import hash_token, tokens_equal

SESSION_COOKIE = "km_session"
CSRF_HEADER = "X-CSRF-Token"


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_conn(request: Request) -> Iterator[Connection]:
    """The request's transaction. Always use it as ``Depends(get_conn, scope="function")``:
    with the default scope FastAPI runs the commit only *after* the response has been
    sent, so a fast client's next request could miss the write (a just-paired tablet got
    ``device_unknown``, a fresh login 401). Found by the e2e test; see test_db."""
    yield from connect(request.app.state.engine)


@dataclass(frozen=True)
class Admin:
    user_id: int
    username: str
    session_id: int
    csrf_token: str
    locale: str | None


def _admin_from_cookie(request: Request, conn: Connection, clock: Clock) -> Admin | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = conn.execute(
        select(
            admin_sessions.c.id,
            admin_sessions.c.csrf_token,
            admin_sessions.c.expires_at,
            admin_users.c.id.label("user_id"),
            admin_users.c.username,
            admin_users.c.locale,
        )
        .join(admin_users, admin_users.c.id == admin_sessions.c.user_id)
        .where(admin_sessions.c.token_hash == hash_token(token))
    ).first()
    if row is None or row.expires_at <= to_db(clock.now()):
        return None
    return Admin(row.user_id, row.username, row.id, row.csrf_token, row.locale)


def require_admin(
    request: Request,
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> Admin:
    admin = _admin_from_cookie(request, conn, clock)
    if admin is None:
        raise ApiError(401, "not_authenticated")
    return admin


def require_admin_csrf(request: Request, admin: Admin = Depends(require_admin)) -> Admin:
    """Admin + CSRF header for every state-changing admin request (SPEC §7)."""
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent or not tokens_equal(sent, admin.csrf_token):
        raise ApiError(403, "csrf_failed")
    return admin


@dataclass(frozen=True)
class Device:
    id: int
    profile_id: int
    name: str
    max_volume_percent: int | None = None


LAST_SEEN_RESOLUTION = timedelta(seconds=60)


def require_device(
    request: Request,
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> Device:
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "not_authenticated")
    row = conn.execute(
        select(devices).where(
            devices.c.token_hash == hash_token(token.strip()), devices.c.revoked_at.is_(None)
        )
    ).first()
    if row is None:
        raise ApiError(401, "device_unknown")
    now = to_db(clock.now())
    if row.last_seen_at is None or now - row.last_seen_at >= LAST_SEEN_RESOLUTION:
        conn.execute(update(devices).where(devices.c.id == row.id).values(last_seen_at=now))
    return Device(row.id, row.profile_id, row.name, row.max_volume_percent)


@dataclass(frozen=True)
class Viewer:
    """Either a paired device or a logged-in admin (e.g. for thumbnails and previews)."""

    device: Device | None
    admin: Admin | None


def require_viewer(
    request: Request,
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> Viewer:
    if request.headers.get("Authorization"):
        return Viewer(require_device(request, conn, clock), None)
    admin = _admin_from_cookie(request, conn, clock)
    if admin is None:
        raise ApiError(401, "not_authenticated")
    return Viewer(None, admin)
