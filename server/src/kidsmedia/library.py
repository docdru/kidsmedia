"""Shared read models: thumbnails, group covers, media items (SPEC §5.2, §6.4)."""

import re
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import Connection, Row, select

from kidsmedia.db import media

# media, group, world cover, world chapter picture (w<id>c<n>)
THUMB_KEY_RE = re.compile(r"^(?:[mgw]\d+|w\d+c\d+)-[0-9a-f]{6}$")


class ThumbRef(BaseModel):
    url: str
    aspect: float
    source: Literal["embedded", "frame", "upload", "group_cover"] | None = None


def thumb_ref(key: str | None, aspect: float | None, source: Any = None) -> ThumbRef | None:
    if not key:
        return None
    return ThumbRef(url=f"/api/thumbs/{key}.webp", aspect=aspect or 1.0, source=source)


def group_cover(conn: Connection, group: Row[Any]) -> ThumbRef | None:
    """Resolve a group's cover at read time (SPEC §6.4, §6.1).

    upload -> the uploaded picture; item -> that item's thumbnail; otherwise, and as a
    fallback when the chosen item is gone (cover_media_id NULL), the first item with a
    thumbnail by sort order ("auto"). Content-hashed keys give a new URL on change.
    """
    if group.cover_source == "upload" and group.cover_key:
        return thumb_ref(group.cover_key, group.cover_aspect, "upload")
    if group.cover_source == "item" and group.cover_media_id is not None:
        row = conn.execute(
            select(media.c.thumb_key, media.c.thumb_aspect).where(
                media.c.id == group.cover_media_id
            )
        ).first()
        if row is not None and row.thumb_key:
            return thumb_ref(row.thumb_key, row.thumb_aspect)
    row = conn.execute(
        select(media.c.thumb_key, media.c.thumb_aspect)
        .where(media.c.group_id == group.id, media.c.thumb_key.is_not(None))
        .order_by(media.c.sort, media.c.id)
        .limit(1)
    ).first()
    return thumb_ref(row.thumb_key, row.thumb_aspect) if row is not None else None


def item_thumb(item: Row[Any], cover: ThumbRef | None) -> ThumbRef | None:
    """An item's own thumbnail, or the group cover when it has none (SPEC §6.4 step 3)."""
    own = thumb_ref(item.thumb_key, item.thumb_aspect, item.thumb_source)
    if own is not None:
        return own
    if cover is not None:
        return ThumbRef(url=cover.url, aspect=cover.aspect, source="group_cover")
    return None
