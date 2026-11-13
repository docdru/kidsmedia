"""Activities over HTTP: device and admin (SPEC §13.3, §13.4, §14, §15.2).

The fixed clock starts on Saturday 2026-11-07 14:20 Europe/Berlin; default activity days
are Mon-Sat, so a world queued now has chapters on Sat 7, Mon 9 ... Fri 13 Nov.
"""

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from kidsmedia import packcheck
from kidsmedia.activities import Pack
from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.db import activity_done, make_engine, reward_ledger, worlds
from kidsmedia.library import THUMB_KEY_RE
from kidsmedia.routes import activities_admin
from kidsmedia.routes.activities_admin import EXAMPLE_ORDER, PACKS_DIR
from tests.conftest import BERLIN
from tests.test_play import bearer


def task(kind: str, title: str = "T") -> dict[str, Any]:
    return {
        "kind": kind,
        "minutes": 10,
        "texts": {
            "de": {"title": title, "text": "Mach es", "easier": "leichter", "harder": "schwerer"},
            "en": {"title": title, "text": "Do it", "easier": "easier", "harder": "harder"},
            "el": {"title": title, "text": "Κάνε", "easier": "ευκολότερα", "harder": "δυσκολότερα"},
        },
    }


# The problems of `calc_task` per level (§15.2): (text, answer); the same in every language.
CALC_PROBLEMS: dict[str, list[tuple[str, int]]] = {
    "normal": [("sum?", 5), ("left?", 3)],
    "easier": [("easy sum?", 2)],
    "harder": [("hard sum?", 12)],
}


def problems_of(level: str) -> list[dict[str, Any]]:
    return [{"text": t, "answer": a, "hint": "count"} for t, a in CALC_PROBLEMS[level]]


def calc_task() -> dict[str, Any]:
    """A complete calc task: three level texts and problems per level (§15.2)."""
    body = task("calc")
    for block in body["texts"].values():
        block["problems"] = {level: problems_of(level) for level in CALC_PROBLEMS}
    return body


def questions(word: str) -> list[dict[str, Any]]:
    return [{"question": f"{word}?", "answers": ["a", "b", "c"], "correct": 1}]


def read_task(title: str = "R") -> dict[str, Any]:
    """A complete read task: three level texts and questions per level (§14.2)."""
    return {
        "kind": "read",
        "minutes": 5,
        "texts": {
            lang: {
                "title": title,
                "text": f"{lang} normal",
                "easier": f"{lang} easy",
                "harder": f"{lang} hard",
                "questions": {
                    "normal": questions("normal"),
                    "easier": questions("easier"),
                    "harder": questions("harder"),
                },
                "say": {"text": f"{lang} spoken"},
            }
            for lang in ("de", "en", "el")
        },
    }


def pack(slug: str = "test-world", chapters: int = 6, title: str = "One") -> dict[str, Any]:
    return {
        "format": 1,
        "slug": slug,
        "age_band": "6-7",
        "color": "#B84A12",
        "texts": {
            "de": {"name": "Welt", "guide": "mit Mira"},
            "en": {"name": "World"},
            "el": {"name": "Κόσμος"},
        },
        "chapters": [
            {
                "texts": {
                    "de": {"title": title, "intro": "i"},
                    "en": {"title": title},
                    "el": {"title": title},
                },
                "tasks": [task("draw", title), calc_task(), task("write"), read_task()],
            }
            for _ in range(chapters)
        ],
    }


def import_json(admin: TestClient, body: dict[str, Any], dry_run: bool = False) -> Any:
    return admin.put(
        f"/api/admin/worlds/import?dry_run={'true' if dry_run else 'false'}",
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )


def queued_world(admin: TestClient, slug: str = "test-world") -> int:
    r = import_json(admin, pack(slug))
    assert r.status_code == 200, r.text
    world_id: int = r.json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [world_id]})
    return world_id


def png(color: tuple[int, int, int] = (200, 80, 20), size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def key_of(ref: dict[str, Any]) -> str:
    key: str = ref["url"].removeprefix("/api/thumbs/").removesuffix(".webp")
    return key


def thumb_files(settings: Settings) -> set[str]:
    d = settings.thumbs_dir
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def done_count(settings: Settings) -> int:
    with make_engine(settings.db_path).connect() as conn:
        n: int = conn.execute(select(func.count()).select_from(activity_done)).scalar_one()
        return n


# ---- device ---------------------------------------------------------------------------------


def test_nothing_without_a_queue(admin: TestClient, device_token: str) -> None:
    r = admin.get("/api/activities", headers=bearer(device_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["world"] is None
    assert body["unlock_time"] == "07:00"
    assert body["today_is_activity_day"] is True  # Saturday is in Mon-Sat


def test_requires_a_device(client: TestClient) -> None:
    assert client.get("/api/activities").status_code == 401


def test_the_queued_world_with_its_chapters(admin: TestClient, device_token: str) -> None:
    world_id = queued_world(admin)
    body = admin.get("/api/activities", headers=bearer(device_token)).json()
    world = body["world"]
    assert world["id"] == world_id
    assert world["texts"]["de"]["guide"] == "mit Mira"
    assert world["cover"] is None
    chapters = world["chapters"]
    assert [c["date"] for c in chapters] == [
        "2026-11-07",
        "2026-11-09",
        "2026-11-10",
        "2026-11-11",
        "2026-11-12",
        "2026-11-13",
    ]
    assert [c["unlocked"] for c in chapters] == [True, False, False, False, False, False]
    assert body["next_unlock_at"].startswith("2026-11-09T07:00:00")
    task_texts = chapters[0]["tasks"][0]["texts"]["en"]
    # The normal level only: no easier/harder, no spoken forms (§14.1, §14.3); no audio
    # without a voice.
    assert task_texts == {
        "title": "One",
        "text": "Do it",
        "materials": [],
        "questions": [],
        "problems": [],
        "audio": {},
    }


def test_done_is_idempotent_and_locked_chapters_refuse(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    auth = bearer(device_token)
    done = {"world_id": world_id, "position": 1, "kind": "draw"}
    r = admin.post("/api/activities/done", json=done, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["world"]["chapters"][0]["done_kinds"] == ["draw"]
    admin.post("/api/activities/done", json=done, headers=auth)
    assert done_count(settings) == 1
    r = admin.post("/api/activities/done", json=done | {"kind": "write"}, headers=auth)
    assert r.json()["world"]["chapters"][0]["done_kinds"] == ["draw", "write"]

    r = admin.post("/api/activities/done", json=done | {"position": 2}, headers=auth)
    assert r.status_code == 409
    assert r.json()["reason"] == "activity_locked"
    for bad in (done | {"position": 7}, done | {"world_id": 999}):
        r = admin.post("/api/activities/done", json=bad, headers=auth)
        assert r.status_code == 404, bad
        assert r.json()["reason"] == "unknown_task"


def test_a_chapter_missing_a_kind_refuses_it(admin: TestClient, device_token: str) -> None:
    body = pack()
    body["chapters"][0]["tasks"] = [task("draw")]
    world_id = import_json(admin, body).json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [world_id]})
    r = admin.post(
        "/api/activities/done",
        json={"world_id": world_id, "position": 1, "kind": "calc"},
        headers=bearer(device_token),
    )
    assert r.status_code == 404


def test_chapters_unlock_at_seven(admin: TestClient, device_token: str, clock: FixedClock) -> None:
    queued_world(admin)
    auth = bearer(device_token)
    clock.set(datetime(2026, 11, 9, 6, 59, tzinfo=BERLIN))
    assert not admin.get("/api/activities", headers=auth).json()["world"]["chapters"][1]["unlocked"]
    clock.set(datetime(2026, 11, 9, 7, 0, tzinfo=BERLIN))
    assert admin.get("/api/activities", headers=auth).json()["world"]["chapters"][1]["unlocked"]


def test_the_next_world_follows_and_the_old_one_is_dropped(
    admin: TestClient, device_token: str, clock: FixedClock
) -> None:
    first = import_json(admin, pack("first")).json()["world_id"]
    second = import_json(admin, pack("second")).json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [first, second]})
    clock.set(datetime(2026, 11, 14, 9, 0, tzinfo=BERLIN))  # Sat: chapter 1 of the second
    body = admin.get("/api/activities", headers=bearer(device_token)).json()
    assert body["world"]["id"] == second
    queue = admin.get("/api/admin/activities").json()["queue"]
    assert [q["world_id"] for q in queue] == [second]
    assert queue[0]["first_date"] == "2026-11-14"
    assert queue[0]["status"] == "running"


# ---- admin: settings, days off, queue -------------------------------------------------------


def test_admin_state_defaults(admin: TestClient) -> None:
    body = admin.get("/api/admin/activities").json()
    assert body["settings"] == {
        "days": [0, 1, 2, 3, 4, 5],
        "unlock_time": "07:00",
        "level": "normal",
        "windows": [],
    }
    assert body["queue"] == []
    assert body["days_off"] == []


def test_settings_move_the_dates(admin: TestClient) -> None:
    queued_world(admin)
    settings = {"days": [5, 0, 3], "unlock_time": "08:30"}
    r = admin.put("/api/admin/activities/settings", json=settings)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"] == {
        "days": [0, 3, 5],
        "unlock_time": "08:30",
        "level": "normal",
        "windows": [],
    }
    assert body["queue"][0]["first_date"] == "2026-11-07"
    assert body["queue"][0]["last_date"] == "2026-11-19"  # Sat 7, Mon 9, Thu 12 ... Thu 19


