"""Playback: start and heartbeat (SPEC §3.3, §3.3a, §5.3)."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Row, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kidsmedia.clock import Clock, from_db, to_db
from kidsmedia.config import Settings
from kidsmedia.db import graces, groups, media, play_sessions, plays, positions
from kidsmedia.deps import Device, get_clock, get_conn, get_settings, require_device
from kidsmedia.errors import ApiError
from kidsmedia.gems import book_gem_spends
from kidsmedia.rules import Decision, extend_grace, grace_rule
from kidsmedia.usage import Evaluator, active_grace, grace_rule_ids, local_now

router = APIRouter(prefix="/api/play", tags=["device: playback"])

HEARTBEAT_TOLERANCE_S = 5.0  # §3.3 upper bound: elapsed wall time + 5 s
SESSION_RETENTION = timedelta(days=7)  # §6.1 play_sessions pruned after 7 days
RESUME_RESET_FRACTION = 0.97  # §5.3 positions in the last 3 % are stored as 0
GRACE_END_SLACK_MS = 2000  # §3.3a grace ends at position >= duration - 2 s


class Grace(BaseModel):
    active: bool = False
    until_position_ms: int | None = None
    rule_id: int | None = None
    expires_at: datetime | None = None


class StartIn(BaseModel):
    media_id: int


class StartOut(BaseModel):
    allowed: bool
    reason: str | None = None
    params: dict[str, str | int | None] = Field(default_factory=dict)
    remaining_seconds: int | None = None
    grace: Grace = Field(default_factory=Grace)
    play_session_id: str | None = None
    position_ms: int = 0
    stream_url: str | None = None


class HeartbeatIn(BaseModel):
    media_id: int
    play_session_id: str = Field(min_length=1, max_length=64)
    played_seconds_total: float = Field(ge=0)
    position_ms: int = Field(ge=0)


class HeartbeatOut(BaseModel):
    allowed: bool
    reason: str | None = None
    params: dict[str, str | int | None] = Field(default_factory=dict)
    remaining_seconds: int | None = None
    grace: Grace = Field(default_factory=Grace)


def _media_and_group(conn: Connection, media_id: int) -> tuple[Row[Any], Row[Any]]:
    m = conn.execute(select(media).where(media.c.id == media_id)).first()
    if m is None:
        raise ApiError(404, "media_not_found", media_id=media_id)
    g = conn.execute(select(groups).where(groups.c.id == m.group_id)).one()
    return m, g


def _ensure_play_row(
    conn: Connection, device: Device, m: Row[Any], day: str, now: datetime
) -> None:
    """The first start of a file on a day counts as one item (§3.3); replays don't."""
    conn.execute(
        sqlite_insert(plays)
        .values(
            profile_id=device.profile_id,
            media_id=m.id,
            group_id=m.group_id,
            title=m.title,
            day=day,
            seconds=0.0,
            started_at=to_db(now),
            last_at=to_db(now),
        )
        .on_conflict_do_nothing(index_elements=["profile_id", "media_id", "day"])
    )


def _store_position(
    conn: Connection, device: Device, m: Row[Any], g: Row[Any], position_ms: int, now: datetime
) -> None:
    if not g.resume:
        return  # §5.3: resume = false -> positions are not persisted
    duration_ms = int(m.duration_s * 1000)
    value = (
        0 if position_ms >= duration_ms * RESUME_RESET_FRACTION else min(position_ms, duration_ms)
    )
    conn.execute(
        sqlite_insert(positions)
        .values(
            profile_id=device.profile_id, media_id=m.id, position_ms=value, updated_at=to_db(now)
        )
        .on_conflict_do_update(
            index_elements=["profile_id", "media_id"],
            set_={"position_ms": value, "updated_at": to_db(now)},
        )
    )


def _grace_out(row: Row[Any] | None) -> Grace:
    if row is None:
        return Grace()
    return Grace(
        active=True,
        until_position_ms=row.until_position_ms,
        rule_id=row.rule_id,
        expires_at=from_db(row.expires_at),
    )


