"""Device: rewards, i.e. stars, the sticker album, swaps and wishes (SPEC §14.4).
Shared loaders for the admin too."""

from datetime import datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, Row, func, insert, or_, select
from sqlalchemy.exc import IntegrityError

from kidsmedia.activities import Lang
from kidsmedia.clock import Clock, from_db, to_db
from kidsmedia.config import Settings
from kidsmedia.db import (
    activity_done,
    adjustments,
    begin_immediate,
    groups,
    reward_ledger,
    reward_swaps,
    reward_wishes,
    rule_groups,
    rules,
    wish_requests,
)
from kidsmedia.deps import Device, get_clock, get_conn, get_settings, require_device
from kidsmedia.errors import ApiError
from kidsmedia.library import ThumbRef
from kidsmedia.rewards import (
    DoneTask,
    LedgerRow,
    album,
    balance,
    rule_active_on,
    swap_refusal,
    swap_suits_rule,
)
from kidsmedia.routes.activities import (
    DeviceWorldTexts,
    chapter_pictures,
    content_of,
    device_world_texts,
    picture_ref,
    world_cover,
    world_rows,
)
from kidsmedia.usage import day_of, local_now

router = APIRouter(prefix="/api", tags=["device: rewards"])

RequestStatus = Literal["pending", "approved", "declined"]


# ---- models -------------------------------------------------------------------------------


class WishTexts(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=60)


class StickerOut(BaseModel):
    position: int
    image: ThumbRef | None  # the chapter picture; None = the app shows the cover
    earned_on: str


class AlbumPageOut(BaseModel):
    world_id: int
    texts: dict[Lang, DeviceWorldTexts]  # without the spoken forms (§14.3)
    cover: ThumbRef | None
    stickers: list[StickerOut]


class DeviceSwap(BaseModel):
    id: int
    stars: int
    rule_id: int
    extra_items: int
    extra_minutes: int
    used_today: int
    max_per_day: int | None
    groups: list[str]  # the rule's group names, for the label; all groups for an all-groups rule


class RequestBrief(BaseModel):
    id: int
    status: RequestStatus
    created_at: datetime


class DeviceWish(BaseModel):
    id: int
    texts: dict[Lang, WishTexts]
    stars: int
    request: RequestBrief | None  # the latest request for this wish


class RewardsOut(BaseModel):
    stars: int
    album: list[AlbumPageOut]
    swaps: list[DeviceSwap]
    wishes: list[DeviceWish]


# ---- shared loaders -----------------------------------------------------------------------


def done_tasks(conn: Connection, profile_id: int) -> list[DoneTask]:
    return [
        DoneTask(
            order=r.id,
            world_id=r.world_id,
            position=r.position,
            kind=r.kind,
            day=r.day,
            attempts=r.attempts,
        )
        for r in conn.execute(
            select(
                activity_done.c.id,
                activity_done.c.world_id,
                activity_done.c.position,
                activity_done.c.kind,
                activity_done.c.day,
                activity_done.c.attempts,
            )
            .where(activity_done.c.profile_id == profile_id)
            .order_by(activity_done.c.id)
        ).all()
    ]


def ledger_rows(conn: Connection, profile_id: int) -> list[LedgerRow]:
    """Every ledger row of the profile as (kind, delta): earned stars and bonus stars
    (positive), "open again" (`reopen`, negative earning, §17.3), swaps and wishes."""
    return [
        (r.kind, r.delta)
        for r in conn.execute(
            select(reward_ledger.c.kind, reward_ledger.c.delta).where(
                reward_ledger.c.profile_id == profile_id
            )
        ).all()
    ]


