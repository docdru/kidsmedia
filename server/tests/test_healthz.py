from pathlib import Path

from fastapi.testclient import TestClient

from kidsmedia import __version__
from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.main import create_app


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


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
