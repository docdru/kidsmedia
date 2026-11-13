"""Admin: groups (SPEC §5.4). Each group is one tile on the tablet."""

import shutil
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Connection, delete, func, insert, select, update

from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import groups, insert_id, media, positions
from kidsmedia.deps import (
    Admin,
    get_clock,
    get_conn,
    get_settings,
    require_admin,
    require_admin_csrf,
)
from kidsmedia.errors import ApiError
from kidsmedia.library import ThumbRef, group_cover
from kidsmedia.thumbs import delete_thumb_files

router = APIRouter(prefix="/api/admin/groups", tags=["admin: groups"])

Kind = Literal["music", "audiobook", "video"]
COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
DEFAULT_COLORS: dict[str, str] = {"music": "#0F7A70", "audiobook": "#4552C7", "video": "#7B3FB0"}


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Kind
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    resume: bool | None = None  # default: true except for music (SPEC §5.3)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    resume: bool | None = None
    offline: bool | None = None  # M7: tablets keep a copy for offline use


class GroupOut(BaseModel):
    id: int
    name: str
    kind: Kind
    color: str
    resume: bool
    offline: bool
    sort: int
    item_count: int
    total_duration_s: float
    cover_source: Literal["auto", "item", "upload"]
    cover_media_id: int | None
    cover: ThumbRef | None


class Reorder(BaseModel):
    ids: list[int]


def load_group(conn: Connection, group_id: int) -> GroupOut:
    row = conn.execute(select(groups).where(groups.c.id == group_id)).first()
    if row is None:
        raise ApiError(404, "group_not_found", group_id=group_id)
    stats = conn.execute(
        select(func.count(media.c.id), func.coalesce(func.sum(media.c.duration_s), 0.0)).where(
            media.c.group_id == group_id
        )
    ).one()
    return GroupOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        color=row.color,
        resume=row.resume,
        offline=row.offline,
        sort=row.sort,
        item_count=stats[0],
        total_duration_s=float(stats[1]),
        cover_source=row.cover_source,
        cover_media_id=row.cover_media_id,
        cover=group_cover(conn, row),
    )


@router.get("", response_model=list[GroupOut])
def list_groups(
    _: Admin = Depends(require_admin), conn: Connection = Depends(get_conn, scope="function")
) -> list[GroupOut]:
    ids = conn.execute(select(groups.c.id).order_by(groups.c.sort, groups.c.id)).scalars().all()
    return [load_group(conn, i) for i in ids]


@router.post("", response_model=GroupOut, status_code=201)
def create_group(
    body: GroupIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> GroupOut:
    next_sort = conn.execute(select(func.coalesce(func.max(groups.c.sort), -1) + 1)).scalar_one()
    group_id = insert_id(
        conn.execute(
            insert(groups).values(
                name=body.name.strip(),
                kind=body.kind,
                color=body.color or DEFAULT_COLORS[body.kind],
                resume=body.kind != "music" if body.resume is None else body.resume,
                cover_source="auto",
                sort=next_sort,
                created_at=to_db(clock.now()),
            )
        )
    )
    return load_group(conn, group_id)


@router.get("/{group_id}", response_model=GroupOut)
def get_group(
    group_id: int,
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
) -> GroupOut:
    return load_group(conn, group_id)


@router.patch("/{group_id}", response_model=GroupOut)
def patch_group(
    group_id: int,
    body: GroupPatch,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> GroupOut:
    load_group(conn, group_id)
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in values:
        values["name"] = values["name"].strip()
    if values:
        conn.execute(update(groups).where(groups.c.id == group_id).values(**values))
    if values.get("resume") is False:
        # SPEC §5.3: switching resume off deletes the group's stored positions.
        conn.execute(
            delete(positions).where(
                positions.c.media_id.in_(select(media.c.id).where(media.c.group_id == group_id))
            )
        )
    return load_group(conn, group_id)


@router.delete("/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Deletes the group, its media rows and files (SPEC §6.1). Play history is kept."""
    load_group(conn, group_id)
    keys = (
        conn.execute(
            select(media.c.thumb_key).where(
                media.c.group_id == group_id, media.c.thumb_key.is_not(None)
            )
        )
        .scalars()
        .all()
    )
    cover_key = conn.execute(select(groups.c.cover_key).where(groups.c.id == group_id)).scalar_one()
    conn.execute(update(groups).where(groups.c.id == group_id).values(cover_media_id=None))
    conn.execute(delete(media).where(media.c.group_id == group_id))
    conn.execute(delete(groups).where(groups.c.id == group_id))
    shutil.rmtree(settings.media_dir / str(group_id), ignore_errors=True)
    for key in [*keys, cover_key]:
        if key:
            delete_thumb_files(settings.thumbs_dir, key)


@router.post("/reorder", response_model=list[GroupOut])
def reorder_groups(
    body: Reorder,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> list[GroupOut]:
    existing = set(conn.execute(select(groups.c.id)).scalars().all())
    if sorted(body.ids) != sorted(existing):
        raise ApiError(422, "reorder_mismatch")
    for position, group_id in enumerate(body.ids):
        conn.execute(update(groups).where(groups.c.id == group_id).values(sort=position))
    return [load_group(conn, i) for i in body.ids]
