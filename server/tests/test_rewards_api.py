"""Rewards over HTTP: stars, album, swaps and wishes (SPEC §14.4).

The fixed clock starts on Saturday 2026-11-07 14:20 Europe/Berlin; a world queued now has
chapter 1 unlocked today.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from kidsmedia.clock import FixedClock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import activity_done, insert_id, make_engine, reward_ledger, wish_requests
from kidsmedia.rewards import stars_for
from tests.test_activities_api import import_json, pack, picture_zip, queued_world
from tests.test_play import Tablet, add_media, bearer, group, rule

WISH = {"texts": {"de": {"name": "Nachtisch aussuchen"}, "en": {"name": "Choose dessert"}}}


def add_done(
    settings: Settings,
    world_id: int,
    position: int,
    kind: str = "draw",
    day: str = "2026-11-07",
    attempts: int | None = None,
) -> None:
    """A done task straight into the database (any chapter, any day), with its earned stars
    in the ledger as POST /api/activities/done records them."""
    at = to_db(datetime.fromisoformat(f"{day}T12:00:00+01:00"))
    with make_engine(settings.db_path).begin() as conn:
        done_id = insert_id(
            conn.execute(
                insert(activity_done).values(
                    profile_id=1,
                    world_id=world_id,
                    position=position,
                    kind=kind,
                    day=day,
                    done_at=at,
                    attempts=attempts,
                )
            )
        )
        conn.execute(
            insert(reward_ledger).values(
                profile_id=1,
                delta=stars_for(kind, attempts),
                kind="earn",
                ref_id=done_id,
                created_at=at,
            )
        )


def give_stars(settings: Settings, admin: TestClient, n: int) -> int:
    """n stars from n done tasks in a world of its own (6 chapters x 3 kinds)."""
    world_id: int = import_json(admin, pack("star-world")).json()["world_id"]
    for i in range(n):
        add_done(settings, world_id, i // 3 + 1, ("draw", "calc", "write")[i % 3])
    return world_id


def rewards(c: TestClient, token: str) -> dict[str, Any]:
    r = c.get("/api/rewards", headers=bearer(token))
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def swap(admin: TestClient, **kw: Any) -> int:
    r = admin.post("/api/admin/rewards/swaps", json={"stars": 2, "max_per_day": 1, **kw})
    assert r.status_code == 201, r.text
    swap_id: int = r.json()["id"]
    return swap_id


def wish(admin: TestClient, stars: int = 3, **kw: Any) -> int:
    r = admin.post("/api/admin/rewards/wishes", json={**WISH, "stars": stars, **kw})
    assert r.status_code == 201, r.text
    wish_id: int = r.json()["id"]
    return wish_id


def ask(c: TestClient, token: str, wish_id: int) -> Any:
    return c.post(f"/api/rewards/wishes/{wish_id}/request", headers=bearer(token))


# ---- stars and album ------------------------------------------------------------------------


def test_needs_a_device_or_an_admin(client: TestClient) -> None:
    assert client.get("/api/rewards").status_code == 401
    assert client.post("/api/rewards/swaps/1").status_code == 401
    assert client.get("/api/admin/rewards").status_code == 401


def test_nothing_yet(admin: TestClient, device_token: str) -> None:
    assert rewards(admin, device_token) == {"stars": 0, "album": [], "swaps": [], "wishes": []}
    body = admin.get("/api/admin/rewards").json()
    assert body == {
        "stars": 0,
        "earned": 0,
        "spent": 0,
        "swaps": [],
        "wishes": [],
        "requests": [],
        "ledger": [],
    }


def test_a_star_per_task_and_one_more_for_reading_right_first_time(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    done = {"world_id": world_id, "position": 1, "kind": "draw"}
    assert admin.post("/api/activities/done", json=done, headers=bearer(device_token)).is_success
    admin.post("/api/activities/done", json=done, headers=bearer(device_token))  # idempotent
    assert rewards(admin, device_token)["stars"] == 1
    add_done(settings, world_id, 2, "read", attempts=1)  # +2
    add_done(settings, world_id, 3, "read", attempts=3)  # +1
    add_done(settings, world_id, 4, "calc", attempts=None)  # +1
    assert rewards(admin, device_token)["stars"] == 5
    body = admin.get("/api/admin/rewards").json()
    assert (body["stars"], body["earned"], body["spent"]) == (5, 5, 0)
    assert body["ledger"] == []  # the admin's ledger lists spending only


def test_the_first_done_of_a_task_earns_a_ledger_row(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    done = {"world_id": world_id, "position": 1, "kind": "draw"}
    for _ in range(2):  # a repeated done earns nothing more
        r = admin.post("/api/activities/done", json=done, headers=bearer(device_token))
        assert r.status_code == 200, r.text
    with make_engine(settings.db_path).connect() as conn:
        rows = conn.execute(
            select(reward_ledger.c.delta, reward_ledger.c.kind, reward_ledger.c.ref_id)
        ).all()
        done_id = conn.execute(select(activity_done.c.id)).scalar_one()
    assert [tuple(r) for r in rows] == [(1, "earn", done_id)]


def test_deleting_a_world_keeps_the_stars(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    """§14.4: stars are never taken away, also not when their world goes."""
    world_id = give_stars(settings, admin, 5)
    wish_id = wish(admin, stars=2)
    request_id = ask(admin, device_token, wish_id).json()["wishes"][0]["request"]["id"]
    assert admin.post(f"/api/admin/rewards/requests/{request_id}/approve").is_success
    assert rewards(admin, device_token)["stars"] == 3

    assert admin.delete(f"/api/admin/worlds/{world_id}").status_code == 204
    seen = rewards(admin, device_token)
    assert (seen["stars"], seen["album"]) == (3, [])  # the stickers go with the world
    body = admin.get("/api/admin/rewards").json()
    assert (body["stars"], body["earned"], body["spent"]) == (3, 5, 2)


def test_album_stickers_are_the_chapter_pictures(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    body = pack(chapters=3) | {"cover": "cover.png"}
    body["chapters"][0]["image"] = "chapter-1.png"
    colors = {"cover.png": (1, 2, 3), "chapter-1.png": (200, 0, 0)}
    r = admin.put("/api/admin/worlds/import", content=picture_zip(body, colors))
    assert r.status_code == 200, r.text
    world_id = r.json()["world_id"]
    admin.put("/api/admin/activities/queue", json={"world_ids": [world_id]})
    auth = bearer(device_token)
    for kind in ("draw", "calc"):  # an extra task doesn't give a second sticker
        done = {"world_id": world_id, "position": 1, "kind": kind}
        assert admin.post("/api/activities/done", json=done, headers=auth).is_success
    add_done(settings, world_id, 3, day="2026-11-10")
    world = admin.get("/api/activities", headers=auth).json()["world"]

    album = rewards(admin, device_token)["album"]
    assert len(album) == 1
    page = album[0]
    assert page["world_id"] == world_id
    assert page["texts"]["en"]["name"] == "World"
    assert page["cover"] == world["cover"]
    assert page["stickers"] == [
        {"position": 1, "image": world["chapters"][0]["image"], "earned_on": "2026-11-07"},
        {"position": 3, "image": None, "earned_on": "2026-11-10"},  # no picture: app uses cover
    ]
    sticker = page["stickers"][0]["image"]["url"]
    assert admin.get(sticker, headers=auth).status_code == 200

    other = give_stars(settings, admin, 1)  # a world worked on later: the next page
    assert [p["world_id"] for p in rewards(admin, device_token)["album"]] == [world_id, other]


# ---- swaps ----------------------------------------------------------------------------------


def test_a_swap_buys_one_more_file_today(
    admin: TestClient, tablet: Tablet, device_token: str, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 1800, "a")
    b = add_media(settings, audio_file, moon, 1800, "b")
    rid = rule(admin, all_groups=False, group_ids=[moon], max_items=1)
    swap_id = swap(admin, rule_id=rid, extra_items=1)
    give_stars(settings, admin, 3)
    assert tablet.start(a)["allowed"]
    assert tablet.start(b)["reason"] == "item_limit"

    offered = rewards(admin, device_token)["swaps"]
    assert offered == [
        {
            "id": swap_id,
            "stars": 2,
            "rule_id": rid,
            "extra_items": 1,
            "extra_minutes": 0,
            "used_today": 0,
            "max_per_day": 1,
            "groups": ["Moon"],
        }
    ]
    r = admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token))
    assert r.status_code == 200, r.text
    assert r.json()["stars"] == 1
    assert r.json()["swaps"][0]["used_today"] == 1
    assert tablet.start(b)["allowed"]  # the adjustment for today lets one more file start

    r = admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token))
    assert r.status_code == 409
    assert r.json()["reason"] == "swap_limit_reached"

    body = admin.get("/api/admin/rewards").json()
    assert (body["stars"], body["earned"], body["spent"]) == (1, 3, 2)
    assert [(e["delta"], e["kind"], e["ref_id"]) for e in body["ledger"]] == [(-2, "swap", swap_id)]

    tablet.clock.advance(days=1)  # a new day: the limit resets, but 1 star isn't enough
    c = add_media(settings, audio_file, moon, 1800, "c")
    assert rewards(admin, device_token)["swaps"][0]["used_today"] == 0
    r = admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token))
    assert r.status_code == 409
    assert r.json() == {"reason": "not_enough_stars", "params": {"stars": 1, "needed": 2}}
    assert tablet.start(a)["allowed"]
    assert tablet.start(c)["reason"] == "item_limit"  # yesterday's swap is gone


def test_a_swap_buys_minutes(
    admin: TestClient, tablet: Tablet, device_token: str, settings: Settings, audio_file: Path
) -> None:
    music = group(admin, "music", "Music")
    song = add_media(settings, audio_file, music, 3600)
    rid = rule(admin, all_groups=False, group_ids=[music], max_minutes=10)
    swap_id = swap(admin, rule_id=rid, extra_minutes=15, max_per_day=None)
    give_stars(settings, admin, 4)
    assert tablet.start(song)["remaining_seconds"] == 600
    for _ in range(2):  # no daily limit
        r = admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token))
        assert r.status_code == 200, r.text
    assert tablet.start(song)["remaining_seconds"] == 40 * 60


def test_a_swap_never_opens_a_lockout(
    admin: TestClient, tablet: Tablet, device_token: str, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 1800, "a")
    rid = rule(admin, max_items=0, lockouts=[{"start": "14:00", "end": "07:00"}])
    swap_id = swap(admin, rule_id=rid, extra_items=1)
    give_stars(settings, admin, 2)
    assert admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token)).is_success
    assert tablet.start(a)["reason"] == "lockout"


def test_swaps_of_a_disabled_or_deleted_rule_are_not_offered(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    group(admin, "music", "Music")
    group(admin, "video", "Films")
    rid = rule(admin, max_items=1)  # all groups: the label names them all
    swap_id = swap(admin, rule_id=rid, extra_items=1)
    give_stars(settings, admin, 2)
    assert rewards(admin, device_token)["swaps"][0]["groups"] == ["Music", "Films"]

    assert admin.patch(f"/api/admin/rules/{rid}", json={"enabled": False}).status_code == 200
    assert rewards(admin, device_token)["swaps"] == []
    r = admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token))
    assert r.status_code == 404
    assert r.json()["reason"] == "unknown_swap"
    assert len(admin.get("/api/admin/rewards").json()["swaps"]) == 1  # still listed for the admin

    admin.patch(f"/api/admin/rules/{rid}", json={"enabled": True})
    r = admin.patch(f"/api/admin/rewards/swaps/{swap_id}", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert rewards(admin, device_token)["swaps"] == []

    assert admin.delete(f"/api/admin/rules/{rid}").status_code == 204
    assert admin.get("/api/admin/rewards").json()["swaps"] == []  # cascaded


def test_swap_crud_and_validation(admin: TestClient) -> None:
    rid = rule(admin, max_items=1)
    for bad in (
        {"stars": 2, "rule_id": rid},  # no amount
        {"stars": 2, "rule_id": rid, "extra_items": 1, "extra_minutes": 5},  # both
        {"stars": 0, "rule_id": rid, "extra_items": 1},
        {"stars": 501, "rule_id": rid, "extra_items": 1},
        {"stars": 2, "rule_id": rid, "extra_items": 1, "max_per_day": 0},
    ):
        assert admin.post("/api/admin/rewards/swaps", json=bad).status_code == 422, bad
    r = admin.post("/api/admin/rewards/swaps", json={"stars": 2, "rule_id": 999, "extra_items": 1})
    assert r.status_code == 422
    assert r.json()["reason"] == "unknown_rule"

    swap_id = swap(admin, rule_id=rid, extra_items=1)
    r = admin.patch(f"/api/admin/rewards/swaps/{swap_id}", json={"stars": 5, "max_per_day": None})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "id": swap_id,
        "stars": 5,
        "rule_id": rid,
        "extra_items": 1,
        "extra_minutes": 0,
        "max_per_day": None,
        "enabled": True,
    }
    r = admin.patch(f"/api/admin/rewards/swaps/{swap_id}", json={"extra_minutes": 15})
    assert r.status_code == 422  # would give both
    timed = rule(admin, max_items=1, max_minutes=30)
    r = admin.patch(
        f"/api/admin/rewards/swaps/{swap_id}",
        json={"rule_id": timed, "extra_items": 0, "extra_minutes": 15},
    )
    assert r.json()["extra_minutes"] == 15
    r = admin.patch(f"/api/admin/rewards/swaps/{swap_id}", json={"rule_id": rid})
    assert r.status_code == 422  # minutes, but `rid` has no minute limit
    assert r.json() == {"reason": "swap_rule_unsuitable", "params": {"rule_id": rid}}
    r = admin.patch(f"/api/admin/rewards/swaps/{swap_id}", json={"rule_id": 999})
    assert r.json()["reason"] == "unknown_rule"
    assert admin.patch("/api/admin/rewards/swaps/999", json={}).status_code == 404

    assert admin.delete(f"/api/admin/rewards/swaps/{swap_id}").status_code == 204
    r = admin.delete(f"/api/admin/rewards/swaps/{swap_id}")
    assert r.status_code == 404
    assert r.json()["reason"] == "unknown_swap"


@pytest.mark.parametrize(
    ("rule_kw", "amount"),
    [
        ({"mode": "block"}, {"extra_items": 1}),  # a swap never opens a block
        ({"max_minutes": 30}, {"extra_items": 1}),  # no item limit to raise
        ({"max_items": 2}, {"extra_minutes": 15}),  # no minute limit to raise
    ],
)
def test_a_swap_that_gives_nothing_is_refused(
    admin: TestClient, rule_kw: dict[str, Any], amount: dict[str, int]
) -> None:
    rid = rule(admin, **rule_kw)
    r = admin.post("/api/admin/rewards/swaps", json={"stars": 2, "rule_id": rid, **amount})
    assert r.status_code == 422
    assert r.json() == {"reason": "swap_rule_unsuitable", "params": {"rule_id": rid}}


def test_swaps_are_offered_only_on_their_rules_days(
    admin: TestClient, device_token: str, settings: Settings, clock: FixedClock
) -> None:
    weekdays = rule(admin, days=[0, 1, 2, 3, 4], max_items=1)  # the clock starts on a Saturday
    swap_id = swap(admin, rule_id=weekdays, extra_items=1)
    give_stars(settings, admin, 2)
    assert rewards(admin, device_token)["swaps"] == []
    r = admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token))
    assert r.status_code == 404
    assert r.json()["reason"] == "unknown_swap"
    clock.advance(days=2)  # Monday
    assert [s["id"] for s in rewards(admin, device_token)["swaps"]] == [swap_id]
    assert admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token)).is_success


def test_a_swap_whose_rule_lost_its_limit_is_not_offered(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    rid = rule(admin, max_items=1)
    swap(admin, rule_id=rid, extra_items=1)
    give_stars(settings, admin, 2)
    assert len(rewards(admin, device_token)["swaps"]) == 1
    body = {"name": "r", "days": list(range(7)), "all_groups": True, "max_minutes": 30}
    r = admin.put(f"/api/admin/rules/{rid}", json=body)  # now a minute limit only
    assert r.status_code == 200, r.text
    assert rewards(admin, device_token)["swaps"] == []


# ---- wishes ---------------------------------------------------------------------------------


def test_wish_request_then_approve_or_decline(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    wish_id = wish(admin, stars=3)
    give_stars(settings, admin, 7)
    offered = rewards(admin, device_token)["wishes"]
    assert offered == [
        {
            "id": wish_id,
            "texts": {"de": {"name": "Nachtisch aussuchen"}, "en": {"name": "Choose dessert"}},
            "stars": 3,
            "request": None,
        }
    ]

    r = ask(admin, device_token, wish_id)
    assert r.status_code == 200, r.text
    request = r.json()["wishes"][0]["request"]
    assert request["status"] == "pending"
    assert r.json()["stars"] == 7  # nothing is deducted yet
    r = ask(admin, device_token, wish_id)
    assert r.status_code == 409
    assert r.json()["reason"] == "request_pending"

    state = admin.get("/api/admin/rewards").json()
    assert [(q["id"], q["wish_id"], q["status"]) for q in state["requests"]] == [
        (request["id"], wish_id, "pending")
    ]
    assert state["requests"][0]["decided_at"] is None
    r = admin.post(f"/api/admin/rewards/requests/{request['id']}/approve")
    assert r.status_code == 200, r.text
    assert (r.json()["stars"], r.json()["spent"]) == (4, 3)
    assert r.json()["requests"][0]["status"] == "approved"
    assert r.json()["requests"][0]["decided_at"] is not None
    assert [(e["delta"], e["kind"], e["ref_id"]) for e in r.json()["ledger"]] == [
        (-3, "wish", request["id"])
    ]
    seen = rewards(admin, device_token)
    assert seen["stars"] == 4
    assert seen["wishes"][0]["request"]["status"] == "approved"
    r = admin.post(f"/api/admin/rewards/requests/{request['id']}/decline")
    assert r.status_code == 409
    assert r.json()["reason"] == "request_decided"

    second = ask(admin, device_token, wish_id).json()["wishes"][0]["request"]  # again
    assert second["id"] != request["id"]
    r = admin.post(f"/api/admin/rewards/requests/{second['id']}/decline")
    assert r.status_code == 200, r.text
    assert r.json()["stars"] == 4  # declining costs nothing
    assert rewards(admin, device_token)["wishes"][0]["request"]["status"] == "declined"
    assert admin.post("/api/admin/rewards/requests/999/approve").status_code == 404


def test_approval_needs_enough_stars_by_then(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    dessert = wish(admin, stars=3)
    film = wish(admin, stars=3, texts={"en": {"name": "Film night"}})
    give_stars(settings, admin, 4)
    first = ask(admin, device_token, dessert)
    assert first.status_code == 200, first.text
    second = ask(admin, device_token, film)
    assert second.status_code == 200, second.text
    ids = {w["id"]: w["request"]["id"] for w in second.json()["wishes"]}

    assert admin.post(f"/api/admin/rewards/requests/{ids[dessert]}/approve").is_success
    r = admin.post(f"/api/admin/rewards/requests/{ids[film]}/approve")
    assert r.status_code == 409
    assert r.json() == {"reason": "not_enough_stars", "params": {"stars": 1, "needed": 3}}
    state = admin.get("/api/admin/rewards").json()
    assert {q["id"]: q["status"] for q in state["requests"]}[ids[film]] == "pending"
    assert state["stars"] == 1


def test_a_request_needs_stars_and_an_enabled_wish(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    wish_id = wish(admin, stars=3)
    give_stars(settings, admin, 2)
    r = ask(admin, device_token, wish_id)
    assert r.status_code == 409
    assert r.json()["reason"] == "not_enough_stars"

    r = admin.patch(f"/api/admin/rewards/wishes/{wish_id}", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert rewards(admin, device_token)["wishes"] == []
    r = ask(admin, device_token, wish_id)
    assert r.status_code == 404
    assert r.json()["reason"] == "unknown_wish"
    assert ask(admin, device_token, 999).status_code == 404


def test_wish_crud_and_validation(admin: TestClient, device_token: str, settings: Settings) -> None:
    for bad in (
        {"texts": {}, "stars": 3},
        {"texts": {"fr": {"name": "Dessert"}}, "stars": 3},
        {"texts": {"en": {"name": ""}}, "stars": 3},
        {"texts": {"en": {"name": "x" * 61}}, "stars": 3},
        {"texts": {"en": {"name": "Dessert"}}, "stars": 0},
        {"texts": {"en": {"name": "Dessert"}}, "stars": 501},
    ):
        assert admin.post("/api/admin/rewards/wishes", json=bad).status_code == 422, bad
    wish_id = wish(admin, texts={"el": {"name": "  " + "Γ" * 60 + " "}}, stars=500)
    r = admin.patch(f"/api/admin/rewards/wishes/{wish_id}", json={"stars": 10})
    assert r.json() == {
        "id": wish_id,
        "texts": {"el": {"name": "Γ" * 60}},
        "stars": 10,
        "enabled": True,
    }
    assert admin.patch(f"/api/admin/rewards/wishes/{wish_id}", json={"stars": 0}).status_code == 422
    assert admin.patch("/api/admin/rewards/wishes/999", json={}).status_code == 404

    give_stars(settings, admin, 10)
    request_id = ask(admin, device_token, wish_id).json()["wishes"][0]["request"]["id"]
    admin.post(f"/api/admin/rewards/requests/{request_id}/approve")
    assert admin.delete(f"/api/admin/rewards/wishes/{wish_id}").status_code == 204
    state = admin.get("/api/admin/rewards").json()
    assert (state["wishes"], state["requests"]) == ([], [])  # requests go with the wish
    assert state["spent"] == 10  # stars stay spent
    assert admin.delete(f"/api/admin/rewards/wishes/{wish_id}").status_code == 404


def test_a_request_is_decided_once(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    wish_id = wish(admin, stars=2)
    give_stars(settings, admin, 6)
    request_id = ask(admin, device_token, wish_id).json()["wishes"][0]["request"]["id"]
    assert admin.post(f"/api/admin/rewards/requests/{request_id}/approve").is_success
    for decision in ("approve", "decline"):
        r = admin.post(f"/api/admin/rewards/requests/{request_id}/{decision}")
        assert r.status_code == 409
        assert r.json() == {"reason": "request_decided", "params": {"status": "approved"}}
    state = admin.get("/api/admin/rewards").json()
    assert (state["stars"], state["spent"]) == (4, 2)  # spent once
    assert [(e["delta"], e["ref_id"]) for e in state["ledger"]] == [(-2, request_id)]


def test_a_failed_approval_leaves_the_request_pending(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    wish_id = wish(admin, stars=3)
    give_stars(settings, admin, 3)
    request_id = ask(admin, device_token, wish_id).json()["wishes"][0]["request"]["id"]
    swap_id = swap(admin, rule_id=rule(admin, max_items=1), extra_items=1, stars=1)
    assert admin.post(f"/api/rewards/swaps/{swap_id}", headers=bearer(device_token)).is_success
    r = admin.post(f"/api/admin/rewards/requests/{request_id}/approve")
    assert r.status_code == 409
    assert r.json()["reason"] == "not_enough_stars"
    state = admin.get("/api/admin/rewards").json()
    assert [q["status"] for q in state["requests"]] == ["pending"]  # the decision rolled back
    assert admin.post(f"/api/admin/rewards/requests/{request_id}/decline").is_success


def test_one_pending_request_per_wish_also_in_the_database(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    wish_id = wish(admin, stars=1)
    give_stars(settings, admin, 1)
    assert ask(admin, device_token, wish_id).status_code == 200
    r = ask(admin, device_token, wish_id)  # a second tap
    assert r.status_code == 409
    assert r.json()["reason"] == "request_pending"
    row = {"profile_id": 1, "wish_id": wish_id, "created_at": datetime(2026, 11, 7)}
    with make_engine(settings.db_path).begin() as conn:
        conn.execute(insert(wish_requests).values(status="declined", **row))  # decided: fine
    with pytest.raises(IntegrityError), make_engine(settings.db_path).begin() as conn:
        conn.execute(insert(wish_requests).values(status="pending", **row))


def test_admin_mutations_need_csrf(admin: TestClient) -> None:
    rid = rule(admin, max_items=1)
    swap_id = swap(admin, rule_id=rid, extra_items=1)
    wish_id = wish(admin)
    token = admin.headers.pop("X-CSRF-Token")
    try:
        for r in (
            admin.post(
                "/api/admin/rewards/swaps", json={"stars": 2, "rule_id": rid, "extra_items": 1}
            ),
            admin.patch(f"/api/admin/rewards/swaps/{swap_id}", json={"stars": 3}),
            admin.delete(f"/api/admin/rewards/swaps/{swap_id}"),
            admin.post("/api/admin/rewards/wishes", json={**WISH, "stars": 3}),
            admin.patch(f"/api/admin/rewards/wishes/{wish_id}", json={"stars": 3}),
            admin.delete(f"/api/admin/rewards/wishes/{wish_id}"),
            admin.post("/api/admin/rewards/requests/1/approve"),
            admin.post("/api/admin/rewards/requests/1/decline"),
        ):
            assert r.status_code == 403, r.text
            assert r.json()["reason"] == "csrf_failed"
    finally:
        admin.headers["X-CSRF-Token"] = token
    assert admin.get("/api/admin/rewards").json()["swaps"][0]["stars"] == 2
