"""Admin: the gem balance and its ledger (SPEC §16.1, §16.3)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Connection, insert, select

from kidsmedia.clock import Clock, from_db, to_db
from kidsmedia.db import begin_immediate, gem_ledger
from kidsmedia.deps import Admin, get_clock, get_conn, require_admin, require_admin_csrf
from kidsmedia.errors import ApiError
from kidsmedia.gems import gem_balance
from kidsmedia.routes.devices import DEFAULT_PROFILE_ID

router = APIRouter(prefix="/api/admin", tags=["admin: gems"])

MAX_DELTA = 20
DEFAULT_ENTRIES = 50


class GemEntry(BaseModel):
    id: int
    delta: int
    kind: Literal["admin", "spend"]
    title: str | None  # spend: the file's title (snapshot)
    note: str | None  # admin: the parent's note
    created_at: datetime


class GemsOut(BaseModel):
    balance: int
    entries: list[GemEntry]  # newest first


class GemsIn(BaseModel):
    delta: int = Field(ge=-MAX_DELTA, le=MAX_DELTA)
    note: str | None = Field(default=None, max_length=140)

    @field_validator("delta")
    @classmethod
    def _not_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("add or remove at least one gem")
        return v


def gems_state(conn: Connection, limit: int = DEFAULT_ENTRIES) -> GemsOut:
    rows = conn.execute(
        select(gem_ledger)
        .where(gem_ledger.c.profile_id == DEFAULT_PROFILE_ID)
        .order_by(gem_ledger.c.created_at.desc(), gem_ledger.c.id.desc())
        .limit(limit)
    )
    return GemsOut(
        balance=gem_balance(conn, DEFAULT_PROFILE_ID),
        entries=[
            GemEntry(
                id=r.id,
                delta=r.delta,
                kind=r.kind,
                title=r.title,
                note=r.note,
                created_at=from_db(r.created_at),
            )
            for r in rows
        ],
    )


@router.get("/gems", response_model=GemsOut)
def get_gems(
    limit: int = Query(DEFAULT_ENTRIES, ge=1, le=500),
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
) -> GemsOut:
    """The balance and the newest ledger entries (§16.3)."""
    return gems_state(conn, limit)


@router.post("/gems", response_model=GemsOut)
def change_gems(
    body: GemsIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> GemsOut:
    """The parent adds or removes gems (`kind = 'admin'`). A removal can't take the
    balance below 0 (422 not_enough_gems, §16.1); check and write are one atomic step."""
    begin_immediate(conn)
    balance = gem_balance(conn, DEFAULT_PROFILE_ID)
    if balance + body.delta < 0:
        raise ApiError(422, "not_enough_gems", have=balance, need=-body.delta)
    conn.execute(
        insert(gem_ledger).values(
            profile_id=DEFAULT_PROFILE_ID,
            delta=body.delta,
            kind="admin",
            note=(body.note or "").strip() or None,
            created_at=to_db(clock.now()),
        )
    )
    return gems_state(conn)
