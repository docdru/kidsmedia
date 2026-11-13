from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.db import admin_users, make_engine
from kidsmedia.main import create_app
from tests.conftest import ADMIN_PASSWORD


def test_login_sets_httponly_strict_cookie(client: TestClient) -> None:
    r = client.post("/api/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "km_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert r.json()["csrf_token"]
    assert client.get("/api/admin/me").json()["username"] == "admin"


def test_wrong_password(client: TestClient) -> None:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401
    assert r.json() == {"reason": "bad_credentials", "params": {}}


def test_login_is_rate_limited(client: TestClient) -> None:
    for _ in range(5):
        client.post("/api/admin/login", json={"username": "admin", "password": "nope"})
    r = client.post("/api/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 429


def test_admin_endpoints_need_a_session(client: TestClient) -> None:
    assert client.get("/api/admin/devices").status_code == 401
    assert client.get("/api/admin/me").json()["reason"] == "not_authenticated"


def test_mutations_need_csrf(admin: TestClient) -> None:
    csrf = admin.headers.pop("X-CSRF-Token")
    assert admin.post("/api/admin/devices", json={"name": "Tablet"}).status_code == 403
    admin.headers["X-CSRF-Token"] = "wrong"
    assert (
        admin.post("/api/admin/devices", json={"name": "Tablet"}).json()["reason"] == "csrf_failed"
    )
    admin.headers["X-CSRF-Token"] = csrf
    assert admin.post("/api/admin/devices", json={"name": "Tablet"}).status_code == 201


def test_session_expires(admin: TestClient, clock: FixedClock) -> None:
    clock.advance(days=31)
    assert admin.get("/api/admin/me").status_code == 401


def test_logout(admin: TestClient) -> None:
    assert admin.post("/api/admin/logout").status_code == 204
    assert admin.get("/api/admin/me").status_code == 401


def test_locale_is_remembered(admin: TestClient) -> None:
    assert admin.get("/api/admin/me").json()["locale"] is None
    assert admin.patch("/api/admin/me", json={"locale": "de"}).json()["locale"] == "de"
    assert admin.get("/api/admin/me").json()["locale"] == "de"
    assert admin.patch("/api/admin/me", json={"locale": "fr"}).status_code == 422
    assert admin.patch("/api/admin/me", json={"locale": "el"}).json()["locale"] == "el"
    assert admin.get("/api/admin/me").json()["locale"] == "el"


def test_default_locale_allows_greek(tmp_path: Path) -> None:
    assert Settings(data_dir=tmp_path, default_locale="el").default_locale == "el"


def test_placeholder_password_creates_no_admin(tmp_path: Path, clock: FixedClock) -> None:
    settings = Settings(data_dir=tmp_path, admin_password="change-me", accel_redirect=False)
    create_app(settings, clock)
    with make_engine(settings.db_path).connect() as conn:
        assert conn.execute(select(func.count()).select_from(admin_users)).scalar_one() == 0


def test_password_is_stored_hashed(admin: TestClient, settings: Settings) -> None:
    with make_engine(settings.db_path).connect() as conn:
        stored = conn.execute(select(admin_users.c.password_hash)).scalar_one()
    assert stored.startswith("$argon2id$") and ADMIN_PASSWORD not in stored