def stars_of(conn: Connection, profile_id: int) -> int:
    return balance(ledger_rows(conn, profile_id))


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The local day of `now` (aware, local) as a [start, end) range in database time."""
    start = datetime.combine(now.date(), time(), tzinfo=now.tzinfo)
    end = datetime.combine(now.date() + timedelta(days=1), time(), tzinfo=now.tzinfo)
    return to_db(start), to_db(end)


def used_today(conn: Connection, profile_id: int, swap_id: int, now: datetime) -> int:
    """How often the swap was redeemed on the local day of `now`."""
    start, end = day_bounds(now)
    n: int = conn.execute(
        select(func.count())
        .select_from(reward_ledger)
        .where(
            reward_ledger.c.profile_id == profile_id,
            reward_ledger.c.kind == "swap",
            reward_ledger.c.ref_id == swap_id,
            reward_ledger.c.created_at >= start,
            reward_ledger.c.created_at < end,
        )
    ).scalar_one()
    return n


def offered_swaps(conn: Connection, profile_id: int, now: datetime) -> list[Row[Any]]:
    """Enabled swaps whose rule exists, is enabled, applies to the profile, is active on the
    weekday of `now` (local) and has the limit the swap raises (§14.4): the others would give
    nothing today."""
    rows = conn.execute(
        select(
            reward_swaps,
            rules.c.all_groups,
            rules.c.days,
            rules.c.mode,
            rules.c.max_items,
            rules.c.max_minutes,
        )
        .join(rules, rules.c.id == reward_swaps.c.rule_id)
        .where(
            reward_swaps.c.profile_id == profile_id,
            reward_swaps.c.enabled.is_(True),
            rules.c.enabled.is_(True),
            or_(rules.c.profile_id.is_(None), rules.c.profile_id == profile_id),
        )
        .order_by(reward_swaps.c.stars, reward_swaps.c.id)
    ).all()
    return [
        r
        for r in rows
        if rule_active_on(r.days, now.weekday())
        and swap_suits_rule(r.mode, r.max_items, r.max_minutes, r.extra_items, r.extra_minutes)
    ]


def rule_group_names(conn: Connection, rule_id: int, all_groups: bool) -> list[str]:
    query = select(groups.c.name).order_by(groups.c.sort, groups.c.id)
    if not all_groups:
        query = query.join(rule_groups, rule_groups.c.group_id == groups.c.id).where(
            rule_groups.c.rule_id == rule_id
        )
    return list(conn.execute(query).scalars())


def latest_requests(conn: Connection, profile_id: int) -> dict[int, Row[Any]]:
    """The newest request per wish."""
    found: dict[int, Row[Any]] = {}
    for r in conn.execute(
        select(wish_requests)
        .where(wish_requests.c.profile_id == profile_id)
        .order_by(wish_requests.c.id)
    ).all():
        found[r.wish_id] = r
    return found


def album_out(conn: Connection, profile_id: int) -> list[AlbumPageOut]:
    rows = world_rows(conn)
    pages: list[AlbumPageOut] = []
    for page in album(done_tasks(conn, profile_id)):
        row = rows.get(page.world_id)
        if row is None:  # pragma: no cover - done rows are deleted with their world
            continue
        pictures = chapter_pictures(row)
        pages.append(
            AlbumPageOut(
                world_id=row.id,
                texts=device_world_texts(content_of(row).texts),
                cover=world_cover(row),
                stickers=[
                    StickerOut(
                        position=s.position,
                        image=picture_ref(pictures[s.position - 1])
                        if s.position <= len(pictures)
                        else None,
                        earned_on=s.earned_on,
                    )
                    for s in page.stickers
                ],
            )
        )
    return pages


def rewards_out(conn: Connection, profile_id: int, now: datetime) -> RewardsOut:
    swaps = [
        DeviceSwap(
            id=s.id,
            stars=s.stars,
            rule_id=s.rule_id,
            extra_items=s.extra_items,
            extra_minutes=s.extra_minutes,
            used_today=used_today(conn, profile_id, s.id, now),
            max_per_day=s.max_per_day,
            groups=rule_group_names(conn, s.rule_id, s.all_groups),
        )
        for s in offered_swaps(conn, profile_id, now)
    ]
    requests = latest_requests(conn, profile_id)
    wishes: list[DeviceWish] = []
    for w in conn.execute(
        select(reward_wishes)
        .where(reward_wishes.c.profile_id == profile_id, reward_wishes.c.enabled.is_(True))
        .order_by(reward_wishes.c.stars, reward_wishes.c.id)
    ).all():
        req = requests.get(w.id)
        wishes.append(
            DeviceWish(
                id=w.id,
                texts=w.texts,
                stars=w.stars,
                request=RequestBrief(
                    id=req.id, status=req.status, created_at=from_db(req.created_at)
                )
                if req is not None
                else None,
            )
        )
    return RewardsOut(
        stars=stars_of(conn, profile_id),
        album=album_out(conn, profile_id),
        swaps=swaps,
        wishes=wishes,
    )


# ---- device API ---------------------------------------------------------------------------


@router.get("/rewards", response_model=RewardsOut)
def get_rewards(
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> RewardsOut:
    """Stars, the sticker album, the swaps and the wishes (SPEC §14.4)."""
    return rewards_out(conn, device.profile_id, local_now(clock, settings))


@router.post("/rewards/swaps/{swap_id}", response_model=RewardsOut)
def redeem_swap(
    swap_id: int,
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> RewardsOut:
    """Stars for today's per-rule bonus (§3.5): an `adjustments` row plus a ledger row.
    409 swap_limit_reached / not_enough_stars. Check and spend are one atomic step: two taps
    at once can't both pass the balance check."""
    now = local_now(clock, settings)
    profile = device.profile_id
    begin_immediate(conn)
    swap = next((s for s in offered_swaps(conn, profile, now) if s.id == swap_id), None)
    if swap is None:
        raise ApiError(404, "unknown_swap", swap_id=swap_id)
    stars = stars_of(conn, profile)
    refusal = swap_refusal(
        stars, swap.stars, used_today(conn, profile, swap.id, now), swap.max_per_day
    )
    if refusal == "swap_limit_reached":
        raise ApiError(409, refusal, max_per_day=swap.max_per_day)
    if refusal is not None:
        raise ApiError(409, refusal, stars=stars, needed=swap.stars)
    conn.execute(
        insert(adjustments).values(
            profile_id=profile,
            day=day_of(now),
            rule_id=swap.rule_id,
            extra_items=swap.extra_items,
            extra_minutes=swap.extra_minutes,
            created_at=to_db(now),
        )
    )
    conn.execute(
        insert(reward_ledger).values(
            profile_id=profile,
            delta=-swap.stars,
            kind="swap",
            ref_id=swap.id,
            created_at=to_db(now),
        )
    )
    return rewards_out(conn, profile, now)