@pytest.mark.parametrize(
    "body",
    [
        {"days": [7], "unlock_time": "07:00"},
        {"days": [0], "unlock_time": "7:00"},
        {"days": [0], "unlock_time": "24:00"},
        {"days": [0], "unlock_time": "07:00", "level": "hard"},
        {"days": [0], "unlock_time": "07:00", "windows": [{"days": [1], "from": "18:30"}]},
        {
            "days": [0],
            "unlock_time": "07:00",
            "windows": [{"days": [1], "from": "18:30", "to": "18:30"}],
        },
        {
            "days": [0],
            "unlock_time": "07:00",
            "windows": [{"days": [1], "from": "15:00", "to": "24:00"}],
        },
        {
            "days": [0],
            "unlock_time": "07:00",
            "windows": [{"days": [7], "from": "15:00", "to": "18:30"}],
        },
        {
            "days": [0],
            "unlock_time": "07:00",
            "windows": [{"days": [1], "from": "15:00", "to": "16:00"}] * 15,
        },
    ],
)
def test_invalid_settings(admin: TestClient, body: dict[str, Any]) -> None:
    assert admin.put("/api/admin/activities/settings", json=body).status_code == 422


def test_days_off(admin: TestClient) -> None:
    queued_world(admin)
    r = admin.post(
        "/api/admin/activities/days-off",
        json={"from": "2026-11-09", "to": "2026-11-10", "note": "at grandma's"},
    )
    assert r.status_code == 201, r.text
    off = r.json()
    assert off["from"] == "2026-11-09"
    assert off["to"] == "2026-11-10"
    body = admin.get("/api/admin/activities").json()
    assert body["days_off"] == [off]
    assert body["queue"][0]["last_date"] == "2026-11-16"  # Sat 7, Wed 11 ... Sat 14, Mon 16
    one = admin.post("/api/admin/activities/days-off", json={"from": "2026-12-24"}).json()
    assert one["to"] == "2026-12-24"
    assert admin.delete(f"/api/admin/activities/days-off/{off['id']}").status_code == 204
    assert admin.delete(f"/api/admin/activities/days-off/{off['id']}").status_code == 404
    bad = {"from": "2026-11-10", "to": "2026-11-09"}
    assert admin.post("/api/admin/activities/days-off", json=bad).status_code == 422


def test_queue_rule_7(admin: TestClient, clock: FixedClock) -> None:
    a = import_json(admin, pack("a")).json()["world_id"]
    b = import_json(admin, pack("b")).json()["world_id"]
    c = import_json(admin, pack("c")).json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [a, b]})
    clock.set(datetime(2026, 11, 10, 12, 0, tzinfo=BERLIN))
    body = admin.put("/api/admin/activities/queue", json={"world_ids": [a, c, b]}).json()
    assert [q["world_id"] for q in body["queue"]] == [a, c, b]
    assert body["queue"][0]["first_date"] == "2026-11-07"  # the running world keeps its dates
    body = admin.put("/api/admin/activities/queue", json={"world_ids": [b, a]}).json()
    assert body["queue"][0]["first_date"] == "2026-11-10"  # a new first world starts today
    dup = admin.put("/api/admin/activities/queue", json={"world_ids": [a, a]})
    assert dup.status_code == 422
    assert dup.json()["reason"] == "queue_duplicate"
    assert admin.put("/api/admin/activities/queue", json={"world_ids": [999]}).status_code == 422


def test_story_language_per_queued_world(admin: TestClient, device_token: str) -> None:
    """The parent picks the story language per queued world; the tablet gets it as
    `world.lang` (§14.7)."""
    a = import_json(admin, pack("a")).json()["world_id"]
    two = pack("two-langs")
    two["texts"].pop("el")
    b = import_json(admin, two).json()["world_id"]
    body = admin.put("/api/admin/activities/queue", json={"world_ids": [a, b]}).json()
    # Nothing chosen: DEFAULT_LOCALE (en in the tests).
    assert [(q["lang"], q["langs"]) for q in body["queue"]] == [
        ("en", ["de", "en", "el"]),
        ("en", ["de", "en"]),
    ]
    body = admin.put(
        "/api/admin/activities/queue", json={"world_ids": [a, b], "langs": {str(a): "el"}}
    ).json()
    assert body["queue"][0]["lang"] == "el"
    assert body["queue"][0]["first_date"] == "2026-11-07"  # the dates stay
    world = admin.get("/api/activities", headers=bearer(device_token)).json()["world"]
    assert world["lang"] == "el"
    assert set(world["texts"]) == {"de", "en", "el"}  # all texts still travel, for fallback
    # Reordering without langs keeps the choice.
    body = admin.put("/api/admin/activities/queue", json={"world_ids": [a, b]}).json()
    assert body["queue"][0]["lang"] == "el"
    # A language the world has no texts in, or a world that isn't queued: 422.
    r = admin.put(
        "/api/admin/activities/queue", json={"world_ids": [a, b], "langs": {str(b): "el"}}
    )
    assert r.status_code == 422
    assert r.json()["reason"] == "lang_not_in_world"
    r = admin.put("/api/admin/activities/queue", json={"world_ids": [b], "langs": {str(a): "de"}})
    assert r.json()["reason"] == "lang_not_queued"
    # Leaving the queue drops the choice: queued again, it is the default again.
    admin.put("/api/admin/activities/queue", json={"world_ids": [b]})
    body = admin.put("/api/admin/activities/queue", json={"world_ids": [b, a]}).json()
    assert body["queue"][1]["lang"] == "en"


