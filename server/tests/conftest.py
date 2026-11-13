from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.main import create_app

BERLIN = ZoneInfo("Europe/Berlin")
ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 7, 14, 20, tzinfo=BERLIN))  # a Saturday


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        tz="Europe/Berlin",
        accel_redirect=False,
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        admin_dist=None,
        speech_worker=False,  # tests render speech with SpeechWorker.drain() and a fake
    )


@pytest.fixture
def app(settings: Settings, clock: FixedClock) -> FastAPI:
    return create_app(settings, clock)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """The same client, logged in, sending the CSRF header on every request."""
    r = client.post("/api/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return client


@pytest.fixture
def device_token(admin: TestClient) -> str:
    code = admin.post("/api/admin/devices", json={"name": "Kids tablet"}).json()["pairing"]["code"]
    r = admin.post("/api/pair", json={"code": code, "app_version": "1.0.0"})
    assert r.status_code == 200, r.text
    token: str = r.json()["token"]
    return token


# Shared with test files beyond test_play (fixtures must be visible to pytest here).
from tests.test_play import audio_file, tablet  # noqa: E402, F401
