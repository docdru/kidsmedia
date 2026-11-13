"""Admin: uploads, media items, thumbnails and group covers (SPEC §4.1, §5.4, §6.3, §6.4)."""

import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine, Row, delete, func, insert, select, update
from starlette.concurrency import run_in_threadpool

from kidsmedia import thumbs
from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import groups, insert_id, media
from kidsmedia.deps import (
    Admin,
    get_clock,
    get_conn,
    get_settings,
    require_admin,
    require_admin_csrf,
)
from kidsmedia.errors import ApiError
from kidsmedia.library import ThumbRef, group_cover, item_thumb
from kidsmedia.routes.groups import GroupOut, load_group

router = APIRouter(prefix="/api/admin", tags=["admin: media"])

CHUNK = 1024 * 1024


class MediaOut(BaseModel):
    id: int
    group_id: int
    title: str
    mime: str
    size: int
    duration_s: float
    width: int | None
    height: int | None
    faststart: bool | None
    has_embedded_art: bool
    sort: int
    thumb_source: Literal["pending", "embedded", "frame", "upload", "none"]
    thumb_frame_ms: int | None
    thumb: ThumbRef | None
    offline: bool


class MediaPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    group_id: int | None = None
    offline: bool | None = None  # M7


class MediaReorder(BaseModel):
    group_id: int
    ids: list[int]


class ThumbChoice(BaseModel):
    source: Literal["embedded", "frame"]
    t_ms: int | None = Field(default=None, ge=0)


class CoverChoice(BaseModel):
    media_id: int


class ApplyThumb(BaseModel):
    media_id: int  # use this item's thumbnail for every item without one


class Frame(BaseModel):
    t_ms: int
    url: str


def _media_row(conn: Connection, media_id: int) -> Row[Any]:
    row = conn.execute(select(media).where(media.c.id == media_id)).first()
    if row is None:
        raise ApiError(404, "media_not_found", media_id=media_id)
    return row


def load_media(conn: Connection, media_id: int) -> MediaOut:
    row = _media_row(conn, media_id)
    group = conn.execute(select(groups).where(groups.c.id == row.group_id)).one()
    return MediaOut(
        id=row.id,
        group_id=row.group_id,
        title=row.title,
        mime=row.mime,
        size=row.size,
        duration_s=row.duration_s,
        width=row.width,
        height=row.height,
        faststart=row.faststart,
        has_embedded_art=row.has_embedded_art,
        sort=row.sort,
        thumb_source=row.thumb_source,
        thumb_frame_ms=row.thumb_frame_ms,
        offline=row.offline,
        thumb=item_thumb(row, group_cover(conn, group)),
    )


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+-\s+", ": ", stem, count=1)
    return stem.strip()[:200] or "Untitled"


def generate_thumbnail(engine: Engine, settings: Settings, media_id: int) -> None:
    """Background job after upload: automatic thumbnail (SPEC §6.4)."""
    with engine.begin() as conn:
        row = conn.execute(select(media).where(media.c.id == media_id)).first()
        if row is None:
            return
        path = settings.media_dir / row.path
        is_video = row.mime.startswith("video/")
    has_art: bool | None = None
    try:
        probe = thumbs.probe(path)
        has_art = thumbs.has_embedded_picture(path, probe)
        source, img, frame_ms = thumbs.automatic_thumbnail(path, probe, is_video)
        key, aspect = (
            (None, None)
            if img is None
            else thumbs.write_thumb(img, settings.thumbs_dir, f"m{media_id}")
        )
    except thumbs.MediaError:
        source, key, aspect, frame_ms = "none", None, None, None
    with engine.begin() as conn:
        conn.execute(
            update(media)
            .where(media.c.id == media_id)
            .values(
                thumb_source=source,
                thumb_key=key,
                thumb_aspect=aspect,
                thumb_frame_ms=frame_ms,
                **({} if has_art is None else {"has_embedded_art": has_art}),
            )
        )


def _replace_thumb(
    conn: Connection,
    settings: Settings,
    row: Row[Any],
    source: str,
    img: Image.Image,
    frame_ms: int | None,
) -> None:
    key, aspect = thumbs.write_thumb(img, settings.thumbs_dir, f"m{row.id}")
    if row.thumb_key and row.thumb_key != key:
        thumbs.delete_thumb_files(settings.thumbs_dir, row.thumb_key)
    conn.execute(
        update(media)
        .where(media.c.id == row.id)
        .values(thumb_source=source, thumb_key=key, thumb_aspect=aspect, thumb_frame_ms=frame_ms)
    )