@router.post("/start", response_model=StartOut)
def start(
    body: StartIn,
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> StartOut:
    """Starting always goes through the normal rules; grace never starts a file (§3.3a)."""
    m, g = _media_and_group(conn, body.media_id)
    now = clock.now()
    ev = Evaluator(conn, device.profile_id, local_now(clock, settings))
    decision = ev.decide(g.id, m.id)
    if not decision.allowed:
        return StartOut(allowed=False, reason=decision.reason, params=dict(decision.params))

    _ensure_play_row(conn, device, m, ev.day, now)
    # Starting another file ends any running grace for this child (§3.3a).
    conn.execute(
        update(graces)
        .where(
            graces.c.profile_id == device.profile_id,
            graces.c.ended_at.is_(None),
            graces.c.media_id != m.id,
        )
        .values(ended_at=to_db(now))
    )
    conn.execute(
        delete(play_sessions).where(
            play_sessions.c.last_heartbeat_at < to_db(now - SESSION_RETENTION)
        )
    )
    session_id = uuid.uuid4().hex
    conn.execute(
        insert(play_sessions).values(
            id=session_id,
            profile_id=device.profile_id,
            device_id=device.id,
            media_id=m.id,
            day=ev.day,
            started_at=to_db(now),
            last_heartbeat_at=to_db(now),
            last_total_seconds=0.0,
        )
    )
    position = 0
    if g.resume:
        stored = conn.execute(
            select(positions.c.position_ms).where(
                positions.c.profile_id == device.profile_id, positions.c.media_id == m.id
            )
        ).scalar_one_or_none()
        position = stored or 0
    # The play row now exists, so re-evaluate for the countdown.
    decision = Evaluator(conn, device.profile_id, local_now(clock, settings)).decide(g.id, m.id)
    return StartOut(
        allowed=True,
        remaining_seconds=decision.remaining_seconds,
        play_session_id=session_id,
        position_ms=position,
        stream_url=f"/api/media/{m.id}/stream",
    )


@router.post("/heartbeat", response_model=HeartbeatOut)
def heartbeat(
    body: HeartbeatIn,
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> HeartbeatOut:
    session = conn.execute(
        select(play_sessions).where(
            play_sessions.c.id == body.play_session_id,
            play_sessions.c.profile_id == device.profile_id,
            play_sessions.c.media_id == body.media_id,
        )
    ).first()
    if session is None:
        raise ApiError(404, "unknown_session")  # §3.3: the app calls /play/start again
    m, g = _media_and_group(conn, body.media_id)
    now = clock.now()
    now_local = local_now(clock, settings)
    # §3.3: a session belongs to the day it started. Its time is booked to that day and
    # it keeps that day's limits after midnight; lockouts and blocks follow the clock
    # (§3.2 "Midnight boundary"), with grace as at any other lockout start (§3.3a).
    day = session.day
    ev = Evaluator(conn, device.profile_id, now_local, session_day=day)

    # §3.3 counting: cumulative total, server-side monotonic, capped by elapsed time.
    delta = 0.0
    if body.played_seconds_total > session.last_total_seconds:
        elapsed = (now - from_db(session.last_heartbeat_at)).total_seconds()
        delta = max(
            0.0,
            min(
                body.played_seconds_total - session.last_total_seconds,
                elapsed + HEARTBEAT_TOLERANCE_S,
            ),
        )
        conn.execute(
            update(play_sessions)
            .where(play_sessions.c.id == session.id)
            .values(last_total_seconds=body.played_seconds_total, last_heartbeat_at=to_db(now))
        )

    # The session's play row is missing only after "reset today" (or a restore): then
    # the file counts again if it may still be started.
    if m.id not in ev.usage.started and ev.decide(g.id, m.id).allowed:
        _ensure_play_row(conn, device, m, day, now)
    if delta > 0:
        row = conn.execute(
            select(plays.c.id, plays.c.seconds).where(
                plays.c.profile_id == device.profile_id,
                plays.c.media_id == m.id,
                plays.c.day == day,
            )
        ).first()
        if row is not None:
            conn.execute(
                update(plays)
                .where(plays.c.id == row.id)
                .values(seconds=row.seconds + delta, last_at=to_db(now))
            )
    _store_position(conn, device, m, g, body.position_ms, now)

    ev = Evaluator(conn, device.profile_id, now_local, session_day=day)
    # §16.2: a file that counts now spends its gems, before this request's evaluation.
    if delta > 0 and book_gem_spends(
        conn,
        device.profile_id,
        ev.rules,
        ev.usage,
        group_id=g.id,
        media_id=m.id,
        title=m.title,
        day=day,
        weekday=ev.weekday,
        now=now,
        down_to_zero=False,
    ):
        ev = Evaluator(conn, device.profile_id, now_local, session_day=day)
    decision = ev.decide(g.id, m.id)
    grace = _update_grace(conn, device, m, body.position_ms, clock)
    if decision.allowed:
        return HeartbeatOut(
            allowed=True, remaining_seconds=decision.remaining_seconds, grace=_grace_out(grace)
        )
    if grace is not None:
        grace = _extend_grace(conn, grace, m, decision, body.position_ms, clock)
        if grace is not None:
            return _graced(grace, m, body.position_ms, now)
        return HeartbeatOut(allowed=False, reason=decision.reason, params=dict(decision.params))
    granted = _grant_grace(conn, device, m, decision, body.position_ms, day, clock)
    if granted is not None:
        return _graced(granted, m, body.position_ms, now)
    return HeartbeatOut(allowed=False, reason=decision.reason, params=dict(decision.params))


def _update_grace(
    conn: Connection, device: Device, m: Row[Any], position_ms: int, clock: Clock
) -> Row[Any] | None:
    """End a running grace when the item is done or the child seeks back (§3.3a)."""
    grace = active_grace(conn, device.profile_id, m.id, clock)
    if grace is None:
        return None
    done = position_ms >= int(m.duration_s * 1000) - GRACE_END_SLACK_MS
    if done or position_ms < grace.start_position_ms:
        conn.execute(
            update(graces).where(graces.c.id == grace.id).values(ended_at=to_db(clock.now()))
        )
        return None
    return grace


def _grant_grace(
    conn: Connection,
    device: Device,
    m: Row[Any],
    decision: Decision,
    position_ms: int,
    day: str,
    clock: Clock,
) -> Row[Any] | None:
    # One grace per file per day: once it ended (finished, expired, seeked back) it
    # can't be re-granted, so the wall-clock cap can't be restarted (§3.3a).
    already = conn.execute(
        select(graces.c.id).where(
            graces.c.profile_id == device.profile_id, graces.c.media_id == m.id, graces.c.day == day
        )
    ).first()
    if already is not None or position_ms >= int(m.duration_s * 1000) - GRACE_END_SLACK_MS:
        return None
    remaining_s = max(0.0, m.duration_s - position_ms / 1000)
    denial = grace_rule(decision, remaining_s)
    if denial is None:
        return None
    now = clock.now()
    conn.execute(
        insert(graces).values(
            profile_id=device.profile_id,
            media_id=m.id,
            rule_id=denial.rule_id,
            day=day,
            granted_at=to_db(now),
            start_position_ms=position_ms,
            until_position_ms=int(m.duration_s * 1000),
            rule_ids=sorted({d.rule_id for d in decision.denials}),
            expires_at=to_db(now + timedelta(minutes=2 * denial.grace_minutes)),
        )
    )
    return active_grace(conn, device.profile_id, m.id, clock)


def _extend_grace(
    conn: Connection,
    grace: Row[Any],
    m: Row[Any],
    decision: Decision,
    position_ms: int,
    clock: Clock,
) -> Row[Any] | None:
    """A grace is running and something denies (§3.3a "Multiple rules").

    Denials from the rules that granted the grace stay covered. A rule that expires
    later (bedtime starts while a music-limit grace runs) must grant grace on its own,
    for the rest of the item, and then joins the grace with a wall-clock cap that is at
    most as long as before. Otherwise the grace ends now and playback stops.
    """
    covered = grace_rule_ids(grace)
    remaining_s = max(0.0, m.duration_s - position_ms / 1000)
    now = clock.now()
    extended = extend_grace(decision, remaining_s, covered)
    if extended is None:
        conn.execute(update(graces).where(graces.c.id == grace.id).values(ended_at=to_db(now)))
        return None
    new = [d for d in decision.denials if d.rule_id not in covered]
    if not new:
        return grace
    cap = now + timedelta(minutes=2 * min(d.grace_minutes for d in new))
    conn.execute(
        update(graces)
        .where(graces.c.id == grace.id)
        .values(
            rule_ids=sorted(extended),
            expires_at=to_db(min(from_db(grace.expires_at), cap)),
        )
    )
    return active_grace(conn, grace.profile_id, m.id, clock)


def _graced(grace: Row[Any] | None, m: Row[Any], position_ms: int, now: datetime) -> HeartbeatOut:
    if grace is None:  # pragma: no cover - callers check
        raise RuntimeError("no grace")
    item_left = max(0.0, m.duration_s - position_ms / 1000)
    cap_left = (from_db(grace.expires_at) - now).total_seconds()
    return HeartbeatOut(
        allowed=True, remaining_seconds=int(min(item_left, cap_left)), grace=_grace_out(grace)
    )