def test_mutations_need_csrf(admin: TestClient) -> None:
    token = admin.headers.pop("X-CSRF-Token")
    try:
        for r in (
            admin.put("/api/admin/activities/settings", json={"days": [0], "unlock_time": "07:00"}),
            admin.put("/api/admin/activities/queue", json={"world_ids": []}),
            admin.post("/api/admin/activities/days-off", json={"from": "2026-11-09"}),
            admin.post("/api/admin/worlds/examples/lighthouse-island"),
            import_json(admin, pack()),
        ):
            assert r.status_code == 403, r.text
            assert r.json()["reason"] == "csrf_failed"
    finally:
        admin.headers["X-CSRF-Token"] = token


# ---- admin: worlds and packs ----------------------------------------------------------------


def test_import_dry_run_then_real_then_replace(admin: TestClient, device_token: str) -> None:
    r = import_json(admin, pack(), dry_run=True)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "slug": "test-world",
        "world_id": None,
        "replaced": False,
        "chapters": 6,
        "tasks": 24,
        "complete": True,
        "warnings": [],
    }
    assert admin.get("/api/admin/activities").json()["worlds"] == []

    world_id = queued_world(admin)
    admin.post(
        "/api/activities/done",
        json={"world_id": world_id, "position": 1, "kind": "draw"},
        headers=bearer(device_token),
    )
    r = import_json(admin, pack(title="Changed"))
    assert r.json()["replaced"] is True
    assert r.json()["world_id"] == world_id
    world = admin.get("/api/activities", headers=bearer(device_token)).json()["world"]
    assert world["id"] == world_id
    assert world["chapters"][0]["texts"]["en"]["title"] == "Changed"
    assert world["chapters"][0]["done_kinds"] == ["draw"]


def test_import_reports_warnings(admin: TestClient) -> None:
    body = pack(chapters=1)
    body["chapters"][0]["tasks"] = [task("draw")]
    del body["chapters"][0]["texts"]["en"]
    report = import_json(admin, body, dry_run=True).json()
    assert report["complete"] is False
    warnings = report["warnings"]
    assert {
        "code": "missing_kind",
        "chapter": 1,
        "kind": "calc",
        "lang": None,
        "level": None,
        "material": None,
    } in warnings
    assert {
        "code": "missing_translation",
        "chapter": 1,
        "kind": None,
        "lang": "en",
        "level": None,
        "material": None,
    } in warnings


def test_import_reports_problems_and_materials(admin: TestClient) -> None:
    """§15.2: problems in every language and level, the same answers; §15.5: materials off
    the simple list only warn."""
    body = pack(chapters=1)
    calc = body["chapters"][0]["tasks"][1]["texts"]
    del calc["el"]["problems"]["harder"]
    calc["de"]["problems"]["normal"][1]["answer"] = 4
    body["chapters"][0]["tasks"][2]["texts"]["en"]["materials"] = ["Glue", "Beads"]
    report = import_json(admin, body, dry_run=True).json()
    assert report["complete"] is False
    assert {
        (w["code"], w["kind"], w["lang"], w["level"], w["material"]) for w in report["warnings"]
    } == {
        ("missing_problems", "calc", "el", "harder", None),
        ("problems_mismatch", "calc", "en", "normal", None),  # de comes first and differs
        ("problems_mismatch", "calc", "el", "normal", None),
        ("unlisted_material", "write", "en", None, "Beads"),
    }
    body = pack(chapters=1)
    body["chapters"][0]["tasks"][2]["texts"]["en"]["materials"] = ["Beads"]
    report = import_json(admin, body, dry_run=True).json()
    assert report["complete"] is True
    assert [w["code"] for w in report["warnings"]] == ["unlisted_material"]
    summary = import_json(admin, body).json()
    assert summary["complete"] is True
    worlds_out = admin.get("/api/admin/activities").json()["worlds"]
    assert worlds_out[0]["complete"] is True


def test_problems_on_a_read_task_are_invalid(admin: TestClient) -> None:
    body = pack(chapters=1)
    body["chapters"][0]["tasks"][3]["texts"]["en"]["problems"] = {"normal": problems_of("normal")}
    r = import_json(admin, body)
    assert r.status_code == 422
    assert r.json()["reason"] == "invalid_pack"


def test_import_without_greek_is_not_complete(admin: TestClient) -> None:
    body = pack(chapters=1)
    del body["texts"]["el"]
    for t in body["chapters"][0]["tasks"]:
        del t["texts"]["el"]
    report = import_json(admin, body, dry_run=True).json()
    assert report["complete"] is False
    assert {(w["code"], w["chapter"], w["kind"], w["lang"]) for w in report["warnings"]} == {
        ("missing_translation", None, None, "el"),
        ("missing_translation", 1, "draw", "el"),
        ("missing_translation", 1, "calc", "el"),
        ("missing_translation", 1, "write", "el"),
        ("missing_translation", 1, "read", "el"),
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        json.dumps({"slug": "x"}).encode(),
        json.dumps(pack() | {"slug": "Bad Slug"}).encode(),
    ],
)
def test_invalid_pack(admin: TestClient, raw: bytes) -> None:
    r = admin.put("/api/admin/worlds/import", content=raw)
    assert r.status_code == 422, r.text
    assert r.json()["reason"] == "invalid_pack"
    assert r.json()["params"]["errors"]


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_with_cover(admin: TestClient, device_token: str) -> None:
    body = pack() | {"cover": "pics/cover.png"}
    raw = make_zip(
        {"lighthouse/world.json": json.dumps(body).encode(), "lighthouse/pics/cover.png": png()}
    )
    r = admin.put("/api/admin/worlds/import", content=raw)
    assert r.status_code == 200, r.text
    world_id = r.json()["world_id"]
    cover = admin.get(f"/api/admin/worlds/{world_id}").json()["cover"]
    assert cover["url"].startswith(f"/api/thumbs/w{world_id}-")
    assert cover["aspect"] == pytest.approx(64 / 48, abs=0.01)
    served = admin.get(cover["url"], headers=bearer(device_token))
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/webp"


@pytest.mark.parametrize(
    "files",
    [
        {"readme.txt": b"hi"},
        {"world.json": json.dumps(pack() | {"cover": "missing.png"}).encode()},
        {"world.json": json.dumps(pack() | {"cover": "c.png"}).encode(), "c.png": b"not a png"},
    ],
)
def test_bad_zips(admin: TestClient, files: dict[str, bytes]) -> None:
    r = admin.put("/api/admin/worlds/import", content=make_zip(files))
    assert r.status_code == 422, r.text
    assert r.json()["reason"] == "invalid_pack"


