"""Uploads, thumbnails, covers, library and streaming over HTTP (SPEC §5, §6)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kidsmedia.config import Settings
from tests import media_fixtures as fx


@pytest.fixture(scope="module")
def files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("upload")
    cover = fx.cover_png(d / "cover.png")
    return {
        "cover_png": cover,
        "with_art": fx.mp3(d / "ep1.mp3", cover=cover, title="Episode 1: Setting Sail"),
        "no_art": fx.mp3(d / "Episode_2 - The Silver Map.mp3"),
        "video": fx.mp4_black_intro(d / "cartoon.mp4"),
        "moov_end": fx.mp4_black_intro(d / "slow.mp4", faststart=False),
        "bad_codec": fx.mp4_unsupported_codec(d / "old.mp4"),
    }


def upload(admin: TestClient, group_id: int, path: Path, name: str | None = None) -> dict[str, Any]:
    r = admin.put(
        f"/api/admin/groups/{group_id}/upload",
        params={"filename": name or path.name},
        content=path.read_bytes(),
    )
    assert r.status_code == 201, r.text
    result: dict[str, Any] = r.json()
    return result


def group(admin: TestClient, kind: str = "audiobook", name: str = "The Moon Pirates") -> int:
    gid: int = admin.post("/api/admin/groups", json={"name": name, "kind": kind}).json()["id"]
    return gid


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_upload_reads_tags_and_makes_thumbnail(
    admin: TestClient, files: dict[str, Path], settings: Settings
) -> None:
    gid = group(admin)
    item = upload(admin, gid, files["with_art"])
    assert item["title"] == "Episode 1: Setting Sail"
    assert item["mime"] == "audio/mpeg" and item["has_embedded_art"] is True
    assert 4.5 < item["duration_s"] < 5.5
    # TestClient runs background tasks before returning; re-read to see the thumbnail
    item = admin.get(f"/api/admin/media/{item['id']}").json()
    assert item["thumb_source"] == "embedded"
    assert item["thumb"]["url"].startswith(f"/api/thumbs/m{item['id']}-")
    assert len(list(settings.thumbs_dir.glob(f"m{item['id']}-*.webp"))) == 3
    # the client's filename never reaches the filesystem
    assert not list(settings.media_dir.rglob("ep1*"))


def test_title_from_filename_and_group_cover_fallback(
    admin: TestClient, files: dict[str, Path]
) -> None:
    gid = group(admin)
    first = upload(admin, gid, files["with_art"])
    second = upload(admin, gid, files["no_art"])
    second = admin.get(f"/api/admin/media/{second['id']}").json()
    assert second["title"] == "Episode 2: The Silver Map"
    assert second["thumb_source"] == "none"
    # SPEC §6.4 step 3: an item without art shows the group cover, which is the first item's picture
    cover = admin.get(f"/api/admin/groups/{gid}").json()["cover"]
    first = admin.get(f"/api/admin/media/{first['id']}").json()
    assert cover["url"] == first["thumb"]["url"]
    assert second["thumb"]["url"] == cover["url"] and second["thumb"]["source"] == "group_cover"


def test_video_frame_and_faststart(admin: TestClient, files: dict[str, Path]) -> None:
    gid = group(admin, "video", "Cartoons")
    video = upload(admin, gid, files["video"])
    assert video["faststart"] is True and video["width"] == 320
    video = admin.get(f"/api/admin/media/{video['id']}").json()
    assert video["thumb_source"] == "frame" and video["thumb_frame_ms"] >= 2000
    assert video["thumb"]["aspect"] == pytest.approx(4 / 3, rel=0.02)

    slow = upload(admin, gid, files["moov_end"])
    assert slow["faststart"] is False
    assert admin.post(f"/api/admin/media/{slow['id']}/optimize").json()["faststart"] is True


def test_unsupported_upload_is_rejected_and_not_stored(
    admin: TestClient, files: dict[str, Path], settings: Settings
) -> None:
    gid = group(admin, "video", "Cartoons")
    r = admin.put(
        f"/api/admin/groups/{gid}/upload",
        params={"filename": "old.mp4"},
        content=files["bad_codec"].read_bytes(),
    )
    assert r.status_code == 415
    assert (
        r.json()["reason"] == "unsupported_format" and r.json()["params"]["video_codec"] == "mpeg4"
    )
    assert not [p for p in settings.media_dir.rglob("*") if p.is_file()]


def test_upload_size_limit(admin: TestClient, settings: Settings) -> None:
    settings.max_upload_mb = 1
    gid = group(admin)
    r = admin.put(
        f"/api/admin/groups/{gid}/upload",
        params={"filename": "big.mp3"},
        content=b"0" * (2 * 1024 * 1024),
    )
    assert r.status_code == 413 and r.json()["reason"] == "upload_too_large"


def test_thumbnail_overrides(admin: TestClient, files: dict[str, Path]) -> None:
    gid = group(admin, "video", "Cartoons")
    vid = upload(admin, gid, files["video"])["id"]
    frames = admin.get(f"/api/admin/media/{vid}/frames", params={"count": 8}).json()
    assert len(frames) == 8 and frames[0]["t_ms"] < frames[-1]["t_ms"]
    img = admin.get(frames[5]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    picked = admin.post(
        f"/api/admin/media/{vid}/thumb", json={"source": "frame", "t_ms": 15000}
    ).json()
    assert picked["thumb_source"] == "frame" and picked["thumb_frame_ms"] == 15000
    assert admin.post(f"/api/admin/media/{vid}/thumb", json={"source": "embedded"}).json()[
        "reason"
    ] == ("no_embedded_picture")
    uploaded = admin.put(
        f"/api/admin/media/{vid}/thumb", content=files["cover_png"].read_bytes()
    ).json()
    assert uploaded["thumb_source"] == "upload" and uploaded["thumb"]["aspect"] == 1.0
    assert (
        admin.put(f"/api/admin/media/{vid}/thumb", content=b"junk").json()["reason"] == "bad_image"
    )
    reset = admin.delete(f"/api/admin/media/{vid}/thumb").json()
    assert reset["thumb_source"] == "frame"


def test_apply_one_picture_to_items_without_art(admin: TestClient, files: dict[str, Path]) -> None:
    gid = group(admin)
    a = upload(admin, gid, files["no_art"])["id"]
    b = upload(admin, gid, files["no_art"])["id"]
    admin.put(f"/api/admin/media/{a}/thumb", content=files["cover_png"].read_bytes())
    changed = admin.post(f"/api/admin/groups/{gid}/thumbs/apply", json={"media_id": a}).json()
    assert [m["id"] for m in changed] == [b] and changed[0]["thumb_source"] == "upload"


def test_group_cover_choices_and_fallback_on_delete(
    admin: TestClient, files: dict[str, Path]
) -> None:
    gid = group(admin)
    first = upload(admin, gid, files["with_art"])["id"]
    second = upload(admin, gid, files["no_art"])["id"]
    admin.put(f"/api/admin/media/{second}/thumb", content=files["cover_png"].read_bytes())
    second_url = admin.get(f"/api/admin/media/{second}").json()["thumb"]["url"]

    g = admin.post(f"/api/admin/groups/{gid}/cover", json={"media_id": second}).json()
    assert g["cover_source"] == "item" and g["cover"]["url"] == second_url
    # SPEC §6.1: deleting the chosen cover item falls back to auto (first item)
    assert admin.delete(f"/api/admin/media/{second}").status_code == 204
    g = admin.get(f"/api/admin/groups/{gid}").json()
    assert g["cover_source"] == "auto" and g["cover_media_id"] is None
    assert g["cover"]["url"] == admin.get(f"/api/admin/media/{first}").json()["thumb"]["url"]

    g = admin.put(f"/api/admin/groups/{gid}/cover", content=files["cover_png"].read_bytes()).json()
    assert g["cover_source"] == "upload" and g["cover"]["url"].startswith(f"/api/thumbs/g{gid}-")
    assert admin.delete(f"/api/admin/groups/{gid}/cover").json()["cover_source"] == "auto"


def test_move_and_reorder(admin: TestClient, files: dict[str, Path], settings: Settings) -> None:
    g1, g2 = group(admin), group(admin, name="Detective Fox")
    a = upload(admin, g1, files["no_art"])["id"]
    b = upload(admin, g1, files["no_art"])["id"]
    assert [
        m["id"]
        for m in admin.post("/api/admin/media/reorder", json={"group_id": g1, "ids": [b, a]}).json()
    ] == [b, a]
    moved = admin.patch(f"/api/admin/media/{a}", json={"group_id": g2, "title": "Case 1"}).json()
    assert moved["group_id"] == g2 and moved["title"] == "Case 1"
    assert list((settings.media_dir / str(g2)).glob("*.mp3"))


def test_library_stream_and_thumbs_for_device(
    admin: TestClient, files: dict[str, Path], device_token: str, settings: Settings
) -> None:
    gid = group(admin)
    item = upload(admin, gid, files["with_art"])
    lib = admin.get("/api/library", headers=bearer(device_token)).json()
    g = lib["groups"][0]
    assert g["name"] == "The Moon Pirates" and g["status"]["allowed"] is True
    entry = g["items"][0]
    assert (
        entry["id"] == item["id"] and entry["position_ms"] == 0 and entry["played_today"] is False
    )

    # Range streaming (no nginx in tests: ACCEL_REDIRECT=false)
    r = admin.get(
        f"/api/media/{item['id']}/stream", headers={**bearer(device_token), "Range": "bytes=0-99"}
    )
    assert r.status_code == 206 and len(r.content) == 100
    assert r.headers["content-type"] == "audio/mpeg"

    thumb_url = entry["thumb"]["url"]
    r = admin.get(thumb_url, params={"size": "sm"}, headers=bearer(device_token))
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert "immutable" in r.headers["cache-control"]


def test_accel_redirect_headers(
    admin: TestClient, files: dict[str, Path], device_token: str, settings: Settings
) -> None:
    settings.accel_redirect = True
    gid = group(admin)
    item = upload(admin, gid, files["with_art"])
    r = admin.get(f"/api/media/{item['id']}/stream", headers=bearer(device_token))
    assert r.headers["x-accel-redirect"].startswith(f"/_media/{gid}/") and r.content == b""
    url = admin.get(f"/api/admin/media/{item['id']}").json()["thumb"]["url"]
    key = url.rsplit("/", 1)[1].removesuffix(".webp")
    r = admin.get(url, params={"size": "lg"}, headers=bearer(device_token))
    assert r.headers["x-accel-redirect"] == f"/_thumbs/{key}-lg.webp"


def test_media_and_thumbs_need_auth(client: TestClient) -> None:
    assert client.get("/api/media/1/stream").status_code == 401
    assert client.get("/api/thumbs/m1-abcdef.webp").status_code == 401
    assert client.get("/api/library").status_code == 401


def test_thumb_key_is_validated(admin: TestClient) -> None:
    assert admin.get("/api/thumbs/..%2F..%2Fkidsmedia.db.webp").status_code == 404
    assert admin.get("/api/thumbs/m1-zzzzzz.webp").status_code == 404
