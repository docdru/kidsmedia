"""Photos of drawings and stories (SPEC §15.3): the device upload and the admin's photo files
(the list, bonus stars and "open again" of done tasks: test_done_admin.py, §17).

The fixed clock starts on Saturday 2026-11-07 14:20 Europe/Berlin; a queued world's first
chapter is unlocked, the second (Mon 9 Nov) isn't.
"""

import io
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import ExifTags, Image
from sqlalchemy import func, select

from kidsmedia import photos
from kidsmedia.clock import FixedClock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import activity_done, activity_photos, make_engine, reward_ledger
from tests.conftest import BERLIN
from tests.test_activities_api import import_json, pack, queued_world
from tests.test_play import bearer

GREEN = (10, 200, 10)
RED = (200, 30, 30)


def jpeg(size: tuple[int, int] = (800, 400), orientation: int = 6) -> bytes:
    """A JPEG as a phone camera writes it: an EXIF orientation, the maker and a location. The
    top-left corner is green, so the rotation can be checked."""
    img = Image.new("RGB", size, RED)
    img.paste(GREEN, (0, 0, 100, 100))
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.Base.Make] = "PhoneMaker"
    exif.get_ifd(ExifTags.IFD.GPSInfo).update(
        {ExifTags.GPS.GPSLatitudeRef: "N", ExifTags.GPS.GPSLatitude: (52.0, 31.0, 12.0)}
    )
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def upload(
    c: TestClient,
    token: str,
    world_id: int,
    body: bytes | None = None,
    position: int = 1,
    kind: str = "draw",
    done_at: datetime | None = None,
) -> Any:
    params: dict[str, str | int] = {"world_id": world_id, "position": position, "kind": kind}
    if done_at is not None:
        params["done_at"] = done_at.isoformat()
    return c.post(
        "/api/activities/photo",
        params=params,
        content=jpeg() if body is None else body,
        headers=bearer(token) | {"Content-Type": "image/jpeg"},
    )


def photo_rows(settings: Settings) -> list[Any]:
    with make_engine(settings.db_path).connect() as conn:
        return list(conn.execute(select(activity_photos).order_by(activity_photos.c.id)).all())


def done_rows(settings: Settings) -> list[Any]:
    with make_engine(settings.db_path).connect() as conn:
        return list(conn.execute(select(activity_done).order_by(activity_done.c.id)).all())


def ledger(settings: Settings) -> list[tuple[str, int, int | None]]:
    with make_engine(settings.db_path).connect() as conn:
        rows = conn.execute(select(reward_ledger).order_by(reward_ledger.c.id)).all()
        return [(r.kind, r.delta, r.ref_id) for r in rows]


def photo_files(settings: Settings) -> set[str]:
    d = settings.photos_dir
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


# ---- device -------------------------------------------------------------------------------