def test_cover_upload_and_reset(admin: TestClient, settings: Settings) -> None:
    world_id = import_json(admin, pack()).json()["world_id"]
    r = admin.put(f"/api/admin/worlds/{world_id}/cover", content=png())
    assert r.status_code == 200, r.text
    key = r.json()["cover"]["url"].removeprefix("/api/thumbs/").removesuffix(".webp")
    assert (settings.thumbs_dir / f"{key}-md.webp").exists()
    r = admin.delete(f"/api/admin/worlds/{world_id}/cover")
    assert r.json()["cover"] is None
    assert not (settings.thumbs_dir / f"{key}-md.webp").exists()


def test_edit_and_export(admin: TestClient) -> None:
    world_id = import_json(admin, pack()).json()["world_id"]
    full = admin.get(f"/api/admin/worlds/{world_id}").json()
    assert full["chapters"][0]["tasks"][0]["texts"]["de"]["easier"] == "leichter"
    edited = pack(title="Edited") | {"color": "#0F7A70"}
    r = admin.put(f"/api/admin/worlds/{world_id}", json=edited)
    assert r.status_code == 200, r.text
    assert r.json()["color"] == "#0F7A70"
    assert r.json()["chapters"][0]["texts"]["en"]["title"] == "Edited"
    r = admin.put(f"/api/admin/worlds/{world_id}", json=pack("other-slug"))
    assert r.status_code == 422
    assert r.json()["reason"] == "slug_mismatch"

    r = admin.get(f"/api/admin/worlds/{world_id}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="test-world.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["world.json"]  # no pictures
        exported = json.loads(zf.read("world.json"))
    assert "cover" not in exported
    assert "id" not in exported
    assert "image" not in exported["chapters"][0]
    assert Pack.model_validate(exported).chapters[0].texts["en"].title == "Edited"


def test_delete_world_cascades(admin: TestClient, device_token: str, settings: Settings) -> None:
    world_id = queued_world(admin)
    admin.post(
        "/api/activities/done",
        json={"world_id": world_id, "position": 1, "kind": "draw"},
        headers=bearer(device_token),
    )
    assert done_count(settings) == 1
    log = admin.get("/api/admin/activities").json()["log"]
    assert log[0]["date"] == "2026-11-07"
    assert log[0]["kinds"] == ["draw"]
    assert admin.delete(f"/api/admin/worlds/{world_id}").status_code == 204
    assert done_count(settings) == 0
    body = admin.get("/api/admin/activities").json()
    assert body["queue"] == []
    assert body["worlds"] == []
    assert admin.get("/api/activities", headers=bearer(device_token)).json()["world"] is None
    assert admin.get(f"/api/admin/worlds/{world_id}").status_code == 404


# ---- levels and reading (§14.1, §14.2) -------------------------------------------------------


def set_level(admin: TestClient, level: str) -> Any:
    r = admin.put(
        "/api/admin/activities/settings",
        json={"days": [0, 1, 2, 3, 4, 5], "unlock_time": "07:00", "level": level},
    )
    assert r.status_code == 200, r.text
    return r.json()


def device_tasks(admin: TestClient, device_token: str) -> dict[str, Any]:
    body = admin.get("/api/activities", headers=bearer(device_token)).json()
    return {t["kind"]: t["texts"] for t in body["world"]["chapters"][0]["tasks"]}


def test_level_round_trip(admin: TestClient) -> None:
    assert set_level(admin, "harder")["settings"]["level"] == "harder"
    # Omitting the level keeps it.
    r = admin.put("/api/admin/activities/settings", json={"days": [0], "unlock_time": "07:00"})
    assert r.json()["settings"] == {
        "days": [0],
        "unlock_time": "07:00",
        "level": "harder",
        "windows": [],
    }
    assert admin.get("/api/admin/activities").json()["settings"]["level"] == "harder"


@pytest.mark.parametrize(
    ("level", "draw", "read", "question"),
    [
        ("normal", "Do it", "en normal", "normal?"),
        ("easier", "easier", "en easy", "easier?"),
        ("harder", "harder", "en hard", "harder?"),
    ],
)
def test_the_tablet_gets_the_childs_level(
    admin: TestClient, device_token: str, level: str, draw: str, read: str, question: str
) -> None:
    queued_world(admin)
    set_level(admin, level)
    body = admin.get("/api/activities", headers=bearer(device_token)).json()
    assert body["level"] == level
    tasks = device_tasks(admin, device_token)
    assert tasks["draw"]["en"]["text"] == draw
    assert tasks["draw"]["en"]["questions"] == []
    assert tasks["read"]["en"] == {
        "title": "R",
        "text": read,
        "materials": [],
        "questions": [
            {"question": question, "answers": ["a", "b", "c"], "correct": 1, "audio": None}
        ],
        "problems": [],
        "audio": {},
    }
    # Calc: the problems of the level, with answer and hint (§15.2); no voice, no audio.
    assert tasks["calc"]["en"]["text"] == draw
    assert tasks["calc"]["en"]["questions"] == []
    assert tasks["calc"]["en"]["problems"] == [
        {"text": text, "answer": answer, "hint": "count", "audio": {"text": None, "hint": None}}
        for text, answer in CALC_PROBLEMS[level]
    ]


def test_missing_levels_fall_back(admin: TestClient, device_token: str) -> None:
    body = pack()
    for chapter in body["chapters"]:
        del chapter["tasks"][0]["texts"]["en"]["harder"]
        del chapter["tasks"][3]["texts"]["en"]["harder"]
        del chapter["tasks"][3]["texts"]["en"]["questions"]["harder"]
    world_id = import_json(admin, body).json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [world_id]})
    set_level(admin, "harder")
    tasks = device_tasks(admin, device_token)
    assert tasks["draw"]["en"]["text"] == "Do it"
    assert tasks["draw"]["de"]["text"] == "schwerer"
    assert tasks["read"]["en"]["text"] == "en normal"
    assert tasks["read"]["en"]["questions"][0]["question"] == "normal?"


def test_missing_problem_levels_fall_back_to_normal(admin: TestClient, device_token: str) -> None:
    body = pack()
    for chapter in body["chapters"]:
        del chapter["tasks"][1]["texts"]["en"]["problems"]["easier"]
    world_id = import_json(admin, body).json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [world_id]})
    set_level(admin, "easier")
    tasks = device_tasks(admin, device_token)
    assert [p["text"] for p in tasks["calc"]["en"]["problems"]] == ["sum?", "left?"]
    assert [p["text"] for p in tasks["calc"]["de"]["problems"]] == ["easy sum?"]


