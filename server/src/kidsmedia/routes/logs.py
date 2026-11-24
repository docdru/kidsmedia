"""Device: why the gems and the stars went up or down (SPEC §16.6, §14.4b).

Each log row is a ledger row plus what the tablet needs to show it to a child who can't read
yet: the `kind` as a code (the words live in the clients, §3.2), a picture (the file, the
chapter) and the names it is about. The parent's note on a gem change is shown as written.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Connection, select

from kidsmedia.activities import Lang
from kidsmedia.clock import from_db
from kidsmedia.config import Settings
from kidsmedia.db import (
    activity_done,
    gem_ledger,
    groups,
    media,
    reward_ledger,
    reward_swaps,
    reward_wishes,
    rules,
    wish_requests,
)
from kidsmedia.deps import Device, get_conn, get_settings, require_device
from kidsmedia.gems import gem_balance
from kidsmedia.library import ThumbRef, group_cover, item_thumb
from kidsmedia.routes.activities import chapter_pictures, picture_ref, world_rows
from kidsmedia.routes.activities_admin import done_entries
from kidsmedia.routes.rewards import WishTexts, rule_group_names, stars_of

router = APIRouter(prefix="/api", tags=["device: logs"])

DEFAULT_ENTRIES = 30
MAX_ENTRIES = 100


# ---- gems ---------------------------------------------------------------------------------


class GemLogEntry(BaseModel):
    id: int
    delta: int
    kind: Literal["admin", "spend"]
    note: str | None  # admin: the parent's note, as written
    title: str | None  # spend: the file's title (snapshot)
    thumb: ThumbRef | None  # spend: the file's picture (or its group's), while the file exists
    created_at: datetime


class GemLogOut(BaseModel):
    balance: int
    entries: list[GemLogEntry]  # newest first


def gem_log(conn: Connection, profile_id: int, limit: int) -> GemLogOut:
    rows = conn.execute(
        select(gem_ledger)
        .where(gem_ledger.c.profile_id == profile_id)
        .order_by(gem_ledger.c.created_at.desc(), gem_ledger.c.id.desc())
        .limit(limit)
    ).all()
    media_ids = {r.media_id for r in rows if r.media_id is not None}
    items = {m.id: m for m in conn.execute(select(media).where(media.c.id.in_(media_ids))).all()}
    covers: dict[int, ThumbRef | None] = {}
    for gid in {m.group_id for m in items.values()}:
        g = conn.execute(select(groups).where(groups.c.id == gid)).first()
        covers[gid] = group_cover(conn, g) if g is not None else None

    def thumb(media_id: int | None) -> ThumbRef | None:
        item = items.get(media_id) if media_id is not None else None
        return item_thumb(item, covers.get(item.group_id)) if item is not None else None

    return GemLogOut(
        balance=gem_balance(conn, profile_id),
        entries=[
            GemLogEntry(
                id=r.id,
                delta=r.delta,
                kind=r.kind,
                note=r.note if r.kind == "admin" else None,
                title=r.title if r.kind == "spend" else None,
                thumb=thumb(r.media_id),
                created_at=from_db(r.created_at),
            )
            for r in rows
        ],
    )


@router.get("/gems/log", response_model=GemLogOut)
def get_gem_log(
    limit: int = Query(DEFAULT_ENTRIES, ge=1, le=MAX_ENTRIES),
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
) -> GemLogOut:
    """The gem balance and why it changed, newest first (§16.6)."""
    return gem_log(conn, device.profile_id, limit)


# ---- stars --------------------------------------------------------------------------------

# The kinds whose ref_id is a done task (`reopen` too, but that task is no longer done).
TASK_KINDS = ("earn", "chapter", "bonus")
StarKind = Literal["earn", "chapter", "bonus", "reopen", "swap", "wish"]


class StarTask(BaseModel):
    """The done task a star row is about, while it is done (§14.4b)."""

    world_id: int
    world_name: str  # in the story language (§14.7)
    position: int  # the chapter
    kind: str  # draw | calc | write | read
    title: str  # the task's title in the story language
    picture: ThumbRef | None  # the chapter's picture


class StarSwap(BaseModel):
    """What a swap gave, while the swap exists."""

    extra_items: int
    extra_minutes: int
    groups: list[str]


class StarLogEntry(BaseModel):
    id: int
    delta: int
    kind: StarKind
    task: StarTask | None  # earn, chapter, bonus: null once the task was opened again
    swap: StarSwap | None  # swap
    wish: dict[Lang, WishTexts] | None  # wish: its name, while the wish exists
    created_at: datetime


class StarLogOut(BaseModel):
    stars: int
    entries: list[StarLogEntry]  # newest first


def _tasks(
    conn: Connection, profile_id: int, done_ids: set[int], default_lang: Lang
) -> dict[int, StarTask]:
    if not done_ids:
        return {}
    rows = conn.execute(
        select(activity_done).where(
            activity_done.c.profile_id == profile_id, activity_done.c.id.in_(done_ids)
        )
    ).all()
    worlds = world_rows(conn)
    out: dict[int, StarTask] = {}
    for e in done_entries(conn, profile_id, list(rows), default_lang):
        pictures = chapter_pictures(worlds[e.world_id])
        out[e.id] = StarTask(
            world_id=e.world_id,
            world_name=e.world_name,
            position=e.position,
            kind=e.kind,
            title=e.task_title,
            picture=picture_ref(pictures[e.position - 1]) if e.position <= len(pictures) else None,
        )
    return out


def _swaps(conn: Connection, swap_ids: set[int]) -> dict[int, StarSwap]:
    out: dict[int, StarSwap] = {}
    for s in conn.execute(
        select(reward_swaps, rules.c.all_groups)
        .join(rules, rules.c.id == reward_swaps.c.rule_id)
        .where(reward_swaps.c.id.in_(swap_ids))
    ).all():
        out[s.id] = StarSwap(
            extra_items=s.extra_items or 0,
            extra_minutes=s.extra_minutes or 0,
            groups=rule_group_names(conn, s.rule_id, s.all_groups),
        )
    return out


def _wishes(conn: Connection, request_ids: set[int]) -> dict[int, Any]:
    return {
        r.id: r.texts
        for r in conn.execute(
            select(wish_requests.c.id, reward_wishes.c.texts)
            .join(reward_wishes, reward_wishes.c.id == wish_requests.c.wish_id)
            .where(wish_requests.c.id.in_(request_ids))
        ).all()
    }


def star_log(conn: Connection, profile_id: int, limit: int, default_lang: Lang) -> StarLogOut:
    rows = conn.execute(
        select(reward_ledger)
        .where(reward_ledger.c.profile_id == profile_id)
        .order_by(reward_ledger.c.created_at.desc(), reward_ledger.c.id.desc())
        .limit(limit)
    ).all()

    def refs(*kinds: str) -> set[int]:
        return {r.ref_id for r in rows if r.kind in kinds and r.ref_id is not None}

    tasks = _tasks(conn, profile_id, refs(*TASK_KINDS), default_lang)
    swaps = _swaps(conn, refs("swap"))
    wishes = _wishes(conn, refs("wish"))
    entries: list[StarLogEntry] = []
    for r in rows:
        ref = r.ref_id
        entries.append(
            StarLogEntry(
                id=r.id,
                delta=r.delta,
                kind=r.kind,
                task=tasks.get(ref) if ref is not None and r.kind in TASK_KINDS else None,
                swap=swaps.get(ref) if ref is not None and r.kind == "swap" else None,
                wish=wishes.get(ref) if ref is not None and r.kind == "wish" else None,
                created_at=from_db(r.created_at),
            )
        )
    return StarLogOut(stars=stars_of(conn, profile_id), entries=entries)


@router.get("/rewards/log", response_model=StarLogOut)
def get_star_log(
    limit: int = Query(DEFAULT_ENTRIES, ge=1, le=MAX_ENTRIES),
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> StarLogOut:
    """The stars and why they changed, newest first (§14.4b)."""
    return star_log(conn, device.profile_id, limit, settings.default_locale)
