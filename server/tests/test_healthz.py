from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kidsmedia import running_version
from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.main import create_app


def test_healthz(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIDSMEDIA_VERSION", raising=False)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "dev"}


def test_the_release_version_is_reported(
    client: TestClient, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image's KIDSMEDIA_VERSION (the release tag) reaches /healthz and the admin."""
    monkeypatch.setenv("KIDSMEDIA_VERSION", " 0.9.0 ")
    assert client.get("/healthz").json()["version"] == "0.9.0"
    assert admin.get("/api/admin/me").json()["version"] == "0.9.0"
    monkeypatch.setenv("KIDSMEDIA_VERSION", "")
    assert admin.get("/api/admin/me").json()["version"] == "dev"


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("v0.9.0", "0.9.0"),  # release.yml passes the tag
        ("0.9.0", "0.9.0"),
        ("main-27b5f9f0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6", "main-27b5f9f"),
        ("dev", "dev"),
    ],
)
def test_running_version(raw: str, shown: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIDSMEDIA_VERSION", raw)
    assert running_version() == shown


def test_admin_spa_is_served_when_built(
    tmp_path: Path, settings: Settings, clock: FixedClock
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>admin</title>")
    client = TestClient(create_app(settings.model_copy(update={"admin_dist": dist}), clock))
    assert "admin" in client.get("/").text
    # API routes keep priority over the SPA mount.
    assert client.get("/healthz").json()["status"] == "ok"
