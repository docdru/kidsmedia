"""Bridge between the database and the pure rules engine (SPEC §3)."""

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Connection, Row, and_, func, or_, select

from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import (
    adjustments,
    graces,
    groups,
    media,
    play_sessions,
    plays,
    rule_groups,
    rules,
)
from kidsmedia.gems import gem_balance, gems_paid
from kidsmedia.rules import GRACEABLE, Decision, Rule, Usage, Window, evaluate


class Status(BaseModel):
    """Child-facing state: reason code + params, never sentences (SPEC §3.2, §5.2)."""

    allowed: bool = True
    reason: str | None = None
    params: dict[str, str | int | None] = Field(default_factory=dict)
    items_left: int | None = None
    minutes_left: int | None = None
    unlocks_at: datetime | None = None
    remaining_seconds: int | None = None
    # §16.3: the gems a new file of the group needs today; None = no gem rule applies.
    gem_cost: int | None = None


def status_from(decision: Decision) -> Status:
    return Status(
        allowed=decision.allowed,
        reason=decision.reason,
        params=dict(decision.params),
        items_left=decision.items_left,
        minutes_left=decision.minutes_left,
        unlocks_at=decision.unlocks_at,
        remaining_seconds=decision.remaining_seconds,
        gem_cost=decision.gem_cost,
    )


def rule_from_row(row: Row[Any], group_ids: frozenset[int]) -> Rule:
    return Rule(
        id=row.id,
        name=row.name,
        enabled=row.enabled,
        days=frozenset(int(c) for c in row.days),
        all_groups=row.all_groups,
        group_ids=group_ids,
        mode=row.mode,
        max_items=row.max_items,
        max_minutes=row.max_minutes,
        lockouts=tuple(Window.parse(a, b) for a, b in row.lockouts),
        grace_minutes=row.grace_minutes,
        count_after_seconds=row.count_after_seconds,
        replay_percent=row.replay_percent,
        gem_cost=row.gem_cost,
        message_en=row.message_en,
        message_de=row.message_de,
        message_el=row.message_el,
    )


def load_rules(conn: Connection, profile_id: int) -> list[Rule]:
    members: dict[int, set[int]] = {}
    for rule_id, group_id in conn.execute(select(rule_groups.c.rule_id, rule_groups.c.group_id)):
        members.setdefault(rule_id, set()).add(group_id)
    rows = conn.execute(
        select(rules)
        .where(or_(rules.c.profile_id.is_(None), rules.c.profile_id == profile_id))
        .order_by(rules.c.id)
    )
    return [rule_from_row(r, frozenset(members.get(r.id, ()))) for r in rows]


def load_usage(conn: Connection, profile_id: int, day: str) -> Usage:
    """`day`'s usage, with the gem balance as it is now and `day`'s paid listens (§16)."""
    started: dict[int, int] = {}
    seconds: dict[int, float] = {}
    played: dict[int, float] = {}
    for p in conn.execute(
        select(plays.c.id, plays.c.media_id, plays.c.group_id, plays.c.seconds).where(
            plays.c.profile_id == profile_id, plays.c.day == day
        )
    ):
        # Deleted media keeps counting (SPEC §3.3): use a key that can't clash with media ids.
        key = p.media_id if p.media_id is not None else -p.id
        started[key] = p.group_id
        played[key] = played.get(key, 0.0) + p.seconds
        seconds[p.group_id] = seconds.get(p.group_id, 0.0) + p.seconds
    extra_items: dict[int, int] = {}
    extra_minutes: dict[int, int] = {}
    for a in conn.execute(
        select(
            adjustments.c.rule_id,
            func.sum(adjustments.c.extra_items).label("items"),
            func.sum(adjustments.c.extra_minutes).label("minutes"),
        )
        .where(adjustments.c.profile_id == profile_id, adjustments.c.day == day)
        .group_by(adjustments.c.rule_id)
    ):
        extra_items[a.rule_id] = int(a.items or 0)
        extra_minutes[a.rule_id] = int(a.minutes or 0)
    durations = {
        m.id: m.duration_s
        for m in conn.execute(
            select(media.c.id, media.c.duration_s).where(media.c.id.in_(list(started)))
        )
    }
    return Usage(
        started=started,
        seconds=seconds,
        played=played,
        durations=durations,
        extra_items=extra_items,
        extra_minutes=extra_minutes,
        gem_balance=gem_balance(conn, profile_id),
        gems_paid=gems_paid(conn, profile_id, day),
    )


