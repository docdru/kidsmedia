"""Playback, counting, grace and enforcement over HTTP (SPEC §3.3, §3.3a, §3.4, §5.3).

The fixed clock starts on Saturday 2026-11-07 14:20 Europe/Berlin.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from kidsmedia.clock import FixedClock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import insert_id, make_engine, media, play_sessions, plays, positions
from tests import media_fixtures as fx
from tests.conftest import BERLIN


@pytest.fixture(scope="module")
def audio_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return fx.mp3(tmp_path_factory.mktemp("play") / "a.mp3")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def group(admin: TestClient, kind: str, name: str) -> int:
    gid: int = admin.post("/api/admin/groups", json={"name": name, "kind": kind}).json()["id"]
    return gid


def add_media(
    settings: Settings, audio_file: Path, group_id: int, duration_s: float, title: str = "x"
) -> int:
    """Insert a media row directly (any duration), backed by a real small file for streaming."""
    rel = f"{group_id}/{title}.mp3"
    dest = settings.media_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio_file.read_bytes())
    with make_engine(settings.db_path).begin() as conn:
        return insert_id(
            conn.execute(
                insert(media).values(
                    group_id=group_id,
                    title=title,
                    path=rel,
                    mime="audio/mpeg",
                    size=dest.stat().st_size,
                    duration_s=duration_s,
                    created_at=to_db(datetime(2026, 11, 1)),
                    thumb_source="none",
                )
            )
        )


def rule(admin: TestClient, **kw: Any) -> int:
    """Create a rule. Files count at start and replays are unlimited unless a test sets
    count_after_seconds or replay_percent."""
    body = {
        "name": "r",
        "days": list(range(7)),
        "all_groups": True,
        "count_after_seconds": 0,
        "replay_percent": None,
        **kw,
    }
    r = admin.post("/api/admin/rules", json=body)
    assert r.status_code == 201, r.text
    rid: int = r.json()["id"]
    return rid


class Tablet:
    def __init__(self, client: TestClient, token: str, clock: FixedClock) -> None:
        self.c, self.h, self.clock = client, bearer(token), clock
        self.session: str | None = None
        self.total = 0.0

    def start(self, media_id: int) -> dict[str, Any]:
        r = self.c.post("/api/play/start", json={"media_id": media_id}, headers=self.h)
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        if body["allowed"]:
            self.session, self.total = body["play_session_id"], 0.0
        return body

    def play(self, media_id: int, seconds: float, position_ms: int = 0) -> dict[str, Any]:
        """Advance the clock by `seconds` of playing and send a heartbeat."""
        self.clock.advance(seconds=seconds)
        self.total += seconds
        return self.beat(media_id, self.total, position_ms)

    def beat(self, media_id: int, total: float, position_ms: int = 0) -> dict[str, Any]:
        r = self.c.post(
            "/api/play/heartbeat",
            json={
                "media_id": media_id,
                "play_session_id": self.session,
                "played_seconds_total": total,
                "position_ms": position_ms,
            },
            headers=self.h,
        )
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        return body

    def stream_status(self, media_id: int) -> int:
        r = self.c.get(f"/api/media/{media_id}/stream", headers={**self.h, "Range": "bytes=0-9"})
        status: int = r.status_code
        return status


@pytest.fixture
def tablet(admin: TestClient, device_token: str, clock: FixedClock) -> Tablet:
    return Tablet(admin, device_token, clock)


def seconds_today(settings: Settings, media_id: int) -> float:
    with make_engine(settings.db_path).connect() as conn:
        return float(
            sum(conn.execute(select(plays.c.seconds).where(plays.c.media_id == media_id)).scalars())
        )


def play_days(settings: Settings, media_id: int) -> dict[str, float]:
    with make_engine(settings.db_path).connect() as conn:
        return {
            r.day: r.seconds
            for r in conn.execute(
                select(plays.c.day, plays.c.seconds).where(plays.c.media_id == media_id)
            )
        }


# ---- items -------------------------------------------------------------------------------


def test_one_story_a_day_and_replays_are_free(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon, fox = group(admin, "audiobook", "Moon"), group(admin, "audiobook", "Fox")
    a = add_media(settings, audio_file, moon, 1800, "a")
    b = add_media(settings, audio_file, fox, 1800, "b")
    rule(admin, all_groups=False, group_ids=[moon, fox], days=[5, 6], max_items=1, grace_minutes=10)
    rule(admin, all_groups=False, group_ids=[moon, fox], days=[0, 1, 2, 3, 4], mode="block")

    assert tablet.start(a)["allowed"]
    denied = tablet.start(b)
    assert (denied["allowed"], denied["reason"], denied["params"]) == (
        False,
        "item_limit",
        {"limit": 1},
    )
    assert tablet.start(a)["allowed"]  # replay
    lib = admin.get("/api/library", headers=tablet.h).json()
    statuses = {g["name"]: g["status"] for g in lib["groups"]}
    assert statuses["Fox"]["reason"] == "item_limit" and statuses["Fox"]["items_left"] == 0
    items = {i["id"]: i for g in lib["groups"] for i in g["items"]}
    assert items[a]["allowed"] and items[a]["played_today"] and not items[b]["allowed"]
    # §3.4 "Grace doesn't start the next file": grace_minutes doesn't help a new file
    assert not tablet.start(b)["allowed"]


def test_blocked_today_reports_next_day(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path, clock: FixedClock
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 60)
    rule(admin, all_groups=False, group_ids=[moon], days=[0, 1, 2, 3, 4], mode="block")
    clock.set(datetime(2026, 11, 10, 9, 0, tzinfo=BERLIN))  # Tuesday
    r = tablet.start(a)
    assert (r["reason"], r["params"]) == ("blocked_today", {"next_day": 5})
    assert tablet.stream_status(a) == 403


# ---- counting (§3.3) -----------------------------------------------------------------------


def test_cumulative_heartbeats_offline_catch_up_and_replay(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    music = group(admin, "music", "Music")
    m = add_media(settings, audio_file, music, 3600)
    tablet.start(m)
    tablet.play(m, 15)
    assert seconds_today(settings, m) == pytest.approx(15)
    # §3.4 "Replayed heartbeat": a lower total books nothing and changes nothing
    tablet.clock.advance(seconds=15)
    tablet.beat(m, 5)
    tablet.total += 15
    tablet.beat(m, tablet.total)  # 30: books exactly 15 more (the cap window wasn't shrunk)
    assert seconds_today(settings, m) == pytest.approx(30)
    # §3.4 "Offline catch-up": 4 min without heartbeats, then the total jumps by 240
    tablet.play(m, 240)
    assert seconds_today(settings, m) == pytest.approx(270)
    # a buggy total far ahead is capped at elapsed + 5 s
    tablet.clock.advance(seconds=15)
    tablet.beat(m, tablet.total + 1000)
    assert seconds_today(settings, m) == pytest.approx(270 + 20)


def test_unknown_or_pruned_session_is_404(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path, clock: FixedClock
) -> None:
    music = group(admin, "music", "Music")
    m = add_media(settings, audio_file, music, 600)
    r = admin.post(
        "/api/play/heartbeat",
        headers=tablet.h,
        json={
            "media_id": m,
            "play_session_id": "nope",
            "played_seconds_total": 1,
            "position_ms": 0,
        },
    )
    assert r.status_code == 404 and r.json()["reason"] == "unknown_session"
    tablet.start(m)
    old = tablet.session
    clock.advance(days=8)
    tablet.start(m)  # a later start prunes sessions older than 7 days
    with make_engine(settings.db_path).connect() as conn:
        assert (
            conn.execute(select(play_sessions.c.id).where(play_sessions.c.id == old)).first()
            is None
        )
    r = admin.post(
        "/api/play/heartbeat",
        headers=tablet.h,
        json={"media_id": m, "play_session_id": old, "played_seconds_total": 99, "position_ms": 0},
    )
    assert r.status_code == 404


def test_minute_limit_stops_playback_and_streaming(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    music = group(admin, "music", "Music")
    m = add_media(settings, audio_file, music, 3600)
    rule(admin, all_groups=False, group_ids=[music], max_minutes=1)
    assert tablet.start(m)["remaining_seconds"] == 60
    assert tablet.play(m, 30)["remaining_seconds"] == 30
    stop = tablet.play(m, 30)
    assert (stop["allowed"], stop["reason"], stop["params"]) == (
        False,
        "minute_limit",
        {"limit": 1},
    )
    assert tablet.stream_status(m) == 403
    # an admin preview isn't rule-checked
    assert admin.get(f"/api/media/{m}/stream", headers={"Range": "bytes=0-9"}).status_code == 206


def test_a_session_belongs_to_the_day_it_started(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path, clock: FixedClock
) -> None:
    music = group(admin, "music", "Music")
    m = add_media(settings, audio_file, music, 3600)
    clock.set(datetime(2026, 11, 7, 23, 59, 50, tzinfo=BERLIN))
    tablet.start(m)
    assert tablet.play(m, 20)["allowed"]  # now Sunday 00:00:10
    # §3.3: the whole session, catch-up included, is booked to the day it started.
    assert play_days(settings, m) == {"2026-11-07": pytest.approx(20)}


def test_one_story_running_past_midnight_doesnt_use_up_tomorrows(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path, clock: FixedClock
) -> None:
    """§3.4 "A story running past midnight"."""
    moon = group(admin, "audiobook", "Moon")
    ep1 = add_media(settings, audio_file, moon, 3600, "ep1")
    ep2 = add_media(settings, audio_file, moon, 3600, "ep2")
    rule(admin, max_items=1)
    clock.set(datetime(2026, 11, 6, 23, 30, tzinfo=BERLIN))  # Friday
    assert tablet.start(ep1)["allowed"]
    assert tablet.play(ep1, 2400, position_ms=2400_000)["allowed"]  # Saturday 00:10
    assert play_days(settings, ep1) == {"2026-11-06": pytest.approx(2400)}
    assert tablet.start(ep2)["allowed"]  # Saturday's story is still free
    assert play_days(settings, ep2) == {"2026-11-07": 0}


def test_a_playing_video_keeps_its_days_minute_limit(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path, clock: FixedClock
) -> None:
    """§3.4 "Videos 30 min on weekdays, 60 at weekends", across midnight."""
    cartoons = group(admin, "video", "Cartoons")
    v1 = add_media(settings, audio_file, cartoons, 3600, "v1")
    v2 = add_media(settings, audio_file, cartoons, 3600, "v2")
    scope = {"all_groups": False, "group_ids": [cartoons]}
    rule(admin, days=[0, 1, 2, 3, 4], max_minutes=30, **scope)
    rule(admin, days=[5, 6], max_minutes=60, **scope)
    clock.set(datetime(2026, 11, 6, 23, 40, tzinfo=BERLIN))  # Friday
    assert tablet.start(v1)["remaining_seconds"] == 1800
    tablet.play(v1, 900)  # 23:55
    after = tablet.play(v1, 600)  # Saturday 00:05: Friday's limit still applies
    assert after["allowed"] and after["remaining_seconds"] == 300
    stop = tablet.play(v1, 300)  # 00:10: Friday's 30 min are used up
    assert (stop["allowed"], stop["reason"], stop["params"]) == (
        False,
        "minute_limit",
        {"limit": 30},
    )
    assert tablet.stream_status(v1) == 403  # the stream follows the session's day too
    assert tablet.start(v2)["remaining_seconds"] == 3600  # a new Saturday start: 60 min
    assert tablet.stream_status(v2) == 206


# ---- grace (§3.3a, §3.4) -------------------------------------------------------------------


def bedtime(admin: TestClient, grace: int) -> None:
    rule(admin, lockouts=[{"start": "14:30", "end": "07:00"}], grace_minutes=grace)


def test_episode_finishes_at_bedtime_and_seeking_still_streams(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    ep = add_media(settings, audio_file, moon, 1800)
    bedtime(admin, grace=10)
    assert tablet.start(ep)["remaining_seconds"] == 600  # 14:20 -> 14:30
    beat = tablet.play(ep, 600, position_ms=1320_000)  # 14:30, 8 min left
    assert (
        beat["allowed"]
        and beat["grace"]["active"]
        and beat["grace"]["until_position_ms"] == 1800_000
    )
    assert beat["remaining_seconds"] == 480
    tablet.clock.advance(minutes=3)  # 14:33: a seek re-opens the stream
    assert tablet.stream_status(ep) == 206
    done = tablet.play(ep, 300, position_ms=1799_000)  # the episode ends
    assert (
        not done["allowed"] and done["reason"] == "lockout" and done["params"] == {"until": "07:00"}
    )
    assert tablet.stream_status(ep) == 403


def test_no_grace_when_too_much_is_left(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    ep = add_media(settings, audio_file, moon, 3600)
    bedtime(admin, grace=10)
    tablet.start(ep)
    beat = tablet.play(ep, 600, position_ms=2100_000)  # 25 min left
    assert not beat["allowed"] and beat["reason"] == "lockout"


def test_grace_expires_and_seeking_back_ends_it(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    ep = add_media(settings, audio_file, moon, 1800)
    bedtime(admin, grace=10)
    tablet.start(ep)
    assert tablet.play(ep, 600, position_ms=1320_000)["grace"]["active"]
    back = tablet.play(ep, 10, position_ms=1000_000)  # seek back before the grace started
    assert not back["allowed"]
    assert tablet.stream_status(ep) == 403
    assert tablet.start(ep)["reason"] == "lockout"  # grace never (re)starts a file


def test_grace_is_granted_once_per_file_per_day(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    ep = add_media(settings, audio_file, moon, 1800)
    bedtime(admin, grace=10)
    tablet.start(ep)
    assert tablet.play(ep, 600, position_ms=1320_000)["grace"]["active"]  # 8 min left
    assert not tablet.play(ep, 10, position_ms=1020_000)["allowed"]  # seek back ends it
    again = tablet.play(ep, 10, position_ms=1320_000)  # 8 min left again
    assert not again["allowed"] and not again["grace"]["active"]


def test_grace_wall_clock_cap(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    ep = add_media(settings, audio_file, moon, 1800)
    bedtime(admin, grace=10)
    tablet.start(ep)
    assert tablet.play(ep, 600, position_ms=1320_000)["grace"]["active"]
    tablet.clock.advance(minutes=20)  # paused for 20 min = 2 x grace_minutes
    assert tablet.stream_status(ep) == 403


@pytest.mark.parametrize("grace", [0, 10])
def test_midnight_starting_lockout_with_and_without_grace(
    admin: TestClient,
    tablet: Tablet,
    settings: Settings,
    audio_file: Path,
    clock: FixedClock,
    grace: int,
) -> None:
    """§3.4 "Midnight-starting lockout gets a countdown", over HTTP."""
    music = group(admin, "music", "Music")
    song = add_media(settings, audio_file, music, 900)
    rule(admin, days=[5], lockouts=[{"start": "00:00", "end": "06:00"}], grace_minutes=grace)
    clock.set(datetime(2026, 11, 6, 23, 50, tzinfo=BERLIN))  # Friday
    assert tablet.start(song)["remaining_seconds"] == 600
    beat = tablet.play(song, 600, position_ms=600_000)  # Saturday 00:00, 5 min left
    # The last seconds before midnight are booked to Friday, and finishing Friday's song
    # doesn't count as a Saturday item.
    assert play_days(settings, song) == {"2026-11-06": pytest.approx(600)}
    if grace == 0:
        assert not beat["allowed"] and beat["reason"] == "lockout"
        assert beat["params"] == {"until": "06:00"}
        assert tablet.stream_status(song) == 403
        return
    assert beat["allowed"] and beat["grace"]["active"] and beat["remaining_seconds"] == 300
    assert tablet.stream_status(song) == 206
    tablet.play(song, 60, position_ms=660_000)
    assert play_days(settings, song) == {"2026-11-06": pytest.approx(660)}  # grace counts
    done = tablet.play(song, 240, position_ms=899_000)  # the song ends
    assert not done["allowed"] and done["reason"] == "lockout"
    assert tablet.stream_status(song) == 403


def test_grace_running_across_midnight_keeps_going(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path, clock: FixedClock
) -> None:
    moon = group(admin, "audiobook", "Moon")
    ep = add_media(settings, audio_file, moon, 1800)
    rule(admin, lockouts=[{"start": "23:55", "end": "07:00"}], grace_minutes=10)
    clock.set(datetime(2026, 11, 6, 23, 45, tzinfo=BERLIN))
    tablet.start(ep)
    assert tablet.play(ep, 600, position_ms=1320_000)["grace"]["active"]  # 23:55, 8 min left
    after = tablet.play(ep, 300, position_ms=1620_000)  # 00:00, still in grace
    assert after["allowed"] and after["grace"]["active"]
    assert tablet.stream_status(ep) == 206


@pytest.mark.parametrize("bedtime_grace", [0, 5])
def test_a_rule_expiring_later_needs_its_own_grace(
    admin: TestClient,
    tablet: Tablet,
    settings: Settings,
    audio_file: Path,
    bedtime_grace: int,
) -> None:
    """§3.3a "Multiple rules": a grace from one rule doesn't carry the item through
    another rule's cutoff."""
    music = group(admin, "music", "Music")
    song = add_media(settings, audio_file, music, 900)
    rule(admin, all_groups=False, group_ids=[music], max_minutes=5, grace_minutes=10)
    rule(admin, lockouts=[{"start": "14:30", "end": "07:00"}], grace_minutes=bedtime_grace)
    tablet.start(song)  # 14:20
    limit = tablet.play(song, 300, position_ms=300_000)  # 14:25: minute limit, 10 min left
    assert limit["allowed"] and limit["grace"]["active"]
    bed = tablet.play(song, 300, position_ms=600_000)  # 14:30: bedtime, 5 min left
    if bedtime_grace == 0:
        assert not bed["allowed"] and bed["reason"] == "lockout"
        assert tablet.stream_status(song) == 403
    else:
        assert bed["allowed"] and bed["grace"]["active"]
        assert tablet.stream_status(song) == 206
        tablet.clock.advance(minutes=11)  # 14:41: bedtime's own cap (2 x 5 min) has passed
        assert tablet.stream_status(song) == 403


