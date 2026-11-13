"""End-to-end test of a real deployment (SPEC §4.1, §10.2): the image installed with
deploy/install.sh into /opt/kidsmedia, behind the host nginx with TLS and the shipped
snippet (deploy/nginx/kidsmedia.conf). Run by .github/workflows/e2e.yml.

Rules-engine behaviour is tested with a frozen clock in tests/. Here only rules that
don't depend on the time of day are used, so the real clock is fine.

Environment:
    KM_E2E_URL       https://kidsmedia.test
    KM_E2E_CA        CA certificate that signed the server certificate
    KM_E2E_PASSWORD  admin password from /opt/kidsmedia/.env
    KM_E2E_FIXTURES  directory with song1.mp3, song2.mp3, clip.mp4
"""

import os
import ssl
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

URL = os.environ.get("KM_E2E_URL", "https://kidsmedia.test")
CA = os.environ.get("KM_E2E_CA", "")
PASSWORD = os.environ.get("KM_E2E_PASSWORD", "")
FIXTURES = Path(os.environ.get("KM_E2E_FIXTURES", "/tmp/e2e/fixtures"))  # noqa: S108

pytestmark = pytest.mark.skipif(not PASSWORD, reason="needs a deployment (see e2e.yml)")


class State:
    """What the tests build up, in file order: groups, media, a paired tablet."""

    music: int
    video: int
    song1: int
    song2: int
    clip: int
    device_id: int
    token: str


S = State()


def client() -> httpx.Client:
    verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=CA) if CA else True
    return httpx.Client(base_url=URL, verify=verify, timeout=30, follow_redirects=False)