def all_group_ids(conn: Connection) -> list[int]:
    return list(conn.execute(select(groups.c.id)).scalars())


def local_now(clock: Clock, settings: Settings) -> datetime:
    return clock.local(settings.zone)


def day_of(moment: datetime | date) -> str:
    return (moment.date() if isinstance(moment, datetime) else moment).isoformat()


class Evaluator:
    """Loads rules and a day's usage once, then evaluates many groups/items.

    session_day: for a file that is already playing, the day its session started
    (§3.3 "a session belongs to the day it started"). Usage and limits are that day's,
    lockouts and blocks follow the clock. None = today (a new start).
    """

    def __init__(
        self,
        conn: Connection,
        profile_id: int,
        now_local: datetime,
        session_day: str | None = None,
    ) -> None:
        self.now = now_local
        self.day = session_day or day_of(now_local)
        self.limits_day = (
            date.fromisoformat(self.day).weekday() if self.day != day_of(now_local) else None
        )
        # The weekday whose limits (and gem costs) apply: the session's day (§3.3).
        self.weekday = date.fromisoformat(self.day).weekday()
        self.rules = load_rules(conn, profile_id)
        self.usage = load_usage(conn, profile_id, self.day)
        self.group_ids = all_group_ids(conn)

    def decide(
        self, group_id: int, media_id: int | None, *, ignore_item_limit: bool = False
    ) -> Decision:
        return evaluate(
            self.rules,
            self.usage,
            self.group_ids,
            group_id,
            media_id,
            self.now,
            ignore_item_limit=ignore_item_limit,
            limits_day=self.limits_day,
        )


RUNNING_SESSION = timedelta(minutes=10)


def running_session_day(
    conn: Connection, profile_id: int, media_id: int, now: datetime
) -> str | None:
    """The day of the newest session of this file that is still running (a heartbeat in
    the last 10 minutes), for the stream endpoint, which has no session id."""
    return conn.execute(
        select(play_sessions.c.day)
        .where(
            play_sessions.c.profile_id == profile_id,
            play_sessions.c.media_id == media_id,
            play_sessions.c.last_heartbeat_at >= to_db(now - RUNNING_SESSION),
        )
        .order_by(play_sessions.c.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def active_grace(conn: Connection, profile_id: int, media_id: int, clock: Clock) -> Row[Any] | None:
    return conn.execute(
        select(graces).where(
            and_(
                graces.c.profile_id == profile_id,
                graces.c.media_id == media_id,
                graces.c.ended_at.is_(None),
                graces.c.expires_at > to_db(clock.now()),
            )
        )
    ).first()


def grace_rule_ids(grace: Row[Any]) -> frozenset[int]:
    """The rules a grace covers. Rows from before migration 0004 only have rule_id."""
    ids = {int(i) for i in (grace.rule_ids or [])}
    if grace.rule_id is not None:
        ids.add(grace.rule_id)
    return frozenset(ids)


def covered_by_grace(decision: Decision, grace: Row[Any] | None) -> bool:
    """A running grace keeps an item going through the minute limits and lockouts of the
    rules that granted it, and nothing else (§3.3a): a rule that expires later must
    grant its own grace (see play._extend_grace)."""
    if grace is None:
        return False
    covered = grace_rule_ids(grace)
    return all(d.reason in GRACEABLE and d.rule_id in covered for d in decision.denials)
