"""M7 offline mode: flags, downloads, the rules snapshot and syncing offline plays."""

from pathlib import Path

from fastapi.testclient import TestClient

from kidsmedia.config import Settings
from tests.test_play import Tablet, add_media, group, rule


def test_offline_flags_reach_the_library(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon, music = group(admin, "audiobook", "Moon"), group(admin, "music", "Music")
    a = add_media(settings, audio_file, moon, 600, "a")
    song = add_media(settings, audio_file, music, 200, "song")
    assert admin.patch(f"/api/admin/groups/{moon}", json={"offline": True}).json()["offline"]
    assert admin.patch(f"/api/admin/media/{song}", json={"offline": True}).json()["offline"]

    lib = {g["id"]: g for g in tablet.c.get("/api/library", headers=tablet.h).json()["groups"]}
    assert lib[moon]["offline"] and lib[moon]["items"][0]["offline"]
    assert not lib[music]["offline"] and lib[music]["items"][0]["offline"]
    assert lib[moon]["items"][0]["size"] > 0
    assert tablet.c.get(f"/api/media/{a}/download", headers=tablet.h).status_code == 200


def test_download_needs_the_flag_but_not_the_rules(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 600, "a")
    assert (
        tablet.c.get(f"/api/media/{a}/download", headers=tablet.h).json()["reason"] == "not_offline"
    )
    admin.patch(f"/api/admin/groups/{moon}", json={"offline": True})
    rule(admin, mode="block")  # blocked today: playing is denied, downloading is not
    assert tablet.start(a)["reason"] == "blocked_today"
    r = tablet.c.get(f"/api/media/{a}/download", headers=tablet.h)
    assert r.status_code == 200 and r.content == audio_file.read_bytes()
    assert tablet.c.get(f"/api/media/{a}/download").status_code == 401


def test_snapshot_carries_rules_and_todays_usage(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 1800, "a")
    rid = rule(
        admin,
        all_groups=False,
        group_ids=[moon],
        max_items=1,
        lockouts=[{"start": "19:30", "end": "07:00"}],
        grace_minutes=10,
        count_after_seconds=15,
        replay_percent=150,
        message_de="Morgen wieder.",
        message_el="Αύριο ξανά.",
    )
    rule(admin, name="off", enabled=False, max_items=0)
    tablet.start(a)
    tablet.play(a, 120)
    snap = tablet.c.get("/api/offline/snapshot", headers=tablet.h).json()
    assert snap["time_zone"] == "Europe/Berlin" and snap["group_ids"] == [moon]
    assert [r["id"] for r in snap["rules"]] == [rid]  # disabled rules are left out
    r = snap["rules"][0]
    assert (
        r["max_items"],
        r["lockouts"],
        r["grace_minutes"],
        r["count_after_seconds"],
        r["replay_percent"],
    ) == (1, [{"start": "19:30", "end": "07:00"}], 10, 15, 150)
    assert (r["message_en"], r["message_de"], r["message_el"]) == (
        None,
        "Morgen wieder.",
        "Αύριο ξανά.",
    )
    assert snap["usage"]["day"] == "2026-11-07"
    assert snap["usage"]["started"] == {str(a): moon}
    assert snap["usage"]["seconds"][str(moon)] == 120
    assert snap["usage"]["played"] == {str(a): 120}


def test_batch_books_offline_plays_once(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 1800, "a")
    b = add_media(settings, audio_file, moon, 1800, "b")
    rule(admin, all_groups=False, group_ids=[moon], max_items=1)
    events = [
        # Played in the car this morning (Berlin time), while the tablet was offline.
        {
            "event_id": "e-0001-aaaa",
            "media_id": a,
            "started_at": "2026-11-07T09:00:00+01:00",
            "seconds": 900,
            "position_ms": 900_000,
        },
        {
            "event_id": "e-0002-bbbb",
            "media_id": 999,
            "started_at": "2026-11-07T09:20:00+01:00",
            "seconds": 60,
        },
    ]
    r = tablet.c.post("/api/play/batch", headers=tablet.h, json={"events": events}).json()
    assert r == {"accepted": 1, "duplicates": 0, "unknown_media": 1}
    again = tablet.c.post("/api/play/batch", headers=tablet.h, json={"events": events}).json()
    assert again == {"accepted": 0, "duplicates": 2, "unknown_media": 0}

    usage = admin.get("/api/admin/usage").json()
    assert usage["groups"][0]["items"] == 1 and usage["groups"][0]["seconds"] == 900
    # The offline play used today's story: a new one is denied online too.
    assert tablet.start(b)["reason"] == "item_limit"
    # ... and the episode resumes where the car ride ended.
    assert tablet.start(a)["position_ms"] == 900_000


def test_batch_caps_and_clocks(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    music = group(admin, "music", "Music")
    song = add_media(settings, audio_file, music, 200, "song")
    events = [
        {
            "event_id": "e-0003-cccc",
            "media_id": song,
            "started_at": "2026-11-06T23:50:00+01:00",
            "seconds": 999_999,
        },
        {
            "event_id": "e-0004-dddd",
            "media_id": song,
            "started_at": "2030-01-01T10:00:00+01:00",
            "seconds": 30,
        },
    ]
    tablet.c.post("/api/play/batch", headers=tablet.h, json={"events": events})
    history = {d["day"]: d for d in admin.get("/api/admin/usage/history?days=2").json()}
    assert history["2026-11-06"]["seconds"] == 6 * 3600  # capped, and booked on its own day
    assert history["2026-11-07"]["seconds"] == 30  # a future clock is pulled back to now
    naive = [
        {
            "event_id": "e-0005-eeee",
            "media_id": song,
            "started_at": "2026-11-07T10:00:00",
            "seconds": 1,
        }
    ]
    assert (
        tablet.c.post("/api/play/batch", headers=tablet.h, json={"events": naive}).status_code
        == 422
    )
