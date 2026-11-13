"""Tablet API: pairing, library, streaming, thumbnails (SPEC §5.1-5.3).

Every media and thumbnail byte goes through an authenticated endpoint; behind nginx
the bytes are then served via X-Accel-Redirect from internal locations (SPEC §4.1).
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Connection, delete, select, update

from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import devices, groups, media, pairing_codes, plays, positions
from kidsmedia.deps import (
    Device,
    Viewer,
    get_clock,
    get_conn,
    get_settings,
    require_device,
    require_viewer,
)
from kidsmedia.errors import ApiError
from kidsmedia.gems import uses_gems
from kidsmedia.library import THUMB_KEY_RE, ThumbRef, group_cover, item_thumb
from kidsmedia.security import RateLimiter, hash_token, new_token
from kidsmedia.usage import (
    Evaluator,
    Status,
    active_grace,
    covered_by_grace,
    local_now,
    running_session_day,
    status_from,
)

router = APIRouter(prefix="/api", tags=["device"])


class PairIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    device_name: str | None = Field(default=None, max_length=80)
    app_version: str | None = Field(default=None, max_length=40)


class PairOut(BaseModel):
    token: str
    device_id: int
    profile_id: int


class MeOut(BaseModel):
    device_id: int
    name: str
    profile_id: int
    server_time: datetime
    time_zone: str
    max_volume_percent: int | None = None


class LibraryItem(BaseModel):
    id: int
    title: str
    duration_s: float
    thumb: ThumbRef | None
    position_ms: int
    played_today: bool
    allowed: bool
    # §16.3: the file counts today for every gem rule that covers it (false without one).
    paid_today: bool = False
    offline: bool = False  # M7: keep a copy on the tablet
    size: int = 0


class LibraryGroup(BaseModel):
    id: int
    name: str
    kind: Literal["music", "audiobook", "video"]
    color: str
    cover: ThumbRef | None
    status: Status
    items: list[LibraryItem]
    offline: bool = False


class Gems(BaseModel):
    """§16.3: the child's gem balance; `used` = an enabled gem rule exists."""

    balance: int
    used: bool


class Library(BaseModel):
    server_time: datetime
    groups: list[LibraryGroup]
    gems: Gems
    # This tablet's volume cap (% of the media stream's maximum), null = none.
    max_volume_percent: int | None = None


def _pair_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.pair_limiter
    return limiter


