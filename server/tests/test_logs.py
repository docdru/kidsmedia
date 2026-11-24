"""Why the gems and the stars changed, for the tablet (SPEC §16.6, §14.4b).

The fixed clock starts on Saturday 2026-11-07 14:20 Europe/Berlin.
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from tests.test_activities_api import queued_world
from tests.test_done_admin import DONE, entry, finish_chapter
from tests.test_gems import gem_rule, gems, library
from tests.test_play import Tablet, add_media, bearer, group, rule
from tests.test_rewards_api import ask, give_stars, swap, wish


def log(c: TestClient, token: str, path: str, **params: Any) -> dict[str, Any]:
    return tablet_log(c, bearer(token), path, **params)


def tablet_log(c: TestClient, headers: dict[str, str], path: str, **params: Any) -> dict[str, Any]:
    r = c.get(path, headers=headers, params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def test_logs_need_a_device(client: TestClient, admin: TestClient) -> None:
    assert client.get("/api/gems/log").status_code == 401
    assert client.get("/api/rewards/log").status_code == 401
    assert admin.get("/api/gems/log").status_code == 401  # an admin session is no device


def test_empty_logs(admin: TestClient, device_token: str) -> None:
    assert log(admin, device_token, "/api/gems/log") == {"balance": 0, "entries": []}
    assert log(admin, device_token, "/api/rewards/log") == {"stars": 0, "entries": []}
    assert library_gems(admin, device_token)["news_id"] is None


def library_gems(c: TestClient, token: str) -> dict[str, Any]:
    gems_now: dict[str, Any] = c.get("/api/library", headers=bearer(token)).json()["gems"]
    return gems_now


def test_gem_log_says_why_newest_first_and_news_only_for_a_grown_up(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path, clock: FixedClock
) -> None:
    cartoons = group(admin, "video", "Cartoons")
    v1 = add_media(settings, audio_file, cartoons, 600, "Episode 1")
    gem_rule(admin, cartoons, cost=2)
    added = gems(admin, 3, "Room tidied")["entries"][0]
    assert library(tablet)["gems"] == {"balance": 3, "used": True, "news_id": added["id"]}

    assert tablet.start(v1)["allowed"]
    tablet.play(v1, 15)  # counts: -2
    assert library(tablet)["gems"]["news_id"] == added["id"]  # a spend is no news

    removed = gems(admin, -1, " ")["entries"][0]  # a blank note is stored as none
    body = tablet_log(tablet.c, tablet.h, "/api/gems/log")
    assert body["balance"] == 0
    rows = [(e["delta"], e["kind"], e["note"], e["title"]) for e in body["entries"]]
    assert rows == [
        (-1, "admin", None, None),
        (-2, "spend", None, "Episode 1"),
        (3, "admin", "Room tidied", None),
    ]
    assert body["entries"][0]["id"] == removed["id"]
    assert library(tablet)["gems"]["news_id"] == removed["id"]
    spend = body["entries"][1]
    assert spend["thumb"] is None or spend["thumb"]["url"].startswith("/api/thumbs/")
    assert set(spend) == {"id", "delta", "kind", "note", "title", "thumb", "created_at"}

    assert [
        e["delta"] for e in tablet_log(tablet.c, tablet.h, "/api/gems/log", limit=1)["entries"]
    ] == [-1]
    for bad in (0, 101):
        r = tablet.c.get("/api/gems/log", headers=tablet.h, params={"limit": bad})
        assert r.status_code == 422


def test_a_deleted_file_keeps_its_title_in_the_gem_log(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    cartoons = group(admin, "video", "Cartoons")
    v1 = add_media(settings, audio_file, cartoons, 600, "Episode 1")
    gem_rule(admin, cartoons, cost=1)
    gems(admin, 1)
    tablet.start(v1)
    tablet.play(v1, 15)
    assert admin.delete(f"/api/admin/media/{v1}").status_code in (200, 204)
    spend = tablet_log(tablet.c, tablet.h, "/api/gems/log")["entries"][0]
    assert (spend["kind"], spend["title"], spend["thumb"]) == ("spend", "Episode 1", None)


def test_star_log_names_the_chapter_the_bonus_the_swap_and_the_wish(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    finish_chapter(admin, device_token, world_id)  # the read task finishes it: +3
    read_id = entry(admin, "read")["id"]
    draw_id = entry(admin, "draw")["id"]
    assert admin.post(f"{DONE}/{draw_id}/bonus").status_code == 200  # +1

    rid = rule(admin, max_minutes=30)
    swap_id = swap(admin, rule_id=rid, extra_minutes=15, stars=1)
    r = admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token))
    assert r.status_code == 200, r.text  # -1
    wish_id = wish(admin, stars=2)
    request_id = ask(admin, device_token, wish_id).json()["wishes"][0]["request"]["id"]
    assert admin.post(f"/api/admin/rewards/requests/{request_id}/approve").status_code == 200

    body = log(admin, device_token, "/api/rewards/log")
    assert body["stars"] == 1
    kinds = [(e["kind"], e["delta"]) for e in body["entries"]]
    assert kinds == [("wish", -2), ("swap", -1), ("bonus", 1), ("chapter", 3)]
    wish_row, swap_row, bonus, chapter = body["entries"]
    assert wish_row["wish"]["en"] == {"name": "Choose dessert"}
    assert (wish_row["task"], wish_row["swap"]) == (None, None)
    assert swap_row["swap"]["extra_minutes"] == 15
    assert swap_row["swap"]["extra_items"] == 0
    assert swap_row["swap"]["groups"] == []  # an all-groups rule without groups yet
    task = chapter["task"]
    assert (task["world_id"], task["world_name"], task["position"], task["kind"]) == (
        world_id,
        "World",
        1,
        "read",
    )
    assert task["title"] == "R"
    assert "picture" in task
    assert (bonus["task"]["kind"], bonus["task"]["title"]) == ("draw", "One")

    # "Open again" takes the stars back: the reopen row has no task, nor has the earning row
    # of the task that is no longer done.
    assert admin.delete(f"{DONE}/{read_id}").status_code == 204
    body = log(admin, device_token, "/api/rewards/log")
    top, *_, old_chapter = body["entries"]
    assert (top["kind"], top["delta"], top["task"]) == ("reopen", -1, None)  # never below 0
    assert (old_chapter["kind"], old_chapter["task"]) == ("chapter", None)
    assert body["stars"] == 0


def test_star_log_keeps_rows_of_deleted_swaps_and_wishes(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    give_stars(settings, admin, 3)
    rid = rule(admin, max_items=1)
    swap_id = swap(admin, rule_id=rid, extra_items=1)
    admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token))
    assert admin.delete(f"/api/admin/rewards/swaps/{swap_id}").status_code == 204
    body = log(admin, device_token, "/api/rewards/log", limit=2)
    assert [(e["kind"], e["swap"]) for e in body["entries"]] == [("swap", None), ("earn", None)]