def keys_in(value: Any) -> set[str]:
    """Every dict key anywhere in a JSON value."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_in(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_in(v)}
    return set()


def test_the_tablet_never_sees_other_levels_or_spoken_forms(
    admin: TestClient, device_token: str
) -> None:
    body = pack()
    body["texts"]["en"]["say"] = {"name": "Wurld"}
    body["chapters"][0]["texts"]["en"]["say"] = {"intro": "spoken intro"}
    world_id = import_json(admin, body).json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [world_id]})
    for level in ("easier", "normal", "harder"):
        set_level(admin, level)
        r = admin.get("/api/activities", headers=bearer(device_token))
        assert keys_in(r.json()).isdisjoint({"easier", "harder", "say"}), level
        assert "spoken" not in r.text, level
    # The admin sees everything.
    full = admin.get(f"/api/admin/worlds/{world_id}").json()
    assert full["texts"]["en"]["say"] == {"name": "Wurld"}
    read = full["chapters"][0]["tasks"][3]["texts"]["en"]
    assert read["say"] == {"text": "en spoken"}
    assert set(read["questions"]) == {"normal", "easier", "harder"}


def attempts_of(settings: Settings) -> dict[str, int | None]:
    with make_engine(settings.db_path).connect() as conn:
        rows = conn.execute(select(activity_done.c.kind, activity_done.c.attempts)).all()
        return {r.kind: r.attempts for r in rows}


def test_attempts_are_stored_for_read_tasks(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    auth = bearer(device_token)
    done = {"world_id": world_id, "position": 1}
    for bad in (0, 51, "x"):
        r = admin.post(
            "/api/activities/done", json=done | {"kind": "read", "attempts": bad}, headers=auth
        )
        assert r.status_code == 422, bad
    r = admin.post(
        "/api/activities/done", json=done | {"kind": "read", "attempts": 2}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.json()["world"]["chapters"][0]["done_kinds"] == ["read"]
    # A repeated done keeps the first value.
    admin.post("/api/activities/done", json=done | {"kind": "read", "attempts": 1}, headers=auth)
    # Other kinds store NULL; a done without attempts is fine.
    admin.post("/api/activities/done", json=done | {"kind": "draw", "attempts": 3}, headers=auth)
    admin.post("/api/activities/done", json=done | {"kind": "calc"}, headers=auth)
    assert attempts_of(settings) == {"read": 2, "draw": None, "calc": None}
    log = admin.get("/api/admin/activities").json()["log"]
    assert sorted(log[0]["kinds"]) == ["calc", "draw", "read"]
    assert log[0]["attempts"] == 2


def stars_of(settings: Settings) -> list[int]:
    with make_engine(settings.db_path).connect() as conn:
        return list(
            conn.execute(select(reward_ledger.c.delta).order_by(reward_ledger.c.id)).scalars()
        )


def test_attempts_and_stars_for_calc_tasks(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    """§15.2: attempts = 1 + the wrong answers; right at the first try earns 1 more star."""
    world_id = queued_world(admin)
    auth = bearer(device_token)
    done = {"world_id": world_id, "position": 1, "kind": "calc"}
    r = admin.post("/api/activities/done", json=done | {"attempts": 1}, headers=auth)
    assert r.status_code == 200, r.text
    admin.post("/api/activities/done", json=done | {"attempts": 4}, headers=auth)  # repeated
    assert attempts_of(settings) == {"calc": 1}
    assert stars_of(settings) == [2]
    reading = {"world_id": world_id, "position": 1, "kind": "read", "attempts": 3}
    admin.post("/api/activities/done", json=reading, headers=auth)
    assert stars_of(settings) == [2, 1]


def test_a_calc_task_with_wrong_answers_earns_one_star(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    r = admin.post(
        "/api/activities/done",
        json={"world_id": world_id, "position": 1, "kind": "calc", "attempts": 3},
        headers=bearer(device_token),
    )
    assert r.status_code == 200, r.text
    assert attempts_of(settings) == {"calc": 3}
    assert stars_of(settings) == [1]
    row = admin.get("/api/admin/activities").json()["log"][0]
    assert (row["attempts"], row["calc_attempts"]) == (None, 3)  # the log shows maths tries (§15.2)


def test_log_without_a_read_task_has_no_attempts(admin: TestClient, device_token: str) -> None:
    world_id = queued_world(admin)
    admin.post(
        "/api/activities/done",
        json={"world_id": world_id, "position": 1, "kind": "write", "attempts": 4},
        headers=bearer(device_token),
    )
    assert admin.get("/api/admin/activities").json()["log"][0]["attempts"] is None


def test_import_reports_missing_levels_and_questions(admin: TestClient) -> None:
    body = pack(chapters=1)
    del body["chapters"][0]["tasks"][0]["texts"]["de"]["easier"]
    del body["chapters"][0]["tasks"][3]["texts"]["el"]["questions"]
    report = import_json(admin, body, dry_run=True).json()
    assert report["complete"] is False
    assert {
        (w["code"], w["chapter"], w["kind"], w["lang"], w["level"]) for w in report["warnings"]
    } == {
        ("missing_level", 1, "draw", "de", "easier"),
        ("missing_questions", 1, "read", "el", "easier"),
        ("missing_questions", 1, "read", "el", "normal"),
        ("missing_questions", 1, "read", "el", "harder"),
    }


def test_questions_on_a_draw_task_are_invalid(admin: TestClient) -> None:
    body = pack(chapters=1)
    body["chapters"][0]["tasks"][0]["texts"]["en"]["questions"] = {"normal": questions("x")}
    r = import_json(admin, body)
    assert r.status_code == 422
    assert r.json()["reason"] == "invalid_pack"


# ---- bundled examples -----------------------------------------------------------------------


def bundled() -> list[Path]:
    return sorted(p for p in PACKS_DIR.iterdir() if p.is_dir()) if PACKS_DIR.is_dir() else []


def test_lighthouse_island_is_bundled() -> None:
    """The public image ships one example pack; the family image adds the others through
    EXTRA_PACKS_DIR (§13.5)."""
    assert [p.name for p in bundled()] == ["lighthouse-island"]


@pytest.mark.parametrize("folder", bundled(), ids=lambda p: p.name)
def test_bundled_packs_pass_packcheck(folder: Path) -> None:
    """Valid and complete, a cover and a picture per chapter, problem sets, simple materials
    and write texts (§13.5, §15.2, §15.4, §15.5): the same checks as `kidsmedia.packcheck`."""
    assert packcheck.check_pack(folder) == []


def test_packcheck_reports_problems(tmp_path: Path) -> None:
    body = pack("bad-world", chapters=1)
    body["chapters"][0]["image"] = "chapter-1.webp"
    folder = example_folder(tmp_path / "packs", "bad-world", body, {})
    problems = packcheck.check_pack(folder)
    assert "cover: no picture" in problems
    assert "chapter 1: chapter-1.webp is missing" in problems
    (tmp_path / "packs" / "other").mkdir()
    assert packcheck.pack_folders(tmp_path / "packs") == [folder]
    assert packcheck.main([str(tmp_path / "packs")]) == 1
    assert packcheck.main([str(tmp_path / "empty-nothing")]) == 1


def test_packcheck_passes_the_bundled_packs() -> None:
    assert packcheck.main([str(PACKS_DIR)]) == 0


def test_import_an_example(admin: TestClient) -> None:
    examples = admin.get("/api/admin/activities").json()["examples"]
    if not examples:
        pytest.skip("no bundled packs")
    slug = examples[0]["slug"]
    assert examples[0]["imported"] is False
    r = admin.post(f"/api/admin/worlds/examples/{slug}")
    assert r.status_code == 200, r.text
    assert r.json()["complete"] is True
    world = admin.get(f"/api/admin/worlds/{r.json()['world_id']}").json()
    assert world["cover"] is not None
    assert all(c["image"] is not None for c in world["chapters"])
    examples = admin.get("/api/admin/activities").json()["examples"]
    assert next(e for e in examples if e["slug"] == slug)["imported"] is True
    assert admin.post("/api/admin/worlds/examples/nope").status_code == 404


def test_examples_follow_the_example_order(
    admin: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 31: EXAMPLE_ORDER first, unknown slugs after it alphabetically."""
    slugs = ["zebra-zoo", "coral-city", "alpine-hut", "owl-mystery", "rocket-kiwi"]
    for slug in slugs:
        (tmp_path / slug).mkdir()
        (tmp_path / slug / "world.json").write_text(json.dumps(pack(slug=slug, chapters=1)))
    (tmp_path / "stray.json").write_text(json.dumps(pack(slug="stray", chapters=1)))
    monkeypatch.setattr(activities_admin, "PACKS_DIR", tmp_path)
    examples = admin.get("/api/admin/activities").json()["examples"]
    assert [e["slug"] for e in examples] == [
        "owl-mystery",
        "rocket-kiwi",
        "coral-city",
        "alpine-hut",
        "zebra-zoo",
    ]
    assert examples[0]["texts"]["el"] == {"name": "Κόσμος"}


