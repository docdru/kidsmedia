"""Gems 💎 over HTTP (SPEC §16, §3.4 "Gems …" rows): spending, the library, the admin.

The fixed clock starts on Saturday 2026-11-07 14:20 Europe/Berlin.
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.db import gem_ledger, make_engine
from tests.test_play import Tablet, add_media, group, rule


def gems(admin: TestClient, delta: int, note: str | None = None) -> dict[str, Any]:
    r = admin.post("/api/admin/gems", json={"delta": delta, "note": note})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def balance(admin: TestClient) -> int:
    value: int = admin.get("/api/admin/gems").json()["balance"]
    return value


def spends(settings: Settings) -> list[tuple[int, int | None, int | None, str | None]]:
    """(delta, rule_id, media_id, day) of every spend row, oldest first."""
    with make_engine(settings.db_path).connect() as conn:
        return [
            (r.delta, r.rule_id, r.media_id, r.day)
            for r in conn.execute(
                select(gem_ledger).where(gem_ledger.c.kind == "spend").order_by(gem_ledger.c.id)
            )
        ]


def library(tablet: Tablet) -> dict[str, Any]:
    body: dict[str, Any] = tablet.c.get("/api/library", headers=tablet.h).json()
    return body


def gem_rule(admin: TestClient, group_id: int, cost: int = 2, **kw: Any) -> int:
    """Rule G of §3.4: all days, count_after_seconds=10, replay_percent=120."""
    return rule(
        admin,
        all_groups=False,
        group_ids=[group_id],
        gem_cost=cost,
        count_after_seconds=10,
        replay_percent=120,
        **kw,
    )


def test_gems_are_spent_at_the_count_once_and_buy_one_listen(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path, clock: FixedClock
) -> None:
    """§3.4 "Gems unlock a listen"."""
    cartoons = group(admin, "video", "Cartoons")
    v1, v2, v3 = (add_media(settings, audio_file, cartoons, 600, n) for n in ("v1", "v2", "v3"))
    rid = gem_rule(admin, cartoons)
    gems(admin, 3)

    lib = library(tablet)
    assert lib["gems"] == {"balance": 3, "used": True}
    assert lib["groups"][0]["status"]["gem_cost"] == 2
    assert not any(i["paid_today"] for i in lib["groups"][0]["items"])

    assert tablet.start(v1)["allowed"]  # 3 >= 2
    assert tablet.play(v1, 5)["allowed"]
    assert balance(admin) == 3  # not before the count
    assert tablet.play(v1, 10)["allowed"]  # 15 s: it counts now
    assert balance(admin) == 1
    tablet.play(v1, 15)
    assert spends(settings) == [(-2, rid, v1, "2026-11-07")]  # once

    lib = library(tablet)
    items = {i["id"]: i for i in lib["groups"][0]["items"]}
    assert items[v1]["paid_today"] and items[v1]["allowed"]
    assert not items[v2]["paid_today"] and not items[v2]["allowed"]
    status = lib["groups"][0]["status"]
    assert (status["allowed"], status["reason"], status["params"], status["gem_cost"]) == (
        False,
        "gems",
        {"need": 2, "have": 1},
        2,
    )
    denied = tablet.start(v2)
    assert (denied["allowed"], denied["reason"], denied["params"]) == (
        False,
        "gems",
        {"need": 2, "have": 1},
    )

    # Replays are free within the budget: 12 min of the 10-min video, 30 s played.
    again = tablet.start(v1)
    assert again["allowed"] and again["remaining_seconds"] == 720 - 30
    stop = tablet.play(v1, 690)
    assert (stop["allowed"], stop["reason"]) == (False, "item_limit")
    assert "limit" not in stop["params"]  # a gem rule has no item limit to show (§16.3)
    assert tablet.start(v1)["reason"] == "item_limit"
    assert balance(admin) == 1

    # Tomorrow the first video costs again.
    clock.advance(days=1)
    assert tablet.start(v1)["params"] == {"need": 2, "have": 1}
    gems(admin, 1)
    assert tablet.start(v1)["allowed"]
    tablet.play(v1, 15)
    assert balance(admin) == 0
    assert spends(settings)[-1] == (-2, rid, v1, "2026-11-08")
    assert tablet.start(v3)["reason"] == "gems"


def test_a_tap_spends_nothing(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    cartoons = group(admin, "video", "Cartoons")
    v1, v2 = (add_media(settings, audio_file, cartoons, 600, n) for n in ("v1", "v2"))
    gem_rule(admin, cartoons)
    gems(admin, 2)
    tablet.start(v1)
    tablet.play(v1, 5)
    assert tablet.start(v2)["allowed"]  # v1 was only tapped: the gems are still there
    tablet.play(v2, 15)
    assert balance(admin) == 0 and len(spends(settings)) == 1


def test_two_gem_rules_add_up(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    """§3.4 "Gems: two rules on one group": a new video needs 2 + 1; both spend at the count."""
    cartoons = group(admin, "video", "Cartoons")
    v1 = add_media(settings, audio_file, cartoons, 600, "v1")
    g = gem_rule(admin, cartoons, 2)
    h = gem_rule(admin, cartoons, 1)
    gems(admin, 2)
    assert library(tablet)["groups"][0]["status"]["gem_cost"] == 3
    assert tablet.start(v1)["params"] == {"need": 3, "have": 2}
    gems(admin, 1)
    assert tablet.start(v1)["allowed"]
    assert tablet.play(v1, 15)["allowed"]
    assert sorted(spends(settings)) == sorted(
        [(-2, g, v1, "2026-11-07"), (-1, h, v1, "2026-11-07")]
    )
    assert balance(admin) == 0
    item = library(tablet)["groups"][0]["items"][0]
    assert item["paid_today"] and item["allowed"]


def test_gems_taken_away_while_playing(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    """§3.4 "Gems taken away while playing": denied before the count, not after it."""
    cartoons = group(admin, "video", "Cartoons")
    v1 = add_media(settings, audio_file, cartoons, 600, "v1")
    gem_rule(admin, cartoons, grace_minutes=10)
    gems(admin, 2)
    assert tablet.start(v1)["allowed"]
    tablet.play(v1, 5)
    gems(admin, -1)
    stop = tablet.play(v1, 10, position_ms=595_000)  # grace would cover the last 5 s
    assert (stop["allowed"], stop["reason"], stop["params"]) == (
        False,
        "gems",
        {"need": 2, "have": 1},
    )
    assert not stop["grace"]["active"]  # gems aren't graceable
    assert spends(settings) == []

    gems(admin, 1)
    assert tablet.start(v1)["allowed"]
    assert tablet.play(v1, 15)["allowed"]  # counted (and paid) now
    assert balance(admin) == 0
    gems(admin, 1)
    gems(admin, -1)
    assert tablet.play(v1, 15)["allowed"]  # removing gems doesn't stop a paid listen


def test_the_stream_does_not_check_gems(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    """§16.2: like the item limit, because ExoPlayer preloads the next queue item."""
    cartoons = group(admin, "video", "Cartoons")
    v1 = add_media(settings, audio_file, cartoons, 600, "v1")
    gem_rule(admin, cartoons)
    assert tablet.start(v1)["reason"] == "gems"
    assert tablet.stream_status(v1) == 206


def test_the_offline_batch_books_spends_down_to_zero(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    cartoons = group(admin, "video", "Cartoons")
    v1, v2 = (add_media(settings, audio_file, cartoons, 600, n) for n in ("v1", "v2"))
    rid = gem_rule(admin, cartoons)
    gems(admin, 1)  # 2 when the tablet went offline; the parent removed one meanwhile
    events = [
        {
            "event_id": "g-0001-aaaa",
            "media_id": v1,
            "started_at": "2026-11-07T09:00:00+01:00",
            "seconds": 60,
        },
        {
            "event_id": "g-0002-bbbb",
            "media_id": v2,
            "started_at": "2026-11-07T09:10:00+01:00",
            "seconds": 5,  # a tap: nothing to pay
        },
    ]
    for _ in range(2):  # a retried batch books nothing twice
        tablet.c.post("/api/play/batch", headers=tablet.h, json={"events": events})
    assert spends(settings) == [(-1, rid, v1, "2026-11-07")]
    assert balance(admin) == 0
    more = [
        {
            "event_id": "g-0003-cccc",
            "media_id": v2,
            "started_at": "2026-11-07T09:20:00+01:00",
            "seconds": 30,
        }
    ]
    tablet.c.post("/api/play/batch", headers=tablet.h, json={"events": more})
    assert spends(settings)[-1] == (0, rid, v2, "2026-11-07")  # paid with what was left
    assert balance(admin) == 0
    items = {i["id"]: i for i in library(tablet)["groups"][0]["items"]}
    assert items[v1]["paid_today"] and items[v2]["paid_today"]
    assert tablet.start(v1)["allowed"]  # the offline listen goes on online


def test_admin_adds_and_removes_gems(admin: TestClient, clock: FixedClock) -> None:
    empty = admin.get("/api/admin/gems").json()
    assert empty == {"balance": 0, "entries": []}
    gems(admin, 2, " Room tidied ")
    clock.advance(minutes=1)
    state = gems(admin, -1)
    assert state["balance"] == 1
    assert [(e["delta"], e["kind"], e["note"], e["title"]) for e in state["entries"]] == [
        (-1, "admin", None, None),
        (2, "admin", "Room tidied", None),
    ]
    r = admin.post("/api/admin/gems", json={"delta": -2})
    assert r.status_code == 422
    assert r.json() == {"reason": "not_enough_gems", "params": {"have": 1, "need": 2}}
    for bad in (0, 21, -21):
        assert admin.post("/api/admin/gems", json={"delta": bad}).status_code == 422
    assert admin.post("/api/admin/gems", json={"delta": 1, "note": "x" * 141}).status_code == 422
    no_csrf = admin.post("/api/admin/gems", json={"delta": 1}, headers={"X-CSRF-Token": ""})
    assert no_csrf.status_code == 403
    assert len(admin.get("/api/admin/gems?limit=1").json()["entries"]) == 1
    assert admin.get("/api/admin/gems?limit=0").status_code == 422
    assert balance(admin) == 1


def test_gems_need_an_admin_session(client: TestClient) -> None:
    assert client.get("/api/admin/gems").status_code == 401
    assert client.post("/api/admin/gems", json={"delta": 1}).status_code in (401, 403)


def test_library_without_gem_rules(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    music = group(admin, "music", "Music")
    add_media(settings, audio_file, music, 200, "song")
    rule(admin, max_minutes=60)
    gems(admin, 4)
    lib = library(tablet)
    assert lib["gems"] == {"balance": 4, "used": False}
    assert lib["groups"][0]["status"]["gem_cost"] is None
    assert lib["groups"][0]["items"][0]["paid_today"] is False
    disabled = gem_rule(admin, music)
    admin.patch(f"/api/admin/rules/{disabled}", json={"enabled": False})
    assert library(tablet)["gems"]["used"] is False


def test_rule_gem_cost_is_validated_and_round_trips(admin: TestClient) -> None:
    base = {"name": "r", "days": [0], "all_groups": True}
    created = admin.post("/api/admin/rules", json=base)
    assert created.status_code == 201 and created.json()["gem_cost"] is None
    rid = created.json()["id"]
    for bad in (0, 21):
        assert admin.post("/api/admin/rules", json={**base, "gem_cost": bad}).status_code == 422
    put = admin.put(f"/api/admin/rules/{rid}", json={**base, "gem_cost": 20})
    assert put.status_code == 200 and put.json()["gem_cost"] == 20
    assert admin.get("/api/admin/rules").json()[0]["gem_cost"] == 20


def test_snapshot_carries_gem_rules_balance_and_paid_listens(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    """§16.2 Offline: the app's engine needs the gem costs, the balance and what is paid."""
    cartoons = group(admin, "video", "Cartoons")
    v1 = add_media(settings, audio_file, cartoons, 600, "v1")
    rid = gem_rule(admin, cartoons, 3)
    gems(admin, 5)
    tablet.start(v1)
    tablet.play(v1, 15)
    snap = tablet.c.get("/api/offline/snapshot", headers=tablet.h).json()
    assert snap["rules"][0]["gem_cost"] == 3
    assert snap["usage"]["gem_balance"] == 2
    assert snap["usage"]["gems_paid"] == {str(rid): [v1]}


def test_usage_view_and_preview_show_gems(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    """§16.4: the Activity page lists spends with the file; §16.3: the preview shows gems."""
    cartoons = group(admin, "video", "Cartoons")
    v1 = add_media(settings, audio_file, cartoons, 600, "v1")
    gem_rule(admin, cartoons, 2)
    gem_rule(admin, cartoons, 1)
    gems(admin, 3)
    tablet.start(v1)
    tablet.play(v1, 15)
    plays_ = admin.get("/api/admin/usage").json()["plays"]
    assert [(p["media_id"], p["gems_spent"]) for p in plays_] == [(v1, 3)]
    entries = admin.get("/api/admin/gems").json()["entries"]
    assert {(e["delta"], e["kind"], e["title"]) for e in entries[:2]} == {
        (-2, "spend", "v1"),
        (-1, "spend", "v1"),
    }
    status = admin.get("/api/admin/preview").json()[0]["status"]
    assert (status["allowed"], status["gem_cost"], status["params"]) == (
        False,
        3,
        {"need": 3, "have": 0},
    )
