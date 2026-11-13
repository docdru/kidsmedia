"""M6: bonus time, the parent's own reason texts, and app self-update (SPEC §3.5, §4.3)."""

from pathlib import Path

from fastapi.testclient import TestClient

from kidsmedia.config import Settings
from tests.test_play import Tablet, add_media, group, rule

# ---- bonus (§3.5) -------------------------------------------------------------------------


def test_bonus_item_for_today(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 1800, "a")
    b = add_media(settings, audio_file, moon, 1800, "b")
    rid = rule(admin, all_groups=False, group_ids=[moon], max_items=1)
    assert tablet.start(a)["allowed"]
    assert tablet.start(b)["reason"] == "item_limit"

    assert (
        admin.post("/api/admin/usage/bonus", json={"rule_id": rid, "extra_items": 1}).status_code
        == 204
    )
    assert tablet.start(b)["allowed"]

    tablet.clock.advance(days=1)  # one-day adjustment: gone tomorrow
    c = add_media(settings, audio_file, moon, 1800, "c")
    assert tablet.start(a)["allowed"]
    assert tablet.start(c)["reason"] == "item_limit"


def test_bonus_minutes_extend_the_limit(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    music = group(admin, "music", "Music")
    song = add_media(settings, audio_file, music, 3600)
    rid = rule(admin, all_groups=False, group_ids=[music], max_minutes=10)
    assert tablet.start(song)["remaining_seconds"] == 600
    admin.post("/api/admin/usage/bonus", json={"rule_id": rid, "extra_minutes": 15})
    assert tablet.start(song)["remaining_seconds"] == 25 * 60


def test_bonus_validation(admin: TestClient) -> None:
    rid = rule(admin, max_items=1)
    assert admin.post("/api/admin/usage/bonus", json={"rule_id": rid}).status_code == 422
    assert (
        admin.post("/api/admin/usage/bonus", json={"rule_id": 999, "extra_items": 1}).status_code
        == 404
    )


# ---- the parent's own reason texts ---------------------------------------------------------


def test_custom_reason_texts_travel_as_params(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 1800, "a")
    b = add_media(settings, audio_file, moon, 1800, "b")
    rid = rule(
        admin,
        all_groups=False,
        group_ids=[moon],
        max_items=1,
        message_en="  One story a day. More tomorrow!  ",
        message_de="Eine Geschichte am Tag.",
        message_el=" Μία ιστορία κάθε μέρα. ",
    )
    saved = admin.get("/api/admin/rules").json()[0]
    assert (saved["id"], saved["message_en"]) == (rid, "One story a day. More tomorrow!")
    assert saved["message_el"] == "Μία ιστορία κάθε μέρα."
    assert tablet.start(a)["allowed"]
    denied = tablet.start(b)
    assert denied["reason"] == "item_limit"  # still a code ...
    assert denied["params"] == {  # ... plus the parent's words for the app to show
        "limit": 1,
        "message_en": "One story a day. More tomorrow!",
        "message_de": "Eine Geschichte am Tag.",
        "message_el": "Μία ιστορία κάθε μέρα.",
    }


def test_greek_reason_text_alone_and_cleared(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    """Decision 30: message_el round-trips on its own; an empty text is stored as NULL."""
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 1800, "a")
    b = add_media(settings, audio_file, moon, 1800, "b")
    rid = rule(admin, all_groups=False, group_ids=[moon], max_items=1, message_el="Αύριο ξανά.")
    saved = admin.get("/api/admin/rules").json()[0]
    assert (saved["message_en"], saved["message_de"], saved["message_el"]) == (
        None,
        None,
        "Αύριο ξανά.",
    )
    assert tablet.start(a)["allowed"]
    assert tablet.start(b)["params"] == {"limit": 1, "message_el": "Αύριο ξανά."}

    body = {k: v for k, v in saved.items() if k != "id"} | {"message_el": "   "}
    assert admin.put(f"/api/admin/rules/{rid}", json=body).status_code == 200
    assert admin.get("/api/admin/rules").json()[0]["message_el"] is None
    assert tablet.start(b)["params"] == {"limit": 1}


def test_custom_reason_text_length_is_limited(admin: TestClient) -> None:
    body = {"name": "r", "days": [0], "all_groups": True, "max_items": 1, "message_en": "x" * 141}
    assert admin.post("/api/admin/rules", json=body).status_code == 422
    body = body | {"message_en": None, "message_el": "x" * 141}
    assert admin.post("/api/admin/rules", json=body).status_code == 422


# ---- self-update (§4.3) --------------------------------------------------------------------

APK = b"PK\x03\x04" + b"\x00" * 2048


def test_upload_and_fetch_latest_apk(admin: TestClient, tablet: Tablet, settings: Settings) -> None:
    assert tablet.c.get("/api/app/latest", headers=tablet.h).json()["reason"] == "no_release"
    r = admin.put("/api/admin/app/apk?version_name=1.1.0&version_code=10100", content=APK)
    assert r.status_code == 201, r.text
    assert r.json()["size"] == len(APK)

    latest = tablet.c.get("/api/app/latest", headers=tablet.h).json()
    assert (latest["version_name"], latest["version_code"], latest["url"]) == (
        "1.1.0",
        10100,
        "/api/app/apk",
    )
    apk = tablet.c.get("/api/app/apk", headers=tablet.h)
    assert apk.status_code == 200 and apk.content == APK
    assert apk.headers["content-type"] == "application/vnd.android.package-archive"
    # The upload's name never reaches the filesystem.
    assert [p.suffix for p in settings.apk_dir.iterdir()] == [".apk"]


def test_apk_upload_checks(admin: TestClient, tablet: Tablet, settings: Settings) -> None:
    assert (
        admin.put("/api/admin/app/apk?version_name=1&version_code=1", content=b"<html>").json()[
            "reason"
        ]
        == "not_an_apk"
    )
    assert (
        admin.put("/api/admin/app/apk?version_name=1&version_code=5", content=APK).status_code
        == 201
    )
    older = admin.put("/api/admin/app/apk?version_name=0.9&version_code=4", content=APK)
    assert (older.status_code, older.json()["reason"]) == (409, "version_not_newer")
    assert (
        admin.put("/api/admin/app/apk?version_name=../x&version_code=6", content=APK).status_code
        == 422
    )
    # Only the newest three are kept.
    for code in (6, 7, 8):
        admin.put(f"/api/admin/app/apk?version_name=1.{code}&version_code={code}", content=APK)
    assert len(list(settings.apk_dir.glob("*.apk"))) == 3
    assert admin.get("/api/admin/app").json()["version_code"] == 8


def test_apk_needs_a_device_or_admin(client: TestClient) -> None:
    assert client.get("/api/app/latest").status_code == 401
    assert client.get("/api/app/apk").status_code == 401
    assert client.put(
        "/api/admin/app/apk?version_name=1&version_code=1", content=APK
    ).status_code in (401, 403)