@router.post("/rewards/wishes/{wish_id}/request", response_model=RewardsOut)
def request_wish(
    wish_id: int,
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> RewardsOut:
    """The child's "I'd like this": a pending request for the parent.
    409 request_pending if one is open, 409 not_enough_stars if the balance is too low.
    Atomic, and the partial unique index allows one pending request per wish anyway."""
    now = local_now(clock, settings)
    profile = device.profile_id
    begin_immediate(conn)
    wish = conn.execute(
        select(reward_wishes).where(
            reward_wishes.c.id == wish_id,
            reward_wishes.c.profile_id == profile,
            reward_wishes.c.enabled.is_(True),
        )
    ).first()
    if wish is None:
        raise ApiError(404, "unknown_wish", wish_id=wish_id)
    pending = conn.execute(
        select(wish_requests.c.id).where(
            wish_requests.c.profile_id == profile,
            wish_requests.c.wish_id == wish_id,
            wish_requests.c.status == "pending",
        )
    ).first()
    if pending is not None:
        raise ApiError(409, "request_pending", request_id=pending.id)
    stars = stars_of(conn, profile)
    if stars < wish.stars:
        raise ApiError(409, "not_enough_stars", stars=stars, needed=wish.stars)
    try:
        conn.execute(
            insert(wish_requests).values(
                profile_id=profile, wish_id=wish_id, status="pending", created_at=to_db(now)
            )
        )
    except IntegrityError as exc:  # pragma: no cover - the lock above makes this unlikely
        raise ApiError(409, "request_pending") from exc
    return rewards_out(conn, profile, now)
