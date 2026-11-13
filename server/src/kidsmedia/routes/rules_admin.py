"""Admin: rules, the 'right now' preview and usage (SPEC §3.1, §3.5, §5.4)."""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import Connection, delete, func, insert, select, update

from kidsmedia.clock import Clock, from_db, to_db
from kidsmedia.config import Settings
from kidsmedia.db import (
    adjustments,
    gem_ledger,
    graces,
    groups,
    insert_id,
    plays,
    rule_groups,
    rules,
)
from kidsmedia.deps import (
    Admin,
    get_clock,
    get_conn,
    get_settings,
    require_admin,
    require_admin_csrf,
)
from kidsmedia.errors import ApiError
from kidsmedia.rules import Window, evaluate
from kidsmedia.usage import (
    Status,
    all_group_ids,
    day_of,
    load_rules,
    load_usage,
    local_now,
    status_from,
)

router = APIRouter(prefix="/api/admin", tags=["admin: rules"])

PROFILE_ID = 1  # v1: one child (SPEC §6.1)
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class Lockout(BaseModel):
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)

    @model_validator(mode="after")
    def _distinct(self) -> "Lockout":
        if self.start == self.end:
            raise ValueError("start and end must differ (SPEC §3.3b)")
        return self


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    days: list[int] = Field(min_length=1)
    all_groups: bool = False
    group_ids: list[int] = Field(default_factory=list)
    mode: Literal["limit", "block"] = "limit"
    max_items: int | None = Field(default=None, ge=0, le=100)
    max_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    lockouts: list[Lockout] = Field(default_factory=list, max_length=8)
    grace_minutes: int = Field(default=0, ge=0, le=120)
    # §3.3: a started file uses up an item only after this many seconds of playing.
    count_after_seconds: int = Field(default=10, ge=0, le=600)
    # §3.3: listening budget of a counted file in % of its length; None = unlimited replays.
    replay_percent: int | None = Field(default=120, ge=100, le=1000)
    # §16.2: gems a listen of a file in the scope costs; None = no gems needed.
    gem_cost: int | None = Field(default=None, ge=1, le=20)
    # Parent's own reason-screen text per language (M6); empty = the app's built-in text.
    message_en: str | None = Field(default=None, max_length=140)
    message_de: str | None = Field(default=None, max_length=140)
    message_el: str | None = Field(default=None, max_length=140)

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @model_validator(mode="after")
    def _scope(self) -> "RuleIn":
        if not self.all_groups and not self.group_ids:
            raise ValueError("choose at least one group or all groups")
        return self


class RuleOut(RuleIn):
    id: int


class GroupStatus(BaseModel):
    group_id: int
    name: str
    status: Status


class GroupUsage(BaseModel):
    group_id: int
    name: str
    items: int
    seconds: float


class PlayLogEntry(BaseModel):
    started_at: datetime
    last_at: datetime
    title: str
    group_id: int
    media_id: int | None
    seconds: float
    gems_spent: int = 0  # §16.4: gems this listen cost (all gem rules together)


class UsageDay(BaseModel):
    day: str
    groups: list[GroupUsage]
    plays: list[PlayLogEntry]


class HistoryDay(BaseModel):
    day: str
    items: int
    seconds: float


def _load(conn: Connection, rule_id: int) -> RuleOut:
    row = conn.execute(select(rules).where(rules.c.id == rule_id)).first()
    if row is None:
        raise ApiError(404, "rule_not_found", rule_id=rule_id)
    gids = sorted(
        conn.execute(
            select(rule_groups.c.group_id).where(rule_groups.c.rule_id == rule_id)
        ).scalars()
    )
    return RuleOut(
        id=row.id,
        name=row.name,
        enabled=row.enabled,
        days=[int(c) for c in row.days],
        all_groups=row.all_groups,
        group_ids=gids,
        mode=row.mode,
        max_items=row.max_items,
        max_minutes=row.max_minutes,
        lockouts=[Lockout(start=a, end=b) for a, b in row.lockouts],
        grace_minutes=row.grace_minutes,
        count_after_seconds=row.count_after_seconds,
        replay_percent=row.replay_percent,
        gem_cost=row.gem_cost,
        message_en=row.message_en,
        message_de=row.message_de,
        message_el=row.message_el,
    )