@pytest.fixture(scope="module")
def admin() -> Iterator[httpx.Client]:
    with client() as c:
        r = c.post("/api/admin/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield c


@pytest.fixture(scope="module")
def anon() -> Iterator[httpx.Client]:
    with client() as c:
        yield c


def tablet() -> httpx.Client:
    c = client()
    c.headers["Authorization"] = f"Bearer {S.token}"
    return c


def upload(admin: httpx.Client, group: int, name: str) -> dict[str, Any]:
    data = (FIXTURES / name).read_bytes()
    r = admin.put(f"/api/admin/groups/{group}/upload", params={"filename": name}, content=data)
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


# ---- nginx, TLS, admin (SPEC §4.1, §7) ------------------------------------------------


def test_https_health_and_admin_spa(anon: httpx.Client) -> None:
    assert anon.get("/healthz").status_code == 200
    home = anon.get("/")
    assert home.status_code == 200 and '<div id="root">' in home.text
    assert "max-age=31536000" in home.headers.get("strict-transport-security", "")


def test_plain_http_redirects_to_https() -> None:
    plain = URL.replace("https://", "http://")
    r = httpx.get(f"{plain}/healthz", follow_redirects=False, timeout=10)
    assert r.status_code == 301
    assert r.headers["location"].startswith("https://")


def test_internal_locations_are_not_public(anon: httpx.Client) -> None:
    # Only reachable through X-Accel-Redirect after the app has checked token and rules.
    assert anon.get("/_media/1/x.mp3").status_code == 404
    assert anon.get("/_thumbs/x-md.webp").status_code == 404


def test_admin_needs_login_and_csrf(anon: httpx.Client) -> None:
    assert anon.get("/api/admin/me").status_code == 401
    bad = anon.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401
    with client() as c:
        ok = c.post("/api/admin/login", json={"username": "admin", "password": PASSWORD})
        assert ok.status_code == 200, ok.text
        me = c.get("/api/admin/me")
        assert me.status_code == 200, (me.text, ok.headers.get_list("set-cookie"), dict(c.cookies))
        # The session cookie alone isn't enough for a mutation.
        r = c.post("/api/admin/groups", json={"name": "x", "kind": "music"})
        assert r.status_code == 403, r.text


# ---- library: uploads through nginx, thumbnails --------------------------------------


def test_create_groups_and_upload(admin: httpx.Client) -> None:
    for kind, name in (("music", "E2E Music"), ("video", "E2E Cartoons")):
        r = admin.post("/api/admin/groups", json={"name": name, "kind": kind})
        assert r.status_code == 201, r.text
        setattr(S, kind, r.json()["id"])
    S.song1 = upload(admin, S.music, "song1.mp3")["id"]
    S.song2 = upload(admin, S.music, "song2.mp3")["id"]
    clip = upload(admin, S.video, "clip.mp4")
    S.clip = clip["id"]
    assert clip["duration_s"] > 0


def test_video_thumbnail_is_made(admin: httpx.Client) -> None:
    deadline = time.monotonic() + 60
    while True:
        m = admin.get(f"/api/admin/media/{S.clip}").json()
        if m["thumb_source"] != "pending" or time.monotonic() > deadline:
            break
        time.sleep(1)
    assert m["thumb_source"] == "frame", m


# ---- pairing and streaming through X-Accel-Redirect (SPEC §5) ------------------------


def test_pair_a_tablet(admin: httpx.Client, anon: httpx.Client) -> None:
    d = admin.post("/api/admin/devices", json={"name": "E2E tablet"})
    assert d.status_code == 201, d.text
    S.device_id = d.json()["id"]
    code = d.json()["pairing"]["code"]
    p = anon.post("/api/pair", json={"code": code, "device_name": "E2E tablet"})
    assert p.status_code == 200, p.text
    S.token = p.json()["token"]
    again = anon.post("/api/pair", json={"code": code, "device_name": "again"})
    assert again.status_code != 200  # pairing codes are single-use


def test_library_lists_the_uploads() -> None:
    with tablet() as t:
        lib = t.get("/api/library")
        assert lib.status_code == 200, lib.text
        items = {i["id"] for g in lib.json()["groups"] for i in g["items"]}
    assert {S.song1, S.song2, S.clip} <= items


def test_range_streaming_is_served_by_nginx(anon: httpx.Client) -> None:
    original = (FIXTURES / "song1.mp3").read_bytes()
    with tablet() as t:
        start = t.post("/api/play/start", json={"media_id": S.song1})
        assert start.status_code == 200 and start.json()["allowed"], start.text
        r = t.get(start.json()["stream_url"], headers={"Range": "bytes=0-99"})
    # The app answers with an empty X-Accel-Redirect response; these bytes come from nginx.
    assert r.status_code == 206, r.text
    assert r.headers["content-range"] == f"bytes 0-99/{len(original)}"
    assert r.content == original[:100]
    assert "x-accel-redirect" not in r.headers
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert anon.get(f"/api/media/{S.song1}/stream").status_code == 401


def test_thumbnail_is_served_by_nginx() -> None:
    with tablet() as t:
        lib = t.get("/api/library").json()
        clip = next(i for g in lib["groups"] for i in g["items"] if i["id"] == S.clip)
        r = t.get(clip["thumb"]["url"], params={"size": "sm"})
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF" and r.content[8:12] == b"WEBP"
    assert "immutable" in r.headers.get("cache-control", "")


def test_heartbeat_counts_time() -> None:
    with tablet() as t:
        start = t.post("/api/play/start", json={"media_id": S.song1}).json()
        time.sleep(2)
        beat = t.post(
            "/api/play/heartbeat",
            json={
                "media_id": S.song1,
                "play_session_id": start["play_session_id"],
                "played_seconds_total": 2.0,
                "position_ms": 2000,
            },
        )
        assert beat.status_code == 200 and beat.json()["allowed"], beat.text


# ---- rules that don't depend on the time of day (SPEC §3.4) --------------------------


def test_item_limit_and_replays(admin: httpx.Client) -> None:
    r = admin.post(
        "/api/admin/rules",
        json={
            "name": "E2E one song",
            "days": list(range(7)),
            "group_ids": [S.music],
            "max_items": 1,
            "count_after_seconds": 0,  # song1 was only played for 2 s above
            "replay_percent": None,  # replays are what this test checks
        },
    )
    assert r.status_code == 201, r.text
    rule = r.json()["id"]
    try:
        with tablet() as t:
            # song1 was already started today (above): replays are free, a new file isn't.
            assert t.post("/api/play/start", json={"media_id": S.song1}).json()["allowed"]
            second = t.post("/api/play/start", json={"media_id": S.song2}).json()
            assert (second["allowed"], second["reason"]) == (False, "item_limit")
    finally:
        assert admin.delete(f"/api/admin/rules/{rule}").status_code == 204


def test_block_stops_start_and_stream(admin: httpx.Client) -> None:
    r = admin.post(
        "/api/admin/rules",
        json={"name": "E2E block", "days": list(range(7)), "all_groups": True, "mode": "block"},
    )
    assert r.status_code == 201, r.text
    rule = r.json()["id"]
    try:
        with tablet() as t:
            start = t.post("/api/play/start", json={"media_id": S.clip}).json()
            assert (start["allowed"], start["reason"]) == (False, "blocked_today")
            s = t.get(f"/api/media/{S.clip}/stream", headers={"Range": "bytes=0-9"})
            assert s.status_code == 403 and s.json()["reason"] == "blocked_today"
    finally:
        assert admin.delete(f"/api/admin/rules/{rule}").status_code == 204


# ---- revoking a tablet ---------------------------------------------------------------


def test_revoked_tablet_is_locked_out(admin: httpx.Client) -> None:
    assert admin.delete(f"/api/admin/devices/{S.device_id}").status_code == 204
    with tablet() as t:
        assert t.get("/api/library").status_code == 401
        assert t.get(f"/api/media/{S.song1}/stream").status_code == 401