# ---- resume (§5.3) -----------------------------------------------------------------------


def test_resume_per_group(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon, music = group(admin, "audiobook", "Moon"), group(admin, "music", "Music")
    ep = add_media(settings, audio_file, moon, 1000)
    song = add_media(settings, audio_file, music, 1000)
    tablet.start(ep)
    tablet.play(ep, 10, position_ms=600_000)
    assert tablet.start(ep)["position_ms"] == 600_000
    tablet.play(ep, 10, position_ms=980_000)  # within the last 3 %
    assert tablet.start(ep)["position_ms"] == 0
    tablet.start(song)
    tablet.play(song, 10, position_ms=500_000)
    assert tablet.start(song)["position_ms"] == 0
    with make_engine(settings.db_path).connect() as conn:
        assert (
            conn.execute(select(positions.c.media_id).where(positions.c.media_id == song)).first()
            is None
        )
    # switching resume off deletes stored positions
    tablet.start(ep)
    tablet.play(ep, 10, position_ms=300_000)
    admin.patch(f"/api/admin/groups/{moon}", json={"resume": False})
    assert tablet.start(ep)["position_ms"] == 0


# ---- admin: rules, preview, usage --------------------------------------------------------


def test_rule_validation(admin: TestClient) -> None:
    gid = group(admin, "music", "Music")
    bad = [
        {"name": "x", "days": [], "all_groups": True},
        {"name": "x", "days": [7], "all_groups": True},
        {"name": "x", "days": [1], "all_groups": False, "group_ids": []},
        {
            "name": "x",
            "days": [1],
            "all_groups": True,
            "lockouts": [{"start": "07:00", "end": "07:00"}],
        },
        {
            "name": "x",
            "days": [1],
            "all_groups": True,
            "lockouts": [{"start": "24:00", "end": "07:00"}],
        },
        {"name": "x", "days": [1], "all_groups": False, "group_ids": [999]},
    ]
    for body in bad:
        assert admin.post("/api/admin/rules", json=body).status_code == 422, body
    rid = rule(admin, all_groups=False, group_ids=[gid], max_minutes=90, days=[0, 6, 6])
    got = admin.get("/api/admin/rules").json()[0]
    assert got["id"] == rid and got["days"] == [0, 6] and got["group_ids"] == [gid]
    assert (
        admin.patch(f"/api/admin/rules/{rid}", json={"enabled": False}).json()["enabled"] is False
    )
    put = admin.put(f"/api/admin/rules/{rid}", json={**got, "max_minutes": 60}).json()
    assert put["max_minutes"] == 60
    assert admin.delete(f"/api/admin/rules/{rid}").status_code == 204
    assert admin.get("/api/admin/rules").json() == []


def test_preview_simulates_another_day(admin: TestClient) -> None:
    moon = group(admin, "audiobook", "Moon")
    rule(admin, all_groups=False, group_ids=[moon], days=[0, 1, 2, 3, 4], mode="block")
    rule(admin, lockouts=[{"start": "19:30", "end": "07:00"}])
    assert admin.get("/api/admin/preview").json()[0]["status"]["allowed"] is True  # Saturday
    tue = admin.get("/api/admin/preview", params={"at": "2026-11-10T10:00:00"}).json()[0]["status"]
    assert tue["reason"] == "blocked_today"
    night = admin.get("/api/admin/preview", params={"at": "2026-11-07T21:00:00"}).json()[0][
        "status"
    ]
    assert night["reason"] == "lockout" and night["params"] == {"until": "07:00"}


def test_usage_history_and_reset_today(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    music = group(admin, "music", "Music")
    m = add_media(settings, audio_file, music, 3600, "Morning Sunshine")
    rule(admin, all_groups=False, group_ids=[music], max_minutes=1)
    tablet.start(m)
    tablet.play(m, 60)
    day = admin.get("/api/admin/usage").json()
    assert day["day"] == "2026-11-07"
    assert day["groups"] == [{"group_id": music, "name": "Music", "items": 1, "seconds": 60.0}]
    assert day["plays"][0]["title"] == "Morning Sunshine"
    history = admin.get("/api/admin/usage/history", params={"days": 7}).json()
    assert len(history) == 7 and history[-1] == {"day": "2026-11-07", "items": 1, "seconds": 60.0}
    assert not tablet.start(m)["allowed"]
    assert admin.post("/api/admin/usage/reset-today").status_code == 204
    assert tablet.start(m)["allowed"]


def test_deleted_media_keeps_counting(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 600, "a")
    b = add_media(settings, audio_file, moon, 600, "b")
    rule(admin, all_groups=False, group_ids=[moon], max_items=1)
    tablet.start(a)
    assert admin.delete(f"/api/admin/media/{a}").status_code == 204
    assert tablet.start(b)["reason"] == "item_limit"  # §3.3: deleted media still counts today


def test_a_quick_tap_does_not_use_up_the_story(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    """§3.4 "Accidental taps", end to end: a file counts only after 10 s of playing."""
    moon = group(admin, "audiobook", "Moon")
    a, b, c = (add_media(settings, audio_file, moon, 600, n) for n in "abc")
    rule(admin, all_groups=False, group_ids=[moon], max_items=1, count_after_seconds=10)
    assert tablet.start(a)["allowed"]
    tablet.play(a, 4)  # tapped by accident
    assert tablet.start(b)["allowed"]  # a didn't count
    assert tablet.play(b, 15)["allowed"]  # b now counts and may go on
    assert tablet.start(b)["allowed"]  # replays stay free
    assert tablet.start(c)["reason"] == "item_limit"
    assert tablet.start(a)["reason"] == "item_limit"  # the tapped one is a new file again
    lib = tablet.c.get("/api/library", headers=tablet.h).json()
    assert lib["groups"][0]["status"]["items_left"] == 0


def test_count_after_seconds_defaults_to_10_and_is_validated(admin: TestClient) -> None:
    base = {"name": "r", "days": [0], "all_groups": True, "max_items": 1}
    created = admin.post("/api/admin/rules", json=base)
    assert created.status_code == 201 and created.json()["count_after_seconds"] == 10
    for bad in (-1, 601):
        r = admin.post("/api/admin/rules", json={**base, "count_after_seconds": bad})
        assert r.status_code == 422


def test_a_finished_story_cant_be_listened_to_again(
    admin: TestClient, tablet: Tablet, settings: Settings, audio_file: Path
) -> None:
    """§3.4 "Replay budget", end to end: 120 % of a 10-min episode is 12 min."""
    moon = group(admin, "audiobook", "Moon")
    a = add_media(settings, audio_file, moon, 600, "a")
    rule(admin, all_groups=False, group_ids=[moon], max_items=1, replay_percent=120)
    assert tablet.start(a)["allowed"]
    done = tablet.play(a, 600)  # listened to the end
    assert done["allowed"] and done["remaining_seconds"] == 120
    assert tablet.start(a)["remaining_seconds"] == 120  # a bit of rewinding is fine
    stop = tablet.play(a, 120)
    assert (stop["allowed"], stop["reason"]) == (False, "item_limit")
    assert tablet.start(a)["reason"] == "item_limit"
    item = tablet.c.get("/api/library", headers=tablet.h).json()["groups"][0]["items"][0]
    assert item["allowed"] is False


def test_replay_percent_defaults_to_120_and_can_be_unlimited(admin: TestClient) -> None:
    base = {"name": "r", "days": [0], "all_groups": True, "max_items": 1}
    assert admin.post("/api/admin/rules", json=base).json()["replay_percent"] == 120
    unlimited = admin.post("/api/admin/rules", json={**base, "replay_percent": None})
    assert unlimited.status_code == 201 and unlimited.json()["replay_percent"] is None
    for bad in (99, 1001):
        assert (
            admin.post("/api/admin/rules", json={**base, "replay_percent": bad}).status_code == 422
        )