def test_example_order_starts_with_the_greek_mystery() -> None:
    assert EXAMPLE_ORDER[0] == "owl-mystery"
    assert len(set(EXAMPLE_ORDER)) == len(EXAMPLE_ORDER)


def example_folder(root: Path, slug: str, body: dict[str, Any], files: dict[str, bytes]) -> Path:
    folder = root / slug
    folder.mkdir(parents=True)
    (folder / "world.json").write_text(json.dumps(body))
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return folder


def test_example_from_a_folder_imports_its_pictures(
    admin: TestClient,
    device_token: str,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = pack("folder-world", chapters=2) | {"cover": "cover.png"}
    body["chapters"][1]["image"] = "chapter-2.png"
    files = {"cover.png": png(), "chapter-2.png": png((10, 120, 200), (40, 40))}
    example_folder(tmp_path / "packs", "folder-world", body, files)
    monkeypatch.setattr(activities_admin, "PACKS_DIR", tmp_path / "packs")
    r = admin.post("/api/admin/worlds/examples/folder-world")
    assert r.status_code == 200, r.text
    world_id = r.json()["world_id"]
    full = admin.get(f"/api/admin/worlds/{world_id}").json()
    assert key_of(full["cover"]).startswith(f"w{world_id}-")
    assert full["chapters"][0]["image"] is None
    assert key_of(full["chapters"][1]["image"]).startswith(f"w{world_id}c2-")
    assert full["chapters"][1]["image"]["aspect"] == 1.0
    served = admin.get(full["chapters"][1]["image"]["url"], headers=bearer(device_token))
    assert served.status_code == 200

    # Re-adding the example replaces the pictures; unchanged ones keep their key.
    before = thumb_files(settings)
    assert admin.post("/api/admin/worlds/examples/folder-world").status_code == 200
    assert thumb_files(settings) == before


@pytest.mark.parametrize("name", ["../outside.png", "sub/c.png", "..", "a\\b.png"])
def test_example_folder_pictures_stay_in_the_folder(
    admin: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    (tmp_path / "outside.png").write_bytes(png())
    body = pack("escape", chapters=1) | {"cover": name}
    folder = example_folder(tmp_path / "packs", "escape", body, {})
    (folder / "sub").mkdir()
    (folder / "sub" / "c.png").write_bytes(png())
    monkeypatch.setattr(activities_admin, "PACKS_DIR", tmp_path / "packs")
    r = admin.post("/api/admin/worlds/examples/escape")
    assert r.status_code == 422, r.text
    assert r.json()["reason"] == "invalid_pack"
    assert admin.get("/api/admin/activities").json()["worlds"] == []


def test_example_folder_missing_picture(
    admin: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = pack("missing", chapters=1)
    body["chapters"][0]["image"] = "chapter-1.webp"
    example_folder(tmp_path / "packs", "missing", body, {})
    monkeypatch.setattr(activities_admin, "PACKS_DIR", tmp_path / "packs")
    r = admin.post("/api/admin/worlds/examples/missing")
    assert r.status_code == 422
    assert "chapters.0.image" in r.json()["params"]["errors"][0]


def test_extra_packs_dir_adds_examples_and_wins_on_the_slug(
    admin: TestClient, settings: Settings, tmp_path: Path
) -> None:
    """EXTRA_PACKS_DIR (§13.5): its packs join the bundled ones, in example order, and one
    with a bundled slug replaces the bundled pack."""
    extra = tmp_path / "extra"
    mine = pack("lighthouse-island", chapters=1)
    mine["texts"]["en"] = {"name": "Mine"}
    example_folder(extra, "owl-mystery", pack("owl-mystery", chapters=1), {})
    example_folder(extra, "lighthouse-island", mine, {})
    settings.extra_packs_dir = extra
    examples = admin.get("/api/admin/activities").json()["examples"]
    assert [e["slug"] for e in examples] == ["owl-mystery", "lighthouse-island"]
    assert examples[1]["texts"]["en"] == {"name": "Mine"}
    r = admin.post("/api/admin/worlds/examples/owl-mystery")
    assert r.status_code == 200, r.text


def test_a_missing_extra_packs_dir_is_ignored(
    admin: TestClient, settings: Settings, tmp_path: Path
) -> None:
    settings.extra_packs_dir = tmp_path / "not-mounted"
    examples = admin.get("/api/admin/activities").json()["examples"]
    assert [e["slug"] for e in examples] == ["lighthouse-island"]


# ---- chapter pictures -----------------------------------------------------------------------


def picture_zip(body: dict[str, Any], colors: dict[str, tuple[int, int, int]]) -> bytes:
    return make_zip(
        {"world.json": json.dumps(body).encode()} | {n: png(c) for n, c in colors.items()}
    )


def test_chapter_pictures_round_trip(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    body = pack(chapters=3) | {"cover": "cover.png"}
    body["chapters"][0]["image"] = "chapter-1.png"
    body["chapters"][2]["image"] = "chapter-3.png"
    colors = {"cover.png": (1, 2, 3), "chapter-1.png": (200, 0, 0), "chapter-3.png": (0, 0, 200)}
    r = admin.put("/api/admin/worlds/import", content=picture_zip(body, colors))
    assert r.status_code == 200, r.text
    world_id = r.json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [world_id]})

    world = admin.get("/api/activities", headers=bearer(device_token)).json()["world"]
    images = [c["image"] for c in world["chapters"]]
    assert images[1] is None
    first, third = key_of(images[0]), key_of(images[2])
    assert first.startswith(f"w{world_id}c1-")
    assert third.startswith(f"w{world_id}c3-")
    assert THUMB_KEY_RE.match(first)
    assert images[0]["aspect"] == pytest.approx(64 / 48, abs=0.01)
    for size in ("sm", "md", "lg"):
        served = admin.get(f"{images[0]['url']}?size={size}", headers=bearer(device_token))
        assert served.status_code == 200, size
        assert served.headers["content-type"] == "image/webp"
    cover = key_of(world["cover"])
    assert thumb_files(settings) == {
        f"{k}-{s}.webp" for k in (cover, first, third) for s in ("sm", "md", "lg")
    }

    # Replace: chapter 1 gets a new picture, chapter 3 loses its picture, the cover stays.
    body["chapters"][0]["image"] = "new.png"
    del body["chapters"][2]["image"]
    colors = {"cover.png": (1, 2, 3), "new.png": (0, 200, 0)}
    r = admin.put("/api/admin/worlds/import", content=picture_zip(body, colors))
    assert r.json()["world_id"] == world_id
    assert r.json()["replaced"] is True
    world = admin.get("/api/activities", headers=bearer(device_token)).json()["world"]
    images = [c["image"] for c in world["chapters"]]
    assert images[2] is None
    new_first = key_of(images[0])
    assert new_first != first
    assert key_of(world["cover"]) == cover
    assert thumb_files(settings) == {
        f"{k}-{s}.webp" for k in (cover, new_first) for s in ("sm", "md", "lg")
    }
    assert admin.get(f"/api/thumbs/{first}.webp", headers=bearer(device_token)).status_code == 404

    assert admin.delete(f"/api/admin/worlds/{world_id}").status_code == 204
    assert thumb_files(settings) == set()


def test_pictures_are_not_stored_as_client_input(admin: TestClient, settings: Settings) -> None:
    """Server-side picture fields are never accepted from clients, file names never stored."""
    body = pack(chapters=1)
    body["chapters"][0]["image_key"] = "w1c1-abcdef"
    r = import_json(admin, body)
    assert r.status_code == 422
    body = pack(chapters=1) | {"cover": "cover.webp"}
    body["chapters"][0]["image"] = "chapter-1.webp"
    r = import_json(admin, body)  # JSON alone: picture names are ignored, as for the cover
    assert r.status_code == 200, r.text
    world_id = r.json()["world_id"]
    full = admin.get(f"/api/admin/worlds/{world_id}").json()
    assert full["cover"] is None
    assert full["chapters"][0]["image"] is None
    edit = pack(chapters=1)
    edit["chapters"][0]["image_aspect"] = 2.0
    assert admin.put(f"/api/admin/worlds/{world_id}", json=edit).status_code == 422
    with make_engine(settings.db_path).connect() as conn:
        content = conn.execute(select(worlds.c.content)).scalar_one()
    assert "image" not in content["chapters"][0]
    assert "image_key" not in content["chapters"][0]


def test_chapter_image_upload_and_delete(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = import_json(admin, pack(chapters=2)).json()["world_id"]
    r = admin.put(f"/api/admin/worlds/{world_id}/chapters/2/image", content=png())
    assert r.status_code == 200, r.text
    chapters = r.json()["chapters"]
    assert chapters[0]["image"] is None
    key = key_of(chapters[1]["image"])
    assert key.startswith(f"w{world_id}c2-")
    assert (settings.thumbs_dir / f"{key}-md.webp").exists()
    served = admin.get(chapters[1]["image"]["url"], headers=bearer(device_token))
    assert served.status_code == 200

    # A new picture replaces the old files.
    r = admin.put(f"/api/admin/worlds/{world_id}/chapters/2/image", content=png((9, 9, 9)))
    new_key = key_of(r.json()["chapters"][1]["image"])
    assert new_key != key
    assert not (settings.thumbs_dir / f"{key}-md.webp").exists()

    # The editor's save keeps pictures by position; dropped chapters lose theirs.
    r = admin.put(f"/api/admin/worlds/{world_id}", json=pack(chapters=2, title="Edited"))
    assert r.status_code == 200, r.text
    assert key_of(r.json()["chapters"][1]["image"]) == new_key
    assert r.json()["chapters"][0]["texts"]["en"]["title"] == "Edited"
    r = admin.put(f"/api/admin/worlds/{world_id}", json=pack(chapters=1))
    assert r.status_code == 200, r.text
    assert not (settings.thumbs_dir / f"{new_key}-md.webp").exists()
    r = admin.put(f"/api/admin/worlds/{world_id}", json=pack(chapters=2))
    assert r.json()["chapters"][1]["image"] is None

    r = admin.put(f"/api/admin/worlds/{world_id}/chapters/1/image", content=png())
    key = key_of(r.json()["chapters"][0]["image"])
    r = admin.delete(f"/api/admin/worlds/{world_id}/chapters/1/image")
    assert r.status_code == 200, r.text
    assert r.json()["chapters"][0]["image"] is None
    assert thumb_files(settings) == set()

    for position in (0, 3):
        r = admin.put(f"/api/admin/worlds/{world_id}/chapters/{position}/image", content=png())
        assert r.status_code == 404
        assert r.json()["reason"] == "chapter_not_found"
        r = admin.delete(f"/api/admin/worlds/{world_id}/chapters/{position}/image")
        assert r.status_code == 404
    assert admin.put("/api/admin/worlds/999/chapters/1/image", content=png()).status_code == 404
    r = admin.put(f"/api/admin/worlds/{world_id}/chapters/1/image", content=b"not an image")
    assert r.status_code == 422
    assert r.json()["reason"] == "bad_image"


def test_chapter_image_needs_csrf(admin: TestClient) -> None:
    world_id = import_json(admin, pack(chapters=1)).json()["world_id"]
    token = admin.headers.pop("X-CSRF-Token")
    try:
        r = admin.put(f"/api/admin/worlds/{world_id}/chapters/1/image", content=png())
        assert r.status_code == 403
        r = admin.delete(f"/api/admin/worlds/{world_id}/chapters/1/image")
        assert r.status_code == 403
    finally:
        admin.headers["X-CSRF-Token"] = token


def test_export_zip_keeps_pictures_on_reimport(admin: TestClient, settings: Settings) -> None:
    body = pack(chapters=2) | {"cover": "c.png"}
    body["chapters"][1]["image"] = "two.png"
    raw = picture_zip(body, {"c.png": (1, 2, 3), "two.png": (4, 5, 6)})
    world_id = admin.put("/api/admin/worlds/import", content=raw).json()["world_id"]

    r = admin.get(f"/api/admin/worlds/{world_id}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="test-world.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert sorted(zf.namelist()) == ["chapter-2.webp", "cover.webp", "world.json"]
        exported = json.loads(zf.read("world.json"))
        assert Image.open(io.BytesIO(zf.read("cover.webp"))).format == "WEBP"
    assert exported["cover"] == "cover.webp"
    assert "image" not in exported["chapters"][0]
    assert exported["chapters"][1]["image"] == "chapter-2.webp"
    assert "image_key" not in exported["chapters"][1]
    Pack.model_validate(exported)

    # Into a fresh server: delete, then import the export again.
    assert admin.delete(f"/api/admin/worlds/{world_id}").status_code == 204
    r = admin.put("/api/admin/worlds/import", content=r.content)
    assert r.status_code == 200, r.text
    full = admin.get(f"/api/admin/worlds/{r.json()['world_id']}").json()
    assert full["cover"] is not None
    assert full["chapters"][0]["image"] is None
    assert full["chapters"][1]["image"]["aspect"] == pytest.approx(64 / 48, abs=0.001)


# ---- opening times (§14.4a) -------------------------------------------------------------------

# The §14.4a example: Mon-Fri 15:00-18:30, Sat-Sun 09:00-18:00.
WINDOWS = [
    {"days": [0, 1, 2, 3, 4], "from": "15:00", "to": "18:30"},
    {"days": [5, 6], "from": "09:00", "to": "18:00"},
]


def set_windows(admin: TestClient, windows: list[dict[str, Any]]) -> Any:
    r = admin.put(
        "/api/admin/activities/settings",
        json={"days": [0, 1, 2, 3, 4, 5, 6], "unlock_time": "07:00", "windows": windows},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_windows_round_trip(admin: TestClient) -> None:
    assert set_windows(admin, WINDOWS)["settings"]["windows"] == WINDOWS
    # Omitting the windows keeps them; an empty list clears them.
    r = admin.put("/api/admin/activities/settings", json={"days": [0, 1], "unlock_time": "07:00"})
    assert r.json()["settings"]["windows"] == WINDOWS
    assert admin.get("/api/admin/activities").json()["settings"]["windows"] == WINDOWS
    assert set_windows(admin, [])["settings"]["windows"] == []


def test_the_tablet_gets_the_opening_times(
    admin: TestClient, device_token: str, clock: FixedClock
) -> None:
    auth = bearer(device_token)
    body = admin.get("/api/activities", headers=auth).json()
    assert (body["windows"], body["open_now"], body["opens_at"]) == ([], True, None)

    set_windows(admin, WINDOWS)
    body = admin.get("/api/activities", headers=auth).json()  # Sat 14:20
    assert (body["windows"], body["open_now"], body["opens_at"]) == (WINDOWS, True, None)
    clock.set(datetime(2026, 11, 8, 18, 10, tzinfo=BERLIN))  # Sun 18:10
    body = admin.get("/api/activities", headers=auth).json()
    assert body["open_now"] is False
    assert body["opens_at"] == "2026-11-09T15:00:00+01:00"  # Mon 15:00


@pytest.mark.parametrize(
    ("hh", "mm", "status"),
    [(15, 0, 200), (18, 29, 200), (18, 30, 200), (18, 44, 200), (18, 45, 409), (18, 50, 409)],
)
def test_done_outside_the_windows(
    admin: TestClient, device_token: str, clock: FixedClock, hh: int, mm: int, status: int
) -> None:
    world_id = queued_world(admin)  # chapter 1 on Sat 7 Nov
    set_windows(admin, WINDOWS)
    clock.set(datetime(2026, 11, 10, hh, mm, tzinfo=BERLIN))  # Tue
    auth = bearer(device_token)
    body = admin.get("/api/activities", headers=auth).json()
    assert body["open_now"] is ((15, 0) <= (hh, mm) < (18, 30))
    r = admin.post(
        "/api/activities/done",
        json={"world_id": world_id, "position": 1, "kind": "draw"},
        headers=auth,
    )
    assert r.status_code == status, r.text
    if status == 409:
        assert r.json() == {
            "reason": "activities_closed",
            "params": {"opens_at": "2026-11-11T15:00:00+01:00"},
        }


def test_closed_ideas_keep_rewards(admin: TestClient, device_token: str, clock: FixedClock) -> None:
    set_windows(admin, WINDOWS)
    clock.set(datetime(2026, 11, 10, 7, 30, tzinfo=BERLIN))  # Tue 07:30: closed
    body = admin.get("/api/activities", headers=bearer(device_token)).json()
    assert (body["open_now"], body["opens_at"]) == (False, "2026-11-10T15:00:00+01:00")
    assert admin.get("/api/rewards", headers=bearer(device_token)).status_code == 200


# ---- queued done taps (§14.4a: done_at) ------------------------------------------------------


def done_at(
    admin: TestClient, device_token: str, world_id: int, position: int, at: datetime | str
) -> Any:
    body = {"world_id": world_id, "position": position, "kind": "draw"}
    body["done_at"] = at if isinstance(at, str) else at.isoformat()
    return admin.post("/api/activities/done", json=body, headers=bearer(device_token))


def done_rows(settings: Settings) -> list[tuple[int, str, datetime]]:
    with make_engine(settings.db_path).connect() as conn:
        rows = conn.execute(
            select(activity_done.c.position, activity_done.c.day, activity_done.c.done_at)
        ).all()
    return [(r.position, r.day, r.done_at) for r in rows]


def test_a_queued_tap_is_judged_when_it_happened(
    admin: TestClient, device_token: str, clock: FixedClock, settings: Settings
) -> None:
    """§14.4a: "Tue 18:40 done, sent at 21:00 -> accepted; Tue 18:50 -> 409"."""
    world_id = queued_world(admin)
    set_windows(admin, WINDOWS)  # every day an activity day: chapter 1 on Sat 7 Nov
    clock.set(datetime(2026, 11, 10, 21, 0, tzinfo=BERLIN))  # Tue 21:00, closed
    r = done_at(admin, device_token, world_id, 1, datetime(2026, 11, 10, 18, 50, tzinfo=BERLIN))
    assert r.status_code == 409
    assert r.json()["reason"] == "activities_closed"
    r = done_at(admin, device_token, world_id, 1, datetime(2026, 11, 10, 18, 40, tzinfo=BERLIN))
    assert r.status_code == 200, r.text
    assert done_rows(settings) == [(1, "2026-11-10", datetime(2026, 11, 10, 17, 40))]  # UTC


def test_a_queued_tap_uses_the_unlock_and_the_day_of_its_moment(
    admin: TestClient, device_token: str, clock: FixedClock, settings: Settings
) -> None:
    world_id = queued_world(admin)
    set_windows(admin, [])  # every day: chapter 2 on Sun 8 Nov, unlocked at 07:00
    clock.set(datetime(2026, 11, 9, 10, 0, tzinfo=BERLIN))  # Mon: chapter 2 is unlocked now
    r = done_at(admin, device_token, world_id, 2, datetime(2026, 11, 8, 6, 30, tzinfo=BERLIN))
    assert r.status_code == 409
    assert r.json()["reason"] == "activity_locked"  # still locked at the tap
    r = done_at(admin, device_token, world_id, 2, "2026-11-08T23:50:00+01:00")
    assert r.status_code == 200, r.text
    assert done_rows(settings) == [(2, "2026-11-08", datetime(2026, 11, 8, 22, 50))]


def test_a_done_at_ahead_of_the_server_is_now(
    admin: TestClient, device_token: str, clock: FixedClock, settings: Settings
) -> None:
    world_id = queued_world(admin)
    now = clock.now()
    r = done_at(admin, device_token, world_id, 1, now + timedelta(seconds=40))
    assert r.status_code == 200, r.text
    assert done_rows(settings) == [(1, "2026-11-07", now.astimezone(UTC).replace(tzinfo=None))]


def test_a_done_at_older_than_7_days_is_refused(
    admin: TestClient, device_token: str, clock: FixedClock, settings: Settings
) -> None:
    world_id = queued_world(admin)
    r = done_at(admin, device_token, world_id, 1, clock.now() - timedelta(days=7, minutes=1))
    assert r.status_code == 409
    assert r.json() == {"reason": "activity_too_old", "params": {"max_days": 7}}
    r = done_at(admin, device_token, world_id, 1, "2026-11-07T14:00:00")  # no offset
    assert r.status_code == 422
    assert done_rows(settings) == []
