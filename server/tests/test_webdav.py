"""Story packs on a WebDAV folder (SPEC §13.5a): listing, dry run, import, errors."""

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kidsmedia import webdav
from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.main import create_app
from kidsmedia.routes.activities_admin import get_pack_folder
from tests.conftest import ADMIN_PASSWORD
from tests.test_activities_api import make_zip, pack

URL = "https://dav.example.com/seafdav/Kidsmedia%20Packs/"
FOLDER = "/seafdav/Kidsmedia Packs/"


class FakeDav:
    """A WebDAV folder with Basic auth (user `dad`, password `secret`)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != httpx.BasicAuth("dad", "secret")._auth_header:
            return httpx.Response(401)
        path = unquote(request.url.path)
        if request.method == "PROPFIND":
            assert path == FOLDER and request.headers["Depth"] == "1"
            return httpx.Response(207, content=self.listing())
        if request.method == "GET" and path.startswith(FOLDER):
            data = self.files.get(path[len(FOLDER) :])
            return httpx.Response(200, content=data) if data is not None else httpx.Response(404)
        return httpx.Response(405)

    def listing(self) -> bytes:
        def entry(href: str, size: int | None, collection: bool) -> str:
            kind = "<d:collection/>" if collection else ""
            length = f"<d:getcontentlength>{size}</d:getcontentlength>" if size is not None else ""
            return (
                f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
                f"<d:resourcetype>{kind}</d:resourcetype>{length}"
                "<d:getlastmodified>Sun, 22 Nov 2026 09:00:00 GMT</d:getlastmodified>"
                "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            )

        rows = [entry("/seafdav/Kidsmedia%20Packs/", None, True)]
        rows.append(entry("/seafdav/Kidsmedia%20Packs/old/", None, True))
        rows.append(entry("/seafdav/Kidsmedia%20Packs/notes.txt", 3, False))
        rows += [
            entry(f"/seafdav/Kidsmedia%20Packs/{name.replace(' ', '%20')}", len(data), False)
            for name, data in self.files.items()
        ]
        return ('<d:multistatus xmlns:d="DAV:">' + "".join(rows) + "</d:multistatus>").encode()


@pytest.fixture
def dav() -> FakeDav:
    return FakeDav()


@pytest.fixture
def dav_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"packs_webdav_url": URL})


@pytest.fixture
def dav_admin(dav_settings: Settings, clock: FixedClock, dav: FakeDav) -> Iterator[TestClient]:
    app: FastAPI = create_app(dav_settings, clock)
    folder = webdav.PackFolder(URL, "dad", "secret", transport=httpx.MockTransport(dav.handler))
    app.dependency_overrides[get_pack_folder] = lambda: folder
    with TestClient(app) as c:
        r = c.post("/api/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield c


def pack_zip(slug: str = "ghost-clock") -> bytes:
    return make_zip({f"{slug}/world.json": json.dumps(pack(slug)).encode()})


def test_lists_only_the_zips_in_the_folder(dav_admin: TestClient, dav: FakeDav) -> None:
    dav.files = {"b pack.zip": pack_zip(), "A.ZIP": pack_zip("other")}
    assert dav_admin.get("/api/admin/activities").json()["webdav"] is True
    r = dav_admin.get("/api/admin/worlds/webdav")
    assert r.status_code == 200, r.text
    packs = r.json()["packs"]
    assert [p["name"] for p in packs] == ["A.ZIP", "b pack.zip"]
    assert packs[1]["size"] == len(dav.files["b pack.zip"])
    assert packs[1]["modified"].startswith("2026-11-22T09:00:00")


def test_dry_run_then_import(dav_admin: TestClient, dav: FakeDav) -> None:
    dav.files = {"ghost clock.zip": pack_zip()}
    url = "/api/admin/worlds/webdav/import"
    r = dav_admin.post(url, params={"name": "ghost clock.zip", "dry_run": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "ghost-clock" and r.json()["world_id"] is None
    assert dav_admin.get("/api/admin/activities").json()["worlds"] == []

    r = dav_admin.post(url, params={"name": "ghost clock.zip"})
    assert r.status_code == 200, r.text
    assert r.json()["world_id"] is not None and r.json()["complete"] is True
    assert [w["slug"] for w in dav_admin.get("/api/admin/activities").json()["worlds"]] == [
        "ghost-clock"
    ]
    # Asked for exactly that file, with the login.
    get = [q for q in dav.requests if q.method == "GET"][-1]
    assert get.url.raw_path == b"/seafdav/Kidsmedia%20Packs/ghost%20clock.zip"


def test_invalid_pack_is_reported_like_an_upload(dav_admin: TestClient, dav: FakeDav) -> None:
    dav.files = {"broken.zip": make_zip({"readme.txt": b"hi"})}
    r = dav_admin.post("/api/admin/worlds/webdav/import", params={"name": "broken.zip"})
    assert r.status_code == 422
    assert r.json()["reason"] == "invalid_pack"


@pytest.mark.parametrize("name", ["../x.zip", "a/b.zip", "a\\b.zip", ".hidden.zip", "pack.txt"])
def test_names_outside_the_folder_are_refused(
    dav_admin: TestClient, dav: FakeDav, name: str
) -> None:
    r = dav_admin.post("/api/admin/worlds/webdav/import", params={"name": name})
    assert r.status_code == 422, r.text
    assert r.json()["reason"] == "invalid_name"
    assert dav.requests == []


def test_missing_pack(dav_admin: TestClient) -> None:
    r = dav_admin.post("/api/admin/worlds/webdav/import", params={"name": "gone.zip"})
    assert r.status_code == 404
    assert r.json() == {"reason": "webdav_pack_not_found", "params": {"name": "gone.zip"}}


def test_too_large(dav_admin: TestClient, dav: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:
    from kidsmedia.routes import activities_admin

    monkeypatch.setattr(activities_admin, "MAX_PACK_BYTES", 1024 * 1024)
    dav.files = {"big.zip": b"PK" + b"\0" * (1024 * 1024)}
    r = dav_admin.post("/api/admin/worlds/webdav/import", params={"name": "big.zip"})
    assert r.status_code == 413
    assert r.json()["reason"] == "pack_too_large"


def test_wrong_login(dav_settings: Settings, clock: FixedClock, dav: FakeDav) -> None:
    app = create_app(dav_settings, clock)
    folder = webdav.PackFolder(URL, "dad", "wrong", transport=httpx.MockTransport(dav.handler))
    app.dependency_overrides[get_pack_folder] = lambda: folder
    with TestClient(app) as c:
        c.post("/api/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        r = c.get("/api/admin/worlds/webdav")
    assert r.status_code == 502
    assert r.json() == {"reason": "webdav_auth_failed", "params": {"status": 401}}


def test_unreachable(dav_settings: Settings, clock: FixedClock) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    app = create_app(dav_settings, clock)
    folder = webdav.PackFolder(URL, "dad", "secret", transport=httpx.MockTransport(fail))
    app.dependency_overrides[get_pack_folder] = lambda: folder
    with TestClient(app) as c:
        c.post("/api/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        r = c.get("/api/admin/worlds/webdav")
    assert r.status_code == 502
    assert r.json()["reason"] == "webdav_unreachable"


def test_not_configured(admin: TestClient) -> None:
    assert admin.get("/api/admin/activities").json()["webdav"] is False
    r = admin.get("/api/admin/worlds/webdav")
    assert r.status_code == 409
    assert r.json()["reason"] == "webdav_not_configured"
    r = admin.post("/api/admin/worlds/webdav/import", params={"name": "a.zip"})
    assert r.json()["reason"] == "webdav_not_configured"


def test_needs_admin_and_csrf(dav_admin: TestClient) -> None:
    token = dav_admin.headers.pop("X-CSRF-Token")
    r = dav_admin.post("/api/admin/worlds/webdav/import", params={"name": "a.zip"})
    assert r.status_code == 403 and r.json()["reason"] == "csrf_failed"
    dav_admin.headers["X-CSRF-Token"] = token
    dav_admin.post("/api/admin/logout")
    assert dav_admin.get("/api/admin/worlds/webdav").status_code == 401


def test_listing_with_a_doctype_is_refused() -> None:
    raw = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><d:multistatus xmlns:d="DAV:"/>'
    with pytest.raises(webdav.WebDavError) as err:
        webdav.parse_listing(raw, "/")
    assert err.value.reason == "webdav_bad_listing"


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://dav.example.com/packs", True),
        ("http://192.168.1.5/dav/", True),
        ("http://localhost:8080/dav/", True),
        ("http://dav.example.com/packs/", False),
        ("https://dad:pw@dav.example.com/packs/", False),
        ("ftp://dav.example.com/", False),
    ],
)
def test_url_check(url: str, ok: bool, tmp_path: Path) -> None:
    if ok:
        assert Settings(data_dir=tmp_path, packs_webdav_url=url).packs_webdav_url.endswith("/")
    else:
        with pytest.raises(ValueError):
            Settings(data_dir=tmp_path, packs_webdav_url=url)