def _write(conn: Connection, rule_id: int, body: RuleIn) -> None:
    known = set(conn.execute(select(groups.c.id)).scalars())
    missing = sorted(set(body.group_ids) - known)
    if missing:
        raise ApiError(422, "unknown_groups", group_ids=",".join(map(str, missing)))
    for lo in body.lockouts:
        Window.parse(lo.start, lo.end)
    conn.execute(
        update(rules)
        .where(rules.c.id == rule_id)
        .values(
            name=body.name.strip(),
            enabled=body.enabled,
            days="".join(str(d) for d in body.days),
            all_groups=body.all_groups,
            mode=body.mode,
            max_items=body.max_items,
            max_minutes=body.max_minutes,
            lockouts=[[lo.start, lo.end] for lo in body.lockouts],
            grace_minutes=body.grace_minutes,
            count_after_seconds=body.count_after_seconds,
            replay_percent=body.replay_percent,
            gem_cost=body.gem_cost,
            message_en=(body.message_en or "").strip() or None,
            message_de=(body.message_de or "").strip() or None,
            message_el=(body.message_el or "").strip() or None,
        )
    )
    conn.execute(delete(rule_groups).where(rule_groups.c.rule_id == rule_id))
    if not body.all_groups:
        for gid in sorted(set(body.group_ids)):
            conn.execute(insert(rule_groups).values(rule_id=rule_id, group_id=gid))


@router.get("/rules", response_model=list[RuleOut])
def list_rules(
    _: Admin = Depends(require_admin), conn: Connection = Depends(get_conn, scope="function")
) -> list[RuleOut]:
    return [_load(conn, i) for i in conn.execute(select(rules.c.id).order_by(rules.c.id)).scalars()]


