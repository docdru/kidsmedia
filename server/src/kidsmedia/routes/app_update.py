"""Self-update (SPEC §4.3): the parent uploads a signed APK, tablets fetch the latest."""

import hashlib
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Connection, Engine, delete, insert, select

from kidsmedia.clock import Clock, from_db, to_db
from kidsmedia.config import Settings
from kidsmedia.db import app_releases
from kidsmedia.deps import (
    Admin,
    Device,
    get_clock,
    get_conn,
    get_settings,
    require_admin,
    require_admin_csrf,
    require_device,
)
from kidsmedia.errors import ApiError

router = APIRouter(tags=["app update"])
log = logging.getLogger("kidsmedia")

MAX_APK_BYTES = 200 * 1024 * 1024
ZIP_MAGIC = b"PK\x03\x04"  # an APK is a zip file
KEEP_RELEASES = 3


class Release(BaseModel):
    version_name: str
    version_code: int
    size: int
    sha256: str
    uploaded_at: datetime
    url: str = "/api/app/apk"


def _latest(conn: Connection) -> Release | None:
    row = conn.execute(select(app_releases).order_by(app_releases.c.version_code.desc())).first()
    if row is None:
        return None
    return Release(
        version_name=row.version_name,
        version_code=row.version_code,
        size=row.size,
        sha256=row.sha256,
        uploaded_at=from_db(row.uploaded_at),
    )


@router.get("/api/admin/app", response_model=Release | None)
def admin_latest(
    _: Admin = Depends(require_admin), conn: Connection = Depends(get_conn, scope="function")
) -> Release | None:
    return _latest(conn)


@router.put("/api/admin/app/apk", response_model=Release, status_code=201)
async def upload_apk(
    request: Request,
    version_name: str = Query(min_length=1, max_length=40, pattern=r"^[0-9A-Za-z.+-]+$"),
    version_code: int = Query(ge=1, le=2_100_000_000),
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> Release:
    """Raw APK body. Tablets only install it if its signature matches (Android checks)."""
    latest = _latest(conn)
    if latest is not None and version_code <= latest.version_code:
        raise ApiError(409, "version_not_newer", latest=latest.version_code)
    settings.apk_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.apk"  # the upload's name never reaches the filesystem
    tmp = settings.apk_dir / f".{name}"
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("xb") as fh:
            async for chunk in request.stream():
                if size == 0 and chunk and not chunk.startswith(ZIP_MAGIC[: len(chunk)]):
                    raise ApiError(415, "not_an_apk")
                size += len(chunk)
                if size > MAX_APK_BYTES:
                    raise ApiError(413, "upload_too_large", max_mb=MAX_APK_BYTES // (1024 * 1024))
                digest.update(chunk)
                fh.write(chunk)
        if size < len(ZIP_MAGIC):
            raise ApiError(415, "not_an_apk")
        tmp.replace(settings.apk_dir / name)
    finally:
        tmp.unlink(missing_ok=True)
    conn.execute(
        insert(app_releases).values(
            version_name=version_name,
            version_code=version_code,
            file=name,
            size=size,
            sha256=digest.hexdigest(),
            uploaded_at=to_db(clock.now()),
        )
    )
    _prune(conn, settings)
    release = _latest(conn)
    if release is None:  # pragma: no cover - just inserted
        raise ApiError(500, "release_missing")
    return release


def _prune(conn: Connection, settings: Settings) -> None:
    rows = conn.execute(
        select(app_releases.c.id, app_releases.c.file).order_by(app_releases.c.version_code.desc())
    ).all()
    for row in rows[KEEP_RELEASES:]:
        (settings.apk_dir / row.file).unlink(missing_ok=True)
        conn.execute(delete(app_releases).where(app_releases.c.id == row.id))


class _Bundle(BaseModel):
    """release.json written by release.yml next to the APK it baked into the image."""

    version_name: str = Field(min_length=1, max_length=40, pattern=r"^[0-9A-Za-z.+-]+$")
    version_code: int = Field(ge=1, le=2_100_000_000)
    file: str = Field(pattern=r"^[0-9A-Za-z._-]+\.apk$")


def import_bundled_apk(engine: Engine, settings: Settings, clock: Clock) -> None:
    """Make the APK shipped inside a release image the tablets' update (SPEC §4.3).

    Runs on every start. A version that is already known (bundled before, or uploaded by
    the parent) is left alone, so restarts are no-ops and a newer upload still wins.
    Problems are logged, never fatal: the server must start without an APK.
    """
    if settings.bundled_apk_dir is None:
        return
    manifest = settings.bundled_apk_dir / "release.json"
    if not manifest.is_file():
        return
    try:
        bundle = _Bundle.model_validate_json(manifest.read_bytes())
    except ValidationError as e:
        log.warning("Ignoring bundled APK: bad %s (%s)", manifest, e.errors()[0]["msg"])
        return
    src = settings.bundled_apk_dir / bundle.file
    if not src.is_file():
        log.warning("Ignoring bundled APK: %s is missing", src)
        return
    with engine.begin() as conn:
        known = conn.execute(
            select(app_releases.c.id).where(app_releases.c.version_code == bundle.version_code)
        ).first()
        if known is not None:
            return
        settings.apk_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.apk"
        tmp = settings.apk_dir / f".{name}"
        digest = hashlib.sha256()
        size = 0
        try:
            with src.open("rb") as fin, tmp.open("xb") as fout:
                while chunk := fin.read(1024 * 1024):
                    if size == 0 and not chunk.startswith(ZIP_MAGIC):
                        log.warning("Ignoring bundled APK: %s is not an APK", src)
                        return
                    size += len(chunk)
                    digest.update(chunk)
                    fout.write(chunk)
            if size == 0:
                log.warning("Ignoring bundled APK: %s is empty", src)
                return
            tmp.replace(settings.apk_dir / name)
        finally:
            tmp.unlink(missing_ok=True)
        conn.execute(
            insert(app_releases).values(
                version_name=bundle.version_name,
                version_code=bundle.version_code,
                file=name,
                size=size,
                sha256=digest.hexdigest(),
                uploaded_at=to_db(clock.now()),
            )
        )
        _prune(conn, settings)
    log.info("Imported the bundled app %s (%d)", bundle.version_name, bundle.version_code)


@router.get("/api/app/latest", response_model=Release)
def device_latest(
    _: Device = Depends(require_device), conn: Connection = Depends(get_conn, scope="function")
) -> Release:
    release = _latest(conn)
    if release is None:
        raise ApiError(404, "no_release")
    return release


@router.get(
    "/api/app/apk", responses={200: {"content": {"application/vnd.android.package-archive": {}}}}
)
def device_apk(
    _: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    row = conn.execute(
        select(app_releases.c.file).order_by(app_releases.c.version_code.desc())
    ).first()
    if row is None:
        raise ApiError(404, "no_release")
    media_type = "application/vnd.android.package-archive"
    if settings.accel_redirect:
        return Response(
            headers={"X-Accel-Redirect": f"/_apk/{row.file}", "Cache-Control": "private, no-cache"},
            media_type=media_type,
        )
    return FileResponse(settings.apk_dir / row.file, media_type=media_type)