@router.put("/groups/{group_id}/upload", response_model=MediaOut, status_code=201)
async def upload(
    group_id: int,
    request: Request,
    background: BackgroundTasks,
    filename: str = Query(min_length=1, max_length=255),
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> MediaOut:
    """Upload one file as the raw request body (no multipart, streamed to disk)."""
    load_group(conn, group_id)
    limit = settings.max_upload_mb * 1024 * 1024
    tmp_dir = settings.media_dir / ".incoming"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / uuid.uuid4().hex  # the client's filename never reaches the filesystem
    size = 0
    try:
        # "xb" = O_EXCL: never follows or reuses an existing path (e.g. a planted symlink).
        with tmp.open("xb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise ApiError(413, "upload_too_large", max_mb=settings.max_upload_mb)
                fh.write(chunk)
        if size == 0:
            raise ApiError(422, "empty_upload")
        try:
            probe = await run_in_threadpool(thumbs.probe, tmp)
            kind = thumbs.classify(probe)
        except thumbs.MediaError as exc:
            raise ApiError(415, exc.reason, **exc.params) from exc
        faststart = await run_in_threadpool(thumbs.is_faststart, tmp) if kind.ext == "mp4" else None
        has_art = await run_in_threadpool(thumbs.has_embedded_picture, tmp, probe)
        rel = f"{group_id}/{uuid.uuid4().hex}.{kind.ext}"
        dest = settings.media_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)

    next_sort = conn.execute(
        select(func.coalesce(func.max(media.c.sort), -1) + 1).where(media.c.group_id == group_id)
    ).scalar_one()
    media_id = insert_id(
        conn.execute(
            insert(media).values(
                group_id=group_id,
                title=probe.title[:200] if probe.title else _title_from_filename(filename),
                path=rel,
                mime=kind.mime,
                size=size,
                duration_s=probe.duration_s,
                width=probe.width if kind.is_video else None,
                height=probe.height if kind.is_video else None,
                faststart=faststart,
                has_embedded_art=has_art,
                sort=next_sort,
                created_at=to_db(clock.now()),
                thumb_source="pending",
            )
        )
    )
    out = load_media(conn, media_id)
    conn.commit()  # the background job runs on its own connection and must see the row
    background.add_task(generate_thumbnail, request.app.state.engine, settings, media_id)
    return out


@router.get("/groups/{group_id}/media", response_model=list[MediaOut])
def list_media(
    group_id: int,
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
) -> list[MediaOut]:
    load_group(conn, group_id)
    ids = (
        conn.execute(
            select(media.c.id)
            .where(media.c.group_id == group_id)
            .order_by(media.c.sort, media.c.id)
        )
        .scalars()
        .all()
    )
    return [load_media(conn, i) for i in ids]


@router.get("/media/{media_id}", response_model=MediaOut)
def get_media(
    media_id: int,
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
) -> MediaOut:
    return load_media(conn, media_id)


@router.patch("/media/{media_id}", response_model=MediaOut)
def patch_media(
    media_id: int,
    body: MediaPatch,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> MediaOut:
    row = _media_row(conn, media_id)
    values: dict[str, object] = {}
    if body.title is not None:
        values["title"] = body.title.strip()
    if body.offline is not None:
        values["offline"] = body.offline
    if body.group_id is not None and body.group_id != row.group_id:
        load_group(conn, body.group_id)
        _detach_as_cover(conn, row.group_id, media_id)
        new_rel = f"{body.group_id}/{Path(row.path).name}"
        (settings.media_dir / new_rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(settings.media_dir / row.path, settings.media_dir / new_rel)
        values["group_id"] = body.group_id
        values["path"] = new_rel
        values["sort"] = conn.execute(
            select(func.coalesce(func.max(media.c.sort), -1) + 1).where(
                media.c.group_id == body.group_id
            )
        ).scalar_one()
    if values:
        conn.execute(update(media).where(media.c.id == media_id).values(**values))
    return load_media(conn, media_id)


def _detach_as_cover(conn: Connection, group_id: int, media_id: int) -> None:
    """SPEC §6.1: a group whose chosen cover item goes away falls back to auto."""
    conn.execute(
        update(groups)
        .where(
            groups.c.id == group_id,
            groups.c.cover_source == "item",
            groups.c.cover_media_id == media_id,
        )
        .values(cover_source="auto", cover_media_id=None)
    )


@router.delete("/media/{media_id}", status_code=204)
def delete_media(
    media_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Plays keep their snapshots (media_id -> NULL); positions and graces go (SPEC §6.1)."""
    row = _media_row(conn, media_id)
    _detach_as_cover(conn, row.group_id, media_id)
    conn.execute(delete(media).where(media.c.id == media_id))
    (settings.media_dir / row.path).unlink(missing_ok=True)
    if row.thumb_key:
        thumbs.delete_thumb_files(settings.thumbs_dir, row.thumb_key)


@router.post("/media/reorder", response_model=list[MediaOut])
def reorder_media(
    body: MediaReorder,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> list[MediaOut]:
    existing = (
        conn.execute(select(media.c.id).where(media.c.group_id == body.group_id)).scalars().all()
    )
    if sorted(body.ids) != sorted(existing):
        raise ApiError(422, "reorder_mismatch")
    for position, media_id in enumerate(body.ids):
        conn.execute(update(media).where(media.c.id == media_id).values(sort=position))
    return [load_media(conn, i) for i in body.ids]


@router.post("/media/{media_id}/optimize", response_model=MediaOut)
async def optimize_media(
    media_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> MediaOut:
    """Move the mp4 index to the front for progressive streaming, without re-encoding."""
    row = _media_row(conn, media_id)
    if not row.mime.startswith("video/") and not row.path.endswith(".m4a"):
        raise ApiError(422, "not_mp4")
    try:
        await run_in_threadpool(thumbs.make_faststart, settings.media_dir / row.path)
    except thumbs.MediaError as exc:
        raise ApiError(500, exc.reason) from exc
    size = (settings.media_dir / row.path).stat().st_size
    conn.execute(update(media).where(media.c.id == media_id).values(faststart=True, size=size))
    return load_media(conn, media_id)


# ---- thumbnails -------------------------------------------------------------------------


@router.get("/media/{media_id}/frames", response_model=list[Frame])
def frames(
    media_id: int,
    count: int = Query(8, ge=1, le=24),
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
) -> list[Frame]:
    """Evenly spaced frame positions; images come from /frame (SPEC §6.4 picker)."""
    row = _media_row(conn, media_id)
    if not row.mime.startswith("video/"):
        raise ApiError(422, "not_video")
    step = row.duration_s * 1000 / count
    points = [int(step * (i + 0.5)) for i in range(count)]
    return [Frame(t_ms=t, url=f"/api/admin/media/{media_id}/frame?t_ms={t}") for t in points]


@router.get("/media/{media_id}/frame", responses={200: {"content": {"image/jpeg": {}}}})
async def frame(
    media_id: int,
    t_ms: int = Query(ge=0),
    width: int = Query(480, ge=64, le=1024),
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    row = _media_row(conn, media_id)
    if not row.mime.startswith("video/"):
        raise ApiError(422, "not_video")
    t = min(t_ms / 1000, max(row.duration_s - 0.1, 0))
    try:
        img = await run_in_threadpool(
            thumbs.frame_at, settings.media_dir / row.path, t, False, width
        )
    except thumbs.MediaError as exc:
        raise ApiError(500, exc.reason) from exc
    return Response(
        thumbs.jpeg_bytes(img),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/media/{media_id}/thumb", response_model=MediaOut)
async def choose_thumb(
    media_id: int,
    body: ThumbChoice,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> MediaOut:
    row = _media_row(conn, media_id)
    path = settings.media_dir / row.path
    if body.source == "embedded":
        probe = await run_in_threadpool(thumbs.probe, path)
        img = await run_in_threadpool(thumbs.embedded_picture, path, probe)
        if img is None:
            raise ApiError(422, "no_embedded_picture")
        _replace_thumb(conn, settings, row, "embedded", img, None)
    else:
        if not row.mime.startswith("video/") or body.t_ms is None:
            raise ApiError(422, "frame_needs_video_and_t_ms")
        t = min(body.t_ms / 1000, max(row.duration_s - 0.1, 0))
        try:
            img = await run_in_threadpool(thumbs.frame_at, path, t)
        except thumbs.MediaError as exc:
            raise ApiError(500, exc.reason) from exc
        _replace_thumb(conn, settings, row, "frame", img, body.t_ms)
    return load_media(conn, media_id)


@router.put("/media/{media_id}/thumb", response_model=MediaOut)
async def upload_thumb(
    media_id: int,
    request: Request,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> MediaOut:
    row = _media_row(conn, media_id)
    img = _decode(await _read_limited(request))
    _replace_thumb(conn, settings, row, "upload", img, None)
    return load_media(conn, media_id)


@router.delete("/media/{media_id}/thumb", response_model=MediaOut)
async def reset_thumb(
    media_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> MediaOut:
    """Back to the automatic choice."""
    row = _media_row(conn, media_id)
    path = settings.media_dir / row.path
    probe = await run_in_threadpool(thumbs.probe, path)
    # Re-check: files uploaded before the tag fallback have has_embedded_art = false.
    has_art = await run_in_threadpool(thumbs.has_embedded_picture, path, probe)
    conn.execute(update(media).where(media.c.id == media_id).values(has_embedded_art=has_art))
    source, img, frame_ms = await run_in_threadpool(
        thumbs.automatic_thumbnail, path, probe, row.mime.startswith("video/")
    )
    if img is None:
        if row.thumb_key:
            thumbs.delete_thumb_files(settings.thumbs_dir, row.thumb_key)
        conn.execute(
            update(media)
            .where(media.c.id == media_id)
            .values(thumb_source="none", thumb_key=None, thumb_aspect=None, thumb_frame_ms=None)
        )
    else:
        _replace_thumb(conn, settings, row, source, img, frame_ms)
    return load_media(conn, media_id)


@router.post("/groups/{group_id}/thumbs/apply", response_model=list[MediaOut])
def apply_thumb_to_group(
    group_id: int,
    body: ApplyThumb,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> list[MediaOut]:
    """Use one item's picture for every item in the group that has none (SPEC §6.4)."""
    load_group(conn, group_id)
    source = _media_row(conn, body.media_id)
    if source.group_id != group_id or not source.thumb_key:
        raise ApiError(422, "source_has_no_thumbnail")
    targets = (
        conn.execute(
            select(media.c.id).where(media.c.group_id == group_id, media.c.thumb_source == "none")
        )
        .scalars()
        .all()
    )
    for target in targets:
        key = thumbs.copy_thumb(settings.thumbs_dir, source.thumb_key, f"m{target}")
        conn.execute(
            update(media)
            .where(media.c.id == target)
            .values(
                thumb_source="upload",
                thumb_key=key,
                thumb_aspect=source.thumb_aspect,
                thumb_frame_ms=None,
            )
        )
    return [load_media(conn, i) for i in targets]


# ---- group covers ------------------------------------------------------------------------


@router.put("/groups/{group_id}/cover", response_model=GroupOut)
async def upload_cover(
    group_id: int,
    request: Request,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> GroupOut:
    load_group(conn, group_id)
    img = _decode(await _read_limited(request))
    key, aspect = thumbs.write_thumb(img, settings.thumbs_dir, f"g{group_id}")
    old = conn.execute(select(groups.c.cover_key).where(groups.c.id == group_id)).scalar_one()
    if old and old != key:
        thumbs.delete_thumb_files(settings.thumbs_dir, old)
    conn.execute(
        update(groups)
        .where(groups.c.id == group_id)
        .values(cover_source="upload", cover_key=key, cover_aspect=aspect, cover_media_id=None)
    )
    return load_group(conn, group_id)


@router.post("/groups/{group_id}/cover", response_model=GroupOut)
def choose_cover(
    group_id: int,
    body: CoverChoice,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> GroupOut:
    load_group(conn, group_id)
    row = _media_row(conn, body.media_id)
    if row.group_id != group_id:
        raise ApiError(422, "media_not_in_group")
    conn.execute(
        update(groups)
        .where(groups.c.id == group_id)
        .values(cover_source="item", cover_media_id=body.media_id)
    )
    return load_group(conn, group_id)


@router.delete("/groups/{group_id}/cover", response_model=GroupOut)
def reset_cover(
    group_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> GroupOut:
    load_group(conn, group_id)
    old = conn.execute(select(groups.c.cover_key).where(groups.c.id == group_id)).scalar_one()
    if old:
        thumbs.delete_thumb_files(settings.thumbs_dir, old)
    conn.execute(
        update(groups)
        .where(groups.c.id == group_id)
        .values(cover_source="auto", cover_media_id=None, cover_key=None, cover_aspect=None)
    )
    return load_group(conn, group_id)


async def _read_limited(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > thumbs.MAX_IMAGE_UPLOAD_BYTES:
            raise ApiError(
                413, "image_too_large", max_mb=thumbs.MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)
            )
    return bytes(data)


def _decode(data: bytes) -> Image.Image:
    try:
        return thumbs.open_image(data)
    except thumbs.MediaError as exc:
        raise ApiError(422, exc.reason) from exc
