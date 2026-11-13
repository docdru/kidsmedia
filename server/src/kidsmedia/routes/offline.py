"""Offline mode (SPEC §11 M7): downloads, a rules snapshot, and syncing offline plays."""

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Connection, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import groups, media, offline_events, plays
from kidsmedia.deps import Device, get_clock, get_conn, get_settings, require_device
from kidsmedia.errors import ApiError
from kidsmedia.gems import book_gem_spends, uses_gems
from kidsmedia.routes.play import _ensure_play_row, _store_position
from kidsmedia.usage import Evaluator, day_of, load_rules, load_usage, local_now

router = APIRouter(prefix="/api", tags=["device: offline"])

MAX_EVENT_SECONDS = 6 * 3600  # one offline play can't book more than this
MAX_BATCH = 500


class LockoutOut(BaseModel):
    start: str
    end: str


class SnapshotRule(BaseModel):
    id: int
    days: list[int]
    all_groups: bool
    group_ids: list[int]
    mode: str
    max_items: int | None
    max_minutes: int | None
    lockouts: list[LockoutOut]
    grace_minutes: int
    count_after_seconds: int
    replay_percent: int | None
    gem_cost: int | None  # §16.2
    message_en: str | None
    message_de: str | None
    message_el: str | None


class SnapshotUsage(BaseModel):
    day: str
    started: dict[int, int]  # media_id -> group_id (negative keys: deleted media)
    seconds: dict[int, float]  # group_id -> seconds today
    played: dict[int, float]  # same keys as `started` -> that file's seconds today
    extra_items: dict[int, int]
    extra_minutes: dict[int, int]
    # §16 Offline: the balance now, and rule_id -> the files paid today under that rule.
    # The app applies condition 5 to this balance minus the gems it spends offline.
    gem_balance: int
    gems_paid: dict[int, list[int]]


class Snapshot(BaseModel):
    """Everything the app needs to run the rules engine itself (SPEC §3) while offline."""

    server_time: datetime
    time_zone: str
    group_ids: list[int]
    rules: list[SnapshotRule]
    usage: SnapshotUsage


def _fmt(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


@router.get("/offline/snapshot", response_model=Snapshot)
def snapshot(
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> Snapshot:
    now = local_now(clock, settings)
    day = day_of(now)
    usage = load_usage(conn, device.profile_id, day)
    rules = [
        SnapshotRule(
            id=r.id,
            days=sorted(r.days),
            all_groups=r.all_groups,
            group_ids=sorted(r.group_ids),
            mode=r.mode,
            max_items=r.max_items,
            max_minutes=r.max_minutes,
            lockouts=[LockoutOut(start=_fmt(w.start), end=_fmt(w.end)) for w in r.lockouts],
            grace_minutes=r.grace_minutes,
            count_after_seconds=r.count_after_seconds,
            replay_percent=r.replay_percent,
            gem_cost=r.gem_cost,
            message_en=r.message_en,
            message_de=r.message_de,
            message_el=r.message_el,
        )
        for r in load_rules(conn, device.profile_id)
        if r.enabled
    ]
    return Snapshot(
        server_time=now,
        time_zone=settings.tz,
        group_ids=list(conn.execute(select(groups.c.id).order_by(groups.c.id)).scalars()),
        rules=rules,
        usage=SnapshotUsage(
            day=day,
            started=dict(usage.started),
            seconds=dict(usage.seconds),
            played=dict(usage.played),
            extra_items=dict(usage.extra_items),
            extra_minutes=dict(usage.extra_minutes),
            gem_balance=usage.gem_balance,
            gems_paid={rid: sorted(ids) for rid, ids in usage.gems_paid.items()},
        ),
    )


@router.get("/media/{media_id}/download")
def download(
    media_id: int,
    _: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """The whole file for the offline copy. Downloading is not playing, so no rule check
    here; plays are counted when they happen (and synced via /play/batch)."""
    row = conn.execute(
        select(media.c.path, media.c.mime, media.c.offline, groups.c.offline.label("g_offline"))
        .join(groups, groups.c.id == media.c.group_id)
        .where(media.c.id == media_id)
    ).first()
    if row is None:
        raise ApiError(404, "media_not_found", media_id=media_id)
    if not (row.offline or row.g_offline):
        raise ApiError(403, "not_offline")
    if settings.accel_redirect:
        return Response(
            headers={"X-Accel-Redirect": f"/_media/{row.path}", "Cache-Control": "private"},
            media_type=row.mime,
        )
    return FileResponse(settings.media_dir / row.path, media_type=row.mime)


class OfflineEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=64)
    media_id: int
    started_at: datetime  # with offset; the server's TZ decides the day
    seconds: float = Field(ge=0)
    position_ms: int = Field(default=0, ge=0)


class BatchIn(BaseModel):
    events: list[OfflineEvent] = Field(max_length=MAX_BATCH)


class BatchOut(BaseModel):
    accepted: int
    duplicates: int
    unknown_media: int


@router.post("/play/batch", response_model=BatchOut)
def play_batch(
    body: BatchIn,
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> BatchOut:
    """Offline plays, sent when the tablet is back online. Each event is booked once:
    a retried batch (lost response, app restart) is de-duplicated by event_id."""
    accepted = duplicates = unknown = 0
    now = clock.now()
    gems = uses_gems(load_rules(conn, device.profile_id))
    for ev in body.events:
        if ev.started_at.tzinfo is None:
            raise ApiError(422, "started_at_needs_offset")
        fresh = conn.execute(
            sqlite_insert(offline_events)
            .values(event_id=ev.event_id, profile_id=device.profile_id, received_at=to_db(now))
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
        if fresh.rowcount == 0:
            duplicates += 1
            continue
        m = conn.execute(select(media).where(media.c.id == ev.media_id)).first()
        if m is None:
            unknown += 1
            continue
        g = conn.execute(select(groups).where(groups.c.id == m.group_id)).one()
        started = min(ev.started_at, now)  # a wrong tablet clock can't book the future
        day = day_of(started.astimezone(settings.zone))
        _ensure_play_row(conn, device, m, day, started)
        row = conn.execute(
            select(plays.c.id, plays.c.seconds).where(
                plays.c.profile_id == device.profile_id,
                plays.c.media_id == m.id,
                plays.c.day == day,
            )
        ).one()
        conn.execute(
            update(plays)
            .where(plays.c.id == row.id)
            .values(seconds=row.seconds + min(ev.seconds, MAX_EVENT_SECONDS), last_at=to_db(now))
        )
        _store_position(conn, device, m, g, ev.position_ms, now)
        if gems and ev.seconds > 0:
            # §16.2: the listen happened offline, so it is paid with what is left (down
            # to 0); the app already checked the balance it had.
            day_ev = Evaluator(conn, device.profile_id, local_now(clock, settings), session_day=day)
            book_gem_spends(
                conn,
                device.profile_id,
                day_ev.rules,
                day_ev.usage,
                group_id=g.id,
                media_id=m.id,
                title=m.title,
                day=day,
                weekday=day_ev.weekday,
                now=now,
                down_to_zero=True,
            )
        accepted += 1
    return BatchOut(accepted=accepted, duplicates=duplicates, unknown_media=unknown)