def test_a_photo_is_stored_clean_and_upright_and_finishes_the_task(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    r = upload(admin, device_token, world_id)
    assert r.status_code == 200, r.text
    assert r.json()["world"]["chapters"][0]["done_kinds"] == ["draw"]  # the GET body

    [row] = photo_rows(settings)
    [done] = done_rows(settings)
    assert row.done_id == done.id
    assert (row.world_id, row.position, row.kind) == (world_id, 1, "draw")
    assert (row.world_name, row.task_title) == ("World", "One")  # story language: en
    assert photos.PHOTO_KEY_RE.match(row.key)
    assert row.key.startswith(f"p{row.id}-")
    assert (row.width, row.height) == (400, 800)  # EXIF orientation 6: turned upright
    assert row.taken_at == done.done_at
    assert ledger(settings) == [("earn", 1, done.id)]

    assert photo_files(settings) == {f"{row.key}-lg.webp", f"{row.key}-sm.webp"}
    for size, expected in (("lg", (400, 800)), ("sm", (160, 320))):
        path = settings.photos_dir / f"{row.key}-{size}.webp"
        raw = path.read_bytes()
        img = Image.open(path)
        assert img.format == "WEBP"
        assert img.size == expected
        # No metadata at all: no EXIF (orientation, maker, location), no XMP, no ICC.
        assert dict(img.getexif()) == {}
        assert not {"exif", "xmp", "icc_profile"} & set(img.info)
        assert b"Exif" not in raw
        assert b"PhoneMaker" not in raw
        # The green top-left corner of the sensor image is top-right once upright.
        rgb = img.convert("RGB")
        assert rgb.getpixel((img.width - 3, 3)) == pytest.approx(GREEN, abs=20)
        assert rgb.getpixel((3, 3)) == pytest.approx(RED, abs=20)


def test_a_second_photo_is_stored_but_earns_nothing(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    assert upload(admin, device_token, world_id).status_code == 200
    r = upload(admin, device_token, world_id, jpeg((300, 200), orientation=1))
    assert r.status_code == 200, r.text
    first, second = photo_rows(settings)
    assert first.done_id == second.done_id
    assert first.key != second.key
    assert (second.width, second.height) == (300, 200)
    assert len(done_rows(settings)) == 1
    assert [kind for kind, _, _ in ledger(settings)] == ["earn"]
    assert len(photo_files(settings)) == 4


def test_a_photo_of_a_task_done_without_one(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    """An older app's done tap first, a photo later: linked to the same done row."""
    world_id = queued_world(admin)
    admin.post(
        "/api/activities/done",
        json={"world_id": world_id, "position": 1, "kind": "write"},
        headers=bearer(device_token),
    )
    assert upload(admin, device_token, world_id, kind="write").status_code == 200
    [done] = done_rows(settings)
    [row] = photo_rows(settings)
    assert row.done_id == done.id
    assert row.task_title == "T"
    assert ledger(settings) == [("earn", 1, done.id)]


@pytest.mark.parametrize("kind", ["calc", "read"])
def test_only_drawings_and_stories(
    admin: TestClient, device_token: str, settings: Settings, kind: str
) -> None:
    world_id = queued_world(admin)
    r = upload(admin, device_token, world_id, kind=kind)
    assert r.status_code == 422
    assert r.json()["reason"] == "photo_not_allowed"
    assert upload(admin, device_token, world_id, kind="paint").status_code == 422
    assert photo_rows(settings) == []
    assert done_rows(settings) == []


@pytest.mark.parametrize("body", [b"", b"not a picture", jpeg()[:200]])
def test_a_body_that_doesnt_decode(
    admin: TestClient, device_token: str, settings: Settings, body: bytes
) -> None:
    world_id = queued_world(admin)
    r = upload(admin, device_token, world_id, body)
    assert r.status_code == 422
    assert r.json()["reason"] == "bad_image"
    assert photo_rows(settings) == []
    assert done_rows(settings) == []  # nothing is marked done either
    assert photo_files(settings) == set()


def test_a_photo_over_15_mb(admin: TestClient, device_token: str, settings: Settings) -> None:
    world_id = queued_world(admin)
    r = upload(admin, device_token, world_id, b"\xff" * (photos.MAX_PHOTO_BYTES + 1))
    assert r.status_code == 413
    assert r.json() == {"reason": "photo_too_large", "params": {"max_mb": 15}}
    assert done_rows(settings) == []


def test_a_photo_is_judged_like_done(
    admin: TestClient, device_token: str, settings: Settings, clock: FixedClock
) -> None:
    world_id = queued_world(admin)
    r = upload(admin, device_token, world_id, position=2)
    assert r.status_code == 409
    assert r.json()["reason"] == "activity_locked"
    for r in (upload(admin, device_token, world_id, position=7), upload(admin, device_token, 999)):
        assert r.status_code == 404
        assert r.json()["reason"] == "unknown_task"
    r = upload(admin, device_token, world_id, done_at=clock.now() - timedelta(days=7, minutes=1))
    assert r.status_code == 409
    assert r.json()["reason"] == "activity_too_old"
    admin.put(
        "/api/admin/activities/settings",
        json={
            "days": [0, 1, 2, 3, 4, 5],
            "unlock_time": "07:00",
            "windows": [{"days": [5], "from": "09:00", "to": "12:00"}],
        },
    )
    r = upload(admin, device_token, world_id)
    assert r.status_code == 409
    assert r.json()["reason"] == "activities_closed"
    assert photo_rows(settings) == []
    assert done_rows(settings) == []


def test_a_photo_taken_offline_keeps_its_moment(
    admin: TestClient, device_token: str, settings: Settings, clock: FixedClock
) -> None:
    world_id = queued_world(admin)
    taken = datetime(2026, 11, 7, 9, 30, tzinfo=BERLIN)
    clock.set(datetime(2026, 11, 9, 8, 0, tzinfo=BERLIN))  # sent on Monday
    r = upload(admin, device_token, world_id, done_at=taken)
    assert r.status_code == 200, r.text
    [done] = done_rows(settings)
    [row] = photo_rows(settings)
    assert done.day == "2026-11-07"
    assert row.taken_at == to_db(taken) == done.done_at
    assert row.created_at > row.taken_at


def test_names_are_snapshots_in_the_story_language(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    body = pack()
    body["chapters"][0]["tasks"][0]["texts"]["de"]["title"] = "Male"
    world_id = import_json(admin, body).json()["world_id"]
    admin.put(
        "/api/admin/activities/queue", json={"world_ids": [world_id], "langs": {world_id: "de"}}
    )
    assert upload(admin, device_token, world_id).status_code == 200
    [row] = photo_rows(settings)
    assert (row.world_name, row.task_title) == ("Welt", "Male")


def test_photos_need_a_device(app: FastAPI, admin: TestClient) -> None:
    world_id = queued_world(admin)
    r = TestClient(app).post(
        "/api/activities/photo",
        params={"world_id": world_id, "position": 1, "kind": "draw"},
        content=jpeg(),
    )
    assert r.status_code == 401


# ---- admin --------------------------------------------------------------------------------
# The list, bonus stars and "open again" of done tasks with their photos: test_done_admin.py.


def three_photos(admin: TestClient, device_token: str) -> int:
    world_id = queued_world(admin)
    for kind in ("draw", "write", "draw"):
        assert upload(admin, device_token, world_id, kind=kind).status_code == 200
    return world_id


def photo_urls(admin: TestClient) -> list[dict[str, Any]]:
    """Every photo of the done list (§17.3), newest done task first."""
    entries = admin.get("/api/admin/activities/done").json()["entries"]
    return [ph for e in entries for ph in e["photos"]]


def test_photo_files_are_for_the_admin_only(
    app: FastAPI, admin: TestClient, device_token: str, settings: Settings
) -> None:
    three_photos(admin, device_token)
    photo = photo_urls(admin)[0]
    key = photo["url_sm"].removeprefix("/api/admin/photos/").removesuffix(".webp?size=sm")
    r = admin.get(photo["url_lg"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(r.content)).size == (400, 800)
    sm = admin.get(f"/api/admin/photos/{key}.webp")  # size defaults to sm
    assert Image.open(io.BytesIO(sm.content)).size == (160, 320)

    settings.accel_redirect = True
    r = admin.get(photo["url_sm"])
    assert r.status_code == 200
    assert r.headers["x-accel-redirect"] == f"/_media/_photos/{key}-sm.webp"
    assert r.content == b""
    settings.accel_redirect = False

    assert admin.get(f"/api/admin/photos/{key}.webp?size=md").status_code == 422
    for bad in ("p1-0000000g", "..%2Fp1-00000000", "p1-0123456", "x1-01234567", "p1-00000000"):
        r = admin.get(f"/api/admin/photos/{bad}.webp")
        assert r.status_code == 404, bad
    assert TestClient(app).get(photo["url_sm"]).status_code == 401
    # The device can't read photos back.
    assert TestClient(app).get(photo["url_sm"], headers=bearer(device_token)).status_code == 401


def test_the_old_photo_endpoints_are_gone(admin: TestClient, device_token: str) -> None:
    """§17.3: photos go with their task; only the image endpoint stays."""
    three_photos(admin, device_token)
    photo_id = photo_urls(admin)[0]["id"]
    assert admin.get("/api/admin/activities/photos").status_code in (404, 405)
    assert admin.post(f"/api/admin/activities/photos/{photo_id}/bonus").status_code in (404, 405)
    assert admin.delete(f"/api/admin/activities/photos/{photo_id}").status_code in (404, 405)


def test_the_log_links_the_latest_photo(admin: TestClient, device_token: str) -> None:
    world_id = queued_world(admin)
    admin.post(
        "/api/activities/done",
        json={"world_id": world_id, "position": 1, "kind": "calc"},
        headers=bearer(device_token),
    )
    [row] = admin.get("/api/admin/activities").json()["log"]
    assert row["photo_id"] is None  # no picture
    upload(admin, device_token, world_id)
    upload(admin, device_token, world_id, kind="write")
    ids = [p["id"] for p in photo_urls(admin)]
    [row] = admin.get("/api/admin/activities").json()["log"]
    assert sorted(row["kinds"]) == ["calc", "draw", "write"]
    assert row["photo_id"] == max(ids)
    write_done = next(
        e["id"]
        for e in admin.get("/api/admin/activities/done").json()["entries"]
        if e["kind"] == "write"
    )
    assert admin.delete(f"/api/admin/activities/done/{write_done}").status_code == 204
    [row] = admin.get("/api/admin/activities").json()["log"]
    assert sorted(row["kinds"]) == ["calc", "draw"]
    assert row["photo_id"] == min(ids)


def test_photos_outlive_their_world(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = three_photos(admin, device_token)
    files = photo_files(settings)
    assert admin.delete(f"/api/admin/worlds/{world_id}").status_code == 204
    assert admin.get("/api/admin/activities/done").json()["entries"] == []
    rows = photo_rows(settings)
    assert len(rows) == 3
    assert {(r.world_id, r.world_name) for r in rows} == {(world_id, "World")}
    assert all(r.done_id is None for r in rows)  # ON DELETE SET NULL
    assert photo_files(settings) == files
    assert admin.get(f"/api/admin/photos/{rows[0].key}.webp?size=lg").status_code == 200
    with make_engine(settings.db_path).connect() as conn:
        n = conn.execute(select(func.count()).select_from(activity_photos)).scalar_one()
    assert n == 3
