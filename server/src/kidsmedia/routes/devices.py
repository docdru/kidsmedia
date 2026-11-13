"""Admin: tablets and pairing codes (SPEC §5.1)."""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Row, delete, insert, select, update

from kidsmedia.clock import Clock, from_db, to_db
from kidsmedia.db import devices, insert_id, pairing_codes
from kidsmedia.deps import Admin, get_clock, get_conn, require_admin, require_admin_csrf
from kidsmedia.errors import ApiError
from kidsmedia.security import new_pairing_code

router = APIRouter(prefix="/api/admin/devices", tags=["admin: devices"])

PAIRING_TTL = timedelta(minutes=15)
DEFAULT_PROFILE_ID = 1  # v1: one child (SPEC §6.1)
MIN_VOLUME_PERCENT = 5  # lower would be inaudible and look like a broken tablet


class DeviceIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    # Cap on the tablet's media volume in % of its maximum. null = no cap. In PATCH,
    # leaving the field out keeps the current value; sending null removes the cap.
    max_volume_percent: int | None = Field(default=None, ge=MIN_VOLUME_PERCENT, le=100)


class Pairing(BaseModel):
    code: str
    expires_at: datetime


class DeviceOut(BaseModel):
    id: int
    name: str
    profile_id: int
    status: str  # waiting_for_pairing | active | revoked
    app_version: str | None
    created_at: datetime
    paired_at: datetime | None
    last_seen_at: datetime | None
    max_volume_percent: int | None = None
    pairing: Pairing | None = None


def _status(row: Row[Any]) -> str:
    if row.revoked_at is not None:
        return "revoked"
    return "active" if row.token_hash else "waiting_for_pairing"


def load_device(conn: Connection, device_id: int, clock: Clock) -> DeviceOut:
    row = conn.execute(select(devices).where(devices.c.id == device_id)).first()
    if row is None:
        raise ApiError(404, "device_not_found", device_id=device_id)
    code = conn.execute(
        select(pairing_codes).where(
            pairing_codes.c.device_id == device_id, pairing_codes.c.expires_at > to_db(clock.now())
        )
    ).first()
    return DeviceOut(
        id=row.id,
        name=row.name,
        profile_id=row.profile_id,
        status=_status(row),
        app_version=row.app_version,
        created_at=from_db(row.created_at),
        paired_at=from_db(row.paired_at) if row.paired_at else None,
        last_seen_at=from_db(row.last_seen_at) if row.last_seen_at else None,
        max_volume_percent=row.max_volume_percent,
        pairing=Pairing(code=code.code, expires_at=from_db(code.expires_at)) if code else None,
    )


def _issue_code(conn: Connection, device_id: int, clock: Clock) -> None:
    now = clock.now()
    conn.execute(delete(pairing_codes).where(pairing_codes.c.expires_at <= to_db(now)))
    conn.execute(delete(pairing_codes).where(pairing_codes.c.device_id == device_id))
    while True:
        code = new_pairing_code()
        if (
            conn.execute(select(pairing_codes.c.code).where(pairing_codes.c.code == code)).first()
            is None
        ):
            break
    conn.execute(
        insert(pairing_codes).values(
            code=code, device_id=device_id, expires_at=to_db(now + PAIRING_TTL)
        )
    )


@router.get("", response_model=list[DeviceOut])
def list_devices(
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> list[DeviceOut]:
    ids = conn.execute(select(devices.c.id).order_by(devices.c.id)).scalars().all()
    return [load_device(conn, i, clock) for i in ids]


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(
    body: DeviceIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> DeviceOut:
    """Create a tablet entry and a 6-digit pairing code valid for 15 minutes."""
    device_id = insert_id(
        conn.execute(
            insert(devices).values(
                profile_id=DEFAULT_PROFILE_ID,
                name=(body.name or "").strip(),
                max_volume_percent=body.max_volume_percent,
                created_at=to_db(clock.now()),
            )
        )
    )
    _issue_code(conn, device_id, clock)
    return load_device(conn, device_id, clock)


@router.post("/{device_id}/pairing-code", response_model=DeviceOut)
def new_code(
    device_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> DeviceOut:
    """'Pair again': a fresh code. The current token stays valid until the new pairing succeeds."""
    load_device(conn, device_id, clock)
    _issue_code(conn, device_id, clock)
    return load_device(conn, device_id, clock)


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: int,
    body: DeviceIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> DeviceOut:
    """Rename a tablet or change its volume cap. The tablet picks up the cap with its
    next library refresh (about once a minute) and keeps it while offline."""
    load_device(conn, device_id, clock)
    values: dict[str, object] = {}
    if body.name:
        values["name"] = body.name.strip()
    if "max_volume_percent" in body.model_fields_set:
        values["max_volume_percent"] = body.max_volume_percent
    if values:
        conn.execute(update(devices).where(devices.c.id == device_id).values(**values))
    return load_device(conn, device_id, clock)


@router.delete("/{device_id}", status_code=204)
def revoke_device(
    device_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> None:
    """Revoke: the token stops working immediately (SPEC §5.1). The row stays for history."""
    load_device(conn, device_id, clock)
    conn.execute(
        update(devices)
        .where(devices.c.id == device_id)
        .values(token_hash=None, revoked_at=to_db(clock.now()))
    )
    conn.execute(delete(pairing_codes).where(pairing_codes.c.device_id == device_id))