@router.post("/rules", response_model=RuleOut, status_code=201)
def create_rule(
    body: RuleIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> RuleOut:
    rule_id = insert_id(
        conn.execute(insert(rules).values(name=body.name, days="0", created_at=to_db(clock.now())))
    )
    _write(conn, rule_id, body)
    return _load(conn, rule_id)


@router.put("/rules/{rule_id}", response_model=RuleOut)
def replace_rule(
    rule_id: int,
    body: RuleIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> RuleOut:
    _load(conn, rule_id)
    _write(conn, rule_id, body)
    return _load(conn, rule_id)


class RuleToggle(BaseModel):
    enabled: bool


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def toggle_rule(
    rule_id: int,
    body: RuleToggle,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> RuleOut:
    _load(conn, rule_id)
    conn.execute(update(rules).where(rules.c.id == rule_id).values(enabled=body.enabled))
    return _load(conn, rule_id)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> None:
    _load(conn, rule_id)
    conn.execute(delete(rules).where(rules.c.id == rule_id))


@router.get("/preview", response_model=list[GroupStatus])
def preview(
    at: datetime | None = Query(default=None, description="Local time to simulate; default now"),
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> list[GroupStatus]:
    """What the tablet shows for each group at `at` (SPEC §3.5), with that day's usage."""
    now = local_now(clock, settings)
    moment = (
        now
        if at is None
        else (
            at.replace(tzinfo=settings.zone) if at.tzinfo is None else at.astimezone(settings.zone)
        )
    )
    rs = load_rules(conn, PROFILE_ID)
    usage = load_usage(conn, PROFILE_ID, day_of(moment))
    gids = all_group_ids(conn)
    out = []
    for g in conn.execute(select(groups.c.id, groups.c.name).order_by(groups.c.sort, groups.c.id)):
        out.append(
            GroupStatus(
                group_id=g.id,
                name=g.name,
                status=status_from(evaluate(rs, usage, gids, g.id, None, moment)),
            )
        )
    return out


@router.get("/usage", response_model=UsageDay)
def usage_day(
    day: date | None = Query(default=None),
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> UsageDay:
    d = day.isoformat() if day else day_of(local_now(clock, settings))
    rows = conn.execute(
        select(plays)
        .where(plays.c.profile_id == PROFILE_ID, plays.c.day == d)
        .order_by(plays.c.started_at)
    ).all()
    names = {gid: name for gid, name in conn.execute(select(groups.c.id, groups.c.name)).tuples()}
    spent = {
        media_id: -int(total)
        for media_id, total in conn.execute(
            select(gem_ledger.c.media_id, func.sum(gem_ledger.c.delta))
            .where(
                gem_ledger.c.profile_id == PROFILE_ID,
                gem_ledger.c.kind == "spend",
                gem_ledger.c.day == d,
                gem_ledger.c.media_id.is_not(None),
            )
            .group_by(gem_ledger.c.media_id)
        ).tuples()
    }
    per: dict[int, GroupUsage] = {}
    for p in rows:
        gu = per.setdefault(
            p.group_id,
            GroupUsage(group_id=p.group_id, name=names.get(p.group_id, "?"), items=0, seconds=0),
        )
        gu.items += 1
        gu.seconds += p.seconds
    return UsageDay(
        day=d,
        groups=list(per.values()),
        plays=[
            PlayLogEntry(
                started_at=from_db(p.started_at),
                last_at=from_db(p.last_at),
                title=p.title,
                group_id=p.group_id,
                media_id=p.media_id,
                seconds=p.seconds,
                gems_spent=spent.get(p.media_id, 0) if p.media_id is not None else 0,
            )
            for p in rows
        ],
    )


@router.get("/usage/history", response_model=list[HistoryDay])
def usage_history(
    days: int = Query(7, ge=1, le=90),
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> list[HistoryDay]:
    today = local_now(clock, settings).date()
    first = (today - timedelta(days=days - 1)).isoformat()
    totals = {
        r.day: (r.items, r.seconds)
        for r in conn.execute(
            select(
                plays.c.day,
                func.count().label("items"),
                func.coalesce(func.sum(plays.c.seconds), 0.0).label("seconds"),
            )
            .where(plays.c.profile_id == PROFILE_ID, plays.c.day >= first)
            .group_by(plays.c.day)
        )
    }
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items, seconds = totals.get(d, (0, 0.0))
        out.append(HistoryDay(day=d, items=items, seconds=float(seconds)))
    return out


@router.post("/usage/reset-today", status_code=204)
def reset_today(
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> None:
    """Deletes today's plays and minutes (SPEC §3.5), e.g. as a reward."""
    d = day_of(local_now(clock, settings))
    conn.execute(delete(plays).where(plays.c.profile_id == PROFILE_ID, plays.c.day == d))
    conn.execute(delete(graces).where(graces.c.profile_id == PROFILE_ID, graces.c.day == d))


class Bonus(BaseModel):
    rule_id: int
    extra_items: int = Field(default=0, ge=0, le=20)
    extra_minutes: int = Field(default=0, ge=0, le=600)

    @model_validator(mode="after")
    def _something(self) -> "Bonus":
        if self.extra_items == 0 and self.extra_minutes == 0:
            raise ValueError("a bonus needs extra items or extra minutes")
        return self


@router.post("/usage/bonus", status_code=204)
def bonus(
    body: Bonus,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> None:
    """+N minutes or +N items on one rule, for today only (SPEC §3.5)."""
    _load(conn, body.rule_id)  # 404 for an unknown rule
    conn.execute(
        insert(adjustments).values(
            profile_id=PROFILE_ID,
            day=day_of(local_now(clock, settings)),
            rule_id=body.rule_id,
            extra_items=body.extra_items,
            extra_minutes=body.extra_minutes,
            created_at=to_db(clock.now()),
        )
    )
