"""Done activities in the admin (SPEC §17): the list with photos and stars, one bonus star per
done task, and "open again", which takes the task's stars back (never below 0).

The fixed clock starts on Saturday 2026-11-07 14:20 Europe/Berlin; a queued world's first
chapter (draw "One", calc "T", write "T", read "R") is unlocked.
"""

from typing import Any

from fastapi.testclient import TestClient

from kidsmedia.config import Settings
from tests.test_activities_api import import_json, pack, queued_world
from tests.test_photos import done_rows, jpeg, ledger, photo_files, photo_rows, upload
from tests.test_play import bearer
from tests.test_rewards_api import ask, rewards, wish

DONE = "/api/admin/activities/done"


def done(c: TestClient, token: str, world_id: int, kind: str, attempts: int | None = None) -> Any:
    body: dict[str, Any] = {"world_id": world_id, "position": 1, "kind": kind}
    if attempts is not None:
        body["attempts"] = attempts
    r = c.post("/api/activities/done", json=body, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


def entries(admin: TestClient, **params: Any) -> list[dict[str, Any]]:
    r = admin.get(DONE, params=params)
    assert r.status_code == 200, r.text
    listed: list[dict[str, Any]] = r.json()["entries"]
    return listed


def entry(admin: TestClient, kind: str) -> dict[str, Any]:
    return next(e for e in entries(admin) if e["kind"] == kind)


def done_kinds(admin: TestClient, token: str) -> list[str]:
    body = admin.get("/api/activities", headers=bearer(token)).json()
    kinds: list[str] = body["world"]["chapters"][0]["done_kinds"]
    return sorted(kinds)


def admin_stars(admin: TestClient) -> tuple[int, int, int]:
    body = admin.get("/api/admin/rewards").json()
    return body["stars"], body["earned"], body["spent"]


# ---- the list -----------------------------------------------------------------------------


def test_the_list_shows_done_tasks_with_photos_and_stars(
    admin: TestClient, device_token: str
) -> None:
    world_id = queued_world(admin)
    assert upload(admin, device_token, world_id).status_code == 200  # draw, with a photo
    done(admin, device_token, world_id, "read", attempts=1)
    done(admin, device_token, world_id, "calc", attempts=3)
    done(admin, device_token, world_id, "write")  # an older app: no picture

    listed = entries(admin)
    assert [e["kind"] for e in listed] == ["write", "calc", "read", "draw"]  # newest first
    draw = listed[-1]
    assert set(draw) == {
        "id",
        "world_id",
        "world_name",
        "position",
        "kind",
        "task_title",
        "done_at",
        "attempts",
        "stars",
        "bonus",
        "photos",
    }
    assert (draw["world_id"], draw["world_name"], draw["position"]) == (world_id, "World", 1)
    assert (draw["task_title"], draw["attempts"], draw["stars"], draw["bonus"]) == (
        "One",
        None,
        1,
        False,
    )
    [photo] = draw["photos"]
    assert set(photo) == {"id", "url_sm", "url_lg", "taken_at"}
    assert photo["url_sm"].startswith("/api/admin/photos/p")
    assert photo["url_sm"].endswith(".webp?size=sm")
    assert photo["url_lg"].endswith(".webp?size=lg")
    assert admin.get(photo["url_lg"]).status_code == 200
    by_kind = {e["kind"]: e for e in listed}
    assert (by_kind["read"]["task_title"], by_kind["read"]["attempts"]) == ("R", 1)
    assert (by_kind["read"]["stars"], by_kind["calc"]["stars"]) == (2, 1)  # first-try extra
    assert by_kind["calc"]["attempts"] == 3
    assert by_kind["write"]["photos"] == []  # "no picture"

    first = admin.get(DONE, params={"limit": 3}).json()
    assert [e["kind"] for e in first["entries"]] == ["write", "calc", "read"]
    assert first["next_before_id"] == first["entries"][-1]["id"]
    rest = admin.get(DONE, params={"before_id": first["next_before_id"]}).json()
    assert [e["id"] for e in rest["entries"]] == [draw["id"]]
    assert rest["next_before_id"] is None
    for bad in ({"limit": 0}, {"limit": 101}, {"before_id": 0}):
        assert admin.get(DONE, params=bad).status_code == 422, bad


def test_names_are_in_the_story_language(admin: TestClient, device_token: str) -> None:
    body = pack()
    body["chapters"][0]["tasks"][0]["texts"]["de"]["title"] = "Male"
    world_id = import_json(admin, body).json()["world_id"]
    admin.put(
        "/api/admin/activities/queue", json={"world_ids": [world_id], "langs": {world_id: "de"}}
    )
    done(admin, device_token, world_id, "draw")
    [e] = entries(admin)
    assert (e["world_name"], e["task_title"]) == ("Welt", "Male")


def test_the_list_needs_an_admin(client: TestClient) -> None:
    assert client.get(DONE).status_code == 401


# ---- bonus star ---------------------------------------------------------------------------


def test_one_bonus_star_per_done_task_of_any_kind(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    done(admin, device_token, world_id, "read", attempts=1)
    read = entry(admin, "read")
    r = admin.post(f"{DONE}/{read['id']}/bonus")
    assert r.status_code == 200, r.text
    assert r.json() == read | {"stars": 3, "bonus": True}
    assert ledger(settings)[-1] == ("bonus", 1, read["id"])
    assert admin_stars(admin) == (3, 3, 0)  # a bonus counts as earned
    assert rewards(admin, device_token)["stars"] == 3
    assert admin.get("/api/admin/rewards").json()["ledger"] == []  # spending rows only

    r = admin.post(f"{DONE}/{read['id']}/bonus")
    assert r.status_code == 409
    assert r.json()["reason"] == "bonus_given"
    r = admin.post(f"{DONE}/999/bonus")
    assert r.status_code == 404
    assert r.json()["reason"] == "unknown_done"
    assert admin_stars(admin) == (3, 3, 0)
    assert entry(admin, "read")["stars"] == 3


# ---- open again ---------------------------------------------------------------------------


def test_open_again_takes_the_stars_back_and_the_task_can_earn_again(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    done(admin, device_token, world_id, "draw")
    done(admin, device_token, world_id, "read", attempts=1)
    read_id = entry(admin, "read")["id"]
    assert admin_stars(admin) == (3, 3, 0)

    assert admin.delete(f"{DONE}/{read_id}").status_code == 204
    assert ledger(settings)[-1] == ("reopen", -2, read_id)
    assert admin_stars(admin) == (1, 1, 0)  # negative earning, not spending
    assert admin.get("/api/admin/rewards").json()["ledger"] == []
    assert [e["kind"] for e in entries(admin)] == ["draw"]
    assert done_kinds(admin, device_token) == ["draw"]  # the tablet sees it open again

    done(admin, device_token, world_id, "read", attempts=1)  # done anew: earns again
    again = entry(admin, "read")
    assert again["id"] != read_id  # a reopened id is never handed out again
    assert (again["stars"], again["bonus"]) == (2, False)
    assert admin_stars(admin) == (3, 3, 0)
    assert done_kinds(admin, device_token) == ["draw", "read"]


def test_open_again_takes_back_the_bonus_star_too(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    done(admin, device_token, world_id, "read", attempts=1)
    read_id = entry(admin, "read")["id"]
    admin.post(f"{DONE}/{read_id}/bonus")
    assert admin.delete(f"{DONE}/{read_id}").status_code == 204
    assert ledger(settings)[-1] == ("reopen", -3, read_id)
    assert admin_stars(admin) == (0, 0, 0)
    done(admin, device_token, world_id, "read", attempts=2)
    again = entry(admin, "read")
    assert (again["stars"], again["bonus"]) == (1, False)  # the old bonus stays with the old row
    assert admin.post(f"{DONE}/{again['id']}/bonus").status_code == 200


def test_open_again_never_takes_the_balance_below_zero(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    done(admin, device_token, world_id, "draw")
    done(admin, device_token, world_id, "read", attempts=1)
    wish_id = wish(admin, stars=2)
    request_id = ask(admin, device_token, wish_id).json()["wishes"][0]["request"]["id"]
    assert admin.post(f"/api/admin/rewards/requests/{request_id}/approve").is_success
    assert admin_stars(admin) == (1, 3, 2)

    read_id = entry(admin, "read")["id"]
    assert admin.delete(f"{DONE}/{read_id}").status_code == 204
    assert ledger(settings)[-1] == ("reopen", -1, read_id)  # only the balance
    assert admin_stars(admin) == (0, 2, 2)  # the swapped stars stay swapped
    assert rewards(admin, device_token)["stars"] == 0

    draw_id = entry(admin, "draw")["id"]
    n = len(ledger(settings))
    assert admin.delete(f"{DONE}/{draw_id}").status_code == 204
    assert len(ledger(settings)) == n  # nothing to take back: no row
    assert admin_stars(admin) == (0, 2, 2)


def test_open_again_deletes_the_photos(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    world_id = queued_world(admin)
    assert upload(admin, device_token, world_id).status_code == 200
    assert upload(admin, device_token, world_id, jpeg((300, 200), orientation=1)).status_code == 200
    assert upload(admin, device_token, world_id, kind="write").status_code == 200
    draw = entry(admin, "draw")
    assert len(draw["photos"]) == 2
    assert len(photo_files(settings)) == 6
    write_key = next(r.key for r in photo_rows(settings) if r.kind == "write")

    assert admin.delete(f"{DONE}/{draw['id']}").status_code == 204
    assert [r.kind for r in photo_rows(settings)] == ["write"]
    assert photo_files(settings) == {f"{write_key}-lg.webp", f"{write_key}-sm.webp"}
    assert admin.get(draw["photos"][0]["url_lg"]).status_code == 404
    assert [r.kind for r in done_rows(settings)] == ["write"]
    assert done_kinds(admin, device_token) == ["write"]
    assert len(rewards(admin, device_token)["album"]) == 1  # the write task keeps the sticker

    write_id = entry(admin, "write")["id"]
    assert admin.delete(f"{DONE}/{write_id}").status_code == 204
    assert photo_rows(settings) == []
    assert photo_files(settings) == set()
    assert done_kinds(admin, device_token) == []
    assert rewards(admin, device_token)["album"] == []  # the chapter's last done task went


def test_open_again_of_an_unknown_task(admin: TestClient, device_token: str) -> None:
    r = admin.delete(f"{DONE}/999")
    assert r.status_code == 404
    assert r.json()["reason"] == "unknown_done"
    world_id = queued_world(admin)
    done(admin, device_token, world_id, "draw")
    done_id = entry(admin, "draw")["id"]
    assert admin.delete(f"{DONE}/{done_id}").status_code == 204
    r = admin.delete(f"{DONE}/{done_id}")
    assert r.status_code == 404
    assert r.json()["reason"] == "unknown_done"


def test_mutations_need_csrf(admin: TestClient, device_token: str, settings: Settings) -> None:
    world_id = queued_world(admin)
    done(admin, device_token, world_id, "draw")
    done_id = entry(admin, "draw")["id"]
    token = admin.headers.pop("X-CSRF-Token")
    try:
        assert admin.post(f"{DONE}/{done_id}/bonus").status_code == 403
        assert admin.delete(f"{DONE}/{done_id}").status_code == 403
    finally:
        admin.headers["X-CSRF-Token"] = token
    assert len(done_rows(settings)) == 1
    assert [kind for kind, _, _ in ledger(settings)] == ["earn"]
