"""The APK baked into a release image becomes the tablets' update (SPEC §4.3)."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.main import create_app
from tests.conftest import ADMIN_PASSWORD
from tests.test_play import Tablet

APK = b"PK\x03\x04" + b"\x01" * 4096


def write_bundle(d: Path, version_name: str, version_code: int, body: bytes = APK) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.apk"):
        old.unlink()
    file = f"kidsmedia-{version_name}.apk"
    (d / file).write_bytes(body)
    (d / "release.json").write_text(
        json.dumps({"version_name": version_name, "version_code": version_code, "file": file})
    )


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    d = tmp_path / "apk-bundle"
    write_bundle(d, "0.2.0", 200)
    return d


@pytest.fixture
def settings(tmp_path: Path, bundle_dir: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        tz="Europe/Berlin",
        accel_redirect=False,
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        admin_dist=None,
        bundled_apk_dir=bundle_dir,
    )


def test_bundled_apk_is_offered_to_tablets(tablet: Tablet, settings: Settings) -> None:
    latest = tablet.c.get("/api/app/latest", headers=tablet.h).json()
    assert (latest["version_name"], latest["version_code"]) == ("0.2.0", 200)
    assert latest["sha256"] == hashlib.sha256(APK).hexdigest()
    assert latest["size"] == len(APK)
    apk = tablet.c.get("/api/app/apk", headers=tablet.h)
    assert apk.status_code == 200 and apk.content == APK
    # Copied under a random name into data/apk, like an upload.
    files = list(settings.apk_dir.glob("*.apk"))
    assert len(files) == 1 and files[0].name != "kidsmedia-0.2.0.apk"


def test_restart_does_not_import_twice(
    admin: TestClient, settings: Settings, clock: FixedClock
) -> None:
    create_app(settings, clock)
    create_app(settings, clock)
    assert len(list(settings.apk_dir.glob("*.apk"))) == 1
    assert admin.get("/api/admin/app").json()["version_code"] == 200


def test_newer_image_replaces_and_upload_still_wins(
    admin: TestClient, tablet: Tablet, settings: Settings, clock: FixedClock, bundle_dir: Path
) -> None:
    # The next release image carries 0.3.0.
    write_bundle(bundle_dir, "0.3.0", 300)
    create_app(settings, clock)
    assert tablet.c.get("/api/app/latest", headers=tablet.h).json()["version_code"] == 300

    # A parent's upload of the same version is rejected, a newer one wins.
    same = admin.put("/api/admin/app/apk?version_name=0.3.0&version_code=300", content=APK)
    assert same.status_code == 409
    newer = admin.put("/api/admin/app/apk?version_name=0.3.1&version_code=301", content=APK)
    assert newer.status_code == 201
    assert tablet.c.get("/api/app/latest", headers=tablet.h).json()["version_code"] == 301

    # Going back to the older image doesn't take the newer app away.
    write_bundle(bundle_dir, "0.2.0", 200)
    create_app(settings, clock)
    assert tablet.c.get("/api/app/latest", headers=tablet.h).json()["version_code"] == 301


@pytest.mark.parametrize(
    "setup",
    [
        lambda d: (d / "release.json").unlink(),  # non-release image
        lambda d: (d / "release.json").write_text("{not json"),
        lambda d: (d / "release.json").write_text(
            json.dumps({"version_name": "1", "version_code": 1, "file": "../db.apk"})
        ),
        lambda d: [p.unlink() for p in d.glob("*.apk")],  # manifest without the APK
        lambda d: write_bundle(d, "0.2.0", 200, body=b"<html>not an apk</html>"),
    ],
    ids=["no-manifest", "bad-json", "path-in-file", "missing-apk", "not-a-zip"],
)
def test_bad_bundles_are_ignored(
    tmp_path: Path, bundle_dir: Path, clock: FixedClock, setup: object
) -> None:
    assert callable(setup)
    setup(bundle_dir)
    settings = Settings(
        data_dir=tmp_path / "other-data",
        admin_password=ADMIN_PASSWORD,
        accel_redirect=False,
        bundled_apk_dir=bundle_dir,
    )
    with TestClient(create_app(settings, clock)) as c:
        assert c.get("/healthz").status_code == 200
    assert list(settings.apk_dir.iterdir()) == []