@router.post("/pair", response_model=PairOut)
def pair(
    body: PairIn,
    request: Request,
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> PairOut:
    """Exchange a single-use 6-digit code for a device token (SPEC §5.1, §7)."""
    ip = request.client.host if request.client else "unknown"
    if not _pair_limiter(request).hit(f"pair:{ip}"):
        raise ApiError(429, "rate_limited")
    now = to_db(clock.now())
    code = conn.execute(
        select(pairing_codes).where(
            pairing_codes.c.code == body.code, pairing_codes.c.expires_at > now
        )
    ).first()
    if code is None:
        raise ApiError(400, "invalid_pairing_code")
    device = conn.execute(select(devices).where(devices.c.id == code.device_id)).one()
    token = new_token()
    values: dict[str, object] = {
        "token_hash": hash_token(token),
        "paired_at": now,
        "last_seen_at": now,
        "revoked_at": None,
        "app_version": body.app_version,
    }
    if not device.name and body.device_name:
        values["name"] = body.device_name.strip()
    conn.execute(update(devices).where(devices.c.id == device.id).values(**values))
    conn.execute(delete(pairing_codes).where(pairing_codes.c.device_id == device.id))
    return PairOut(token=token, device_id=device.id, profile_id=device.profile_id)


@router.get("/me", response_model=MeOut)
def me(
    device: Device = Depends(require_device),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> MeOut:
    return MeOut(
        device_id=device.id,
        name=device.name,
        profile_id=device.profile_id,
        server_time=clock.local(settings.zone),
        time_zone=settings.tz,
        max_volume_percent=device.max_volume_percent,
    )


@router.get("/library", response_model=Library)
def library(
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> Library:
    ev = Evaluator(conn, device.profile_id, local_now(clock, settings))
    day = ev.day
    played = set(
        conn.execute(
            select(plays.c.media_id).where(
                plays.c.profile_id == device.profile_id, plays.c.day == day
            )
        ).scalars()
    )
    # Not dict(result): a Result has keys(), so dict() would treat it as a mapping.
    stored = {
        media_id: position_ms
        for media_id, position_ms in conn.execute(
            select(positions.c.media_id, positions.c.position_ms).where(
                positions.c.profile_id == device.profile_id
            )
        ).tuples()
    }
    out: list[LibraryGroup] = []
    for g in conn.execute(select(groups).order_by(groups.c.sort, groups.c.id)):
        cover = group_cover(conn, g)
        items = []
        for m in conn.execute(
            select(media).where(media.c.group_id == g.id).order_by(media.c.sort, media.c.id)
        ):
            decision = ev.decide(g.id, m.id)
            items.append(
                LibraryItem(
                    id=m.id,
                    title=m.title,
                    duration_s=m.duration_s,
                    thumb=item_thumb(m, cover),
                    position_ms=stored.get(m.id, 0) if g.resume else 0,
                    played_today=m.id in played,
                    allowed=decision.allowed,
                    paid_today=decision.gems_paid,
                    offline=bool(g.offline or m.offline),
                    size=m.size,
                )
            )
        out.append(
            LibraryGroup(
                id=g.id,
                name=g.name,
                kind=g.kind,
                color=g.color,
                cover=cover,
                status=status_from(ev.decide(g.id, None)),
                items=items,
                offline=bool(g.offline),
            )
        )
    return Library(
        server_time=clock.local(settings.zone),
        groups=out,
        gems=Gems(balance=ev.usage.gem_balance, used=uses_gems(ev.rules)),
        max_volume_percent=device.max_volume_percent,
    )


def _serve(settings: Settings, internal: str, path: str, media_type: str, cache: str) -> Response:
    if settings.accel_redirect:
        return Response(
            headers={"X-Accel-Redirect": f"{internal}{path}", "Cache-Control": cache},
            media_type=media_type,
        )
    base = settings.media_dir if internal == "/_media/" else settings.thumbs_dir
    return FileResponse(base / path, media_type=media_type, headers={"Cache-Control": cache})


@router.get("/media/{media_id}/stream", responses={206: {"description": "Partial content (Range)"}})
def stream(
    media_id: int,
    viewer: Viewer = Depends(require_viewer),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> Response:
    """Range streaming with the same rules as start/heartbeat (SPEC §5.3).

    The item limit, the listening budget and the gems are skipped (ExoPlayer preloads
    the next queue item before its start call, §16.2), and an active grace keeps the
    playing item streamable through lockouts and minute limits (§3.3a). Admin previews
    aren't rule-checked.
    """
    row = conn.execute(
        select(media.c.path, media.c.mime, media.c.group_id).where(media.c.id == media_id)
    ).first()
    if row is None:
        raise ApiError(404, "media_not_found", media_id=media_id)
    if viewer.device is not None:
        profile = viewer.device.profile_id
        # A file that is already playing keeps its session's day (§3.3), as in heartbeats.
        day = running_session_day(conn, profile, media_id, clock.now())
        decision = Evaluator(conn, profile, local_now(clock, settings), session_day=day).decide(
            row.group_id, media_id, ignore_item_limit=True
        )
        grace = active_grace(conn, profile, media_id, clock)
        if not decision.allowed and not covered_by_grace(decision, grace):
            raise ApiError(403, decision.reason or "not_allowed", **decision.params)
    return _serve(settings, "/_media/", row.path, row.mime, "private, no-transform")


@router.get("/thumbs/{key}.webp", responses={200: {"content": {"image/webp": {}}}})
def thumb(
    key: str,
    size: Literal["sm", "md", "lg"] = "md",
    _: Viewer = Depends(require_viewer),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not THUMB_KEY_RE.match(key):  # also rules out path traversal
        raise ApiError(404, "thumb_not_found")
    name = f"{key}-{size}.webp"
    if not (settings.thumbs_dir / name).exists():
        raise ApiError(404, "thumb_not_found")
    return _serve(settings, "/_thumbs/", name, "image/webp", "private, max-age=31536000, immutable")
