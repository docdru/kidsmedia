"""Rules engine: every worked example of SPEC §3.4 plus edge cases (frozen times only)."""

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from kidsmedia.rules import (
    Mode,
    Rule,
    Usage,
    Window,
    due_gem_spends,
    evaluate,
    extend_grace,
    grace_rule,
    lockout_free_at,
)

TZ = ZoneInfo("Europe/Berlin")
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
WEEKDAYS = frozenset({MON, TUE, WED, THU, FRI})
WEEKEND = frozenset({SAT, SUN})
ALL_DAYS = frozenset(range(7))

MUSIC, MOON, FOX, CARTOONS = 1, 2, 3, 4
GROUPS = (MUSIC, MOON, FOX, CARTOONS)

# 2026-11-02 is a Monday.
DATES = {MON: 2, TUE: 3, WED: 4, THU: 5, FRI: 6, SAT: 7, SUN: 8}


def at(day: int, hhmm: str, seconds: int = 0) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, DATES[day], h, m, seconds, tzinfo=TZ)


def win(spec: str) -> Window:
    a, b = spec.split("-")
    return Window.parse(a, b)


def rule(
    rid: int,
    days: frozenset[int],
    *groups: int,
    lockouts: Sequence[str] = (),
    mode: Mode = "limit",
    max_items: int | None = None,
    max_minutes: int | None = None,
    grace_minutes: int = 0,
    count_after_seconds: int = 0,
    replay_percent: int | None = None,
    gem_cost: int | None = None,
) -> Rule:
    """A rule for the given groups (none = all groups)."""
    return Rule(
        id=rid,
        days=days,
        group_ids=frozenset(groups),
        all_groups=not groups,
        lockouts=tuple(win(w) for w in lockouts),
        mode=mode,
        max_items=max_items,
        max_minutes=max_minutes,
        grace_minutes=grace_minutes,
        count_after_seconds=count_after_seconds,
        replay_percent=replay_percent,
        gem_cost=gem_cost,
    )


# ---- §3.4 worked examples -------------------------------------------------------------


STORIES = [
    rule(1, WEEKEND, MOON, FOX, max_items=1),
    rule(2, WEEKDAYS, MOON, FOX, mode="block"),
]


def test_one_story_total_weekends_only() -> None:
    sat = at(SAT, "14:20")
    assert evaluate(STORIES, Usage(), GROUPS, MOON, 21, sat).allowed
    used = Usage(started={21: MOON})
    # a different file from either series is denied ...
    d = evaluate(STORIES, used, GROUPS, FOX, 31, sat)
    assert (d.allowed, d.reason, d.params) == (False, "item_limit", {"limit": 1})
    # ... but today's story may be replayed (§3.3)
    assert evaluate(STORIES, used, GROUPS, MOON, 21, sat).allowed
    # group status (new file?) reports 0 left
    assert evaluate(STORIES, used, GROUPS, MOON, None, sat).items_left == 0


def test_stories_blocked_on_school_days_with_next_day() -> None:
    d = evaluate(STORIES, Usage(), GROUPS, MOON, None, at(TUE, "10:00"))
    assert (d.allowed, d.reason, d.params) == (False, "blocked_today", {"next_day": SAT})


def test_one_from_each_series() -> None:
    rules = [rule(1, WEEKEND, MOON, max_items=1), rule(2, WEEKEND, FOX, max_items=1)]
    used = Usage(started={21: MOON})
    assert not evaluate(rules, used, GROUPS, MOON, 22, at(SAT, "10:00")).allowed
    assert evaluate(rules, used, GROUPS, FOX, 31, at(SAT, "10:00")).allowed


def test_music_90_minutes() -> None:
    rules = [rule(1, ALL_DAYS, MUSIC, max_minutes=90)]
    d = evaluate(rules, Usage(seconds={MUSIC: 89 * 60}), GROUPS, MUSIC, 1, at(TUE, "15:00"))
    assert d.allowed and d.remaining_seconds == 60 and d.minutes_left == 1
    d = evaluate(rules, Usage(seconds={MUSIC: 90 * 60}), GROUPS, MUSIC, 1, at(TUE, "15:00"))
    assert (d.allowed, d.reason, d.params, d.minutes_left) == (
        False,
        "minute_limit",
        {"limit": 90},
        0,
    )


def test_videos_30_weekdays_60_weekends() -> None:
    rules = [
        rule(1, WEEKDAYS, CARTOONS, max_minutes=30),
        rule(2, WEEKEND, CARTOONS, max_minutes=60),
    ]
    used = Usage(seconds={CARTOONS: 40 * 60})
    assert not evaluate(rules, used, GROUPS, CARTOONS, 1, at(WED, "16:00")).allowed
    d = evaluate(rules, used, GROUPS, CARTOONS, 1, at(SAT, "16:00"))
    assert d.allowed and d.minutes_left == 20


BEDTIME = [rule(1, ALL_DAYS, lockouts=["19:30-07:00"])]


def test_bedtime() -> None:
    d = evaluate(BEDTIME, Usage(), GROUPS, MUSIC, None, at(TUE, "19:29", 30))
    assert d.allowed and d.remaining_seconds == 30
    d = evaluate(BEDTIME, Usage(), GROUPS, MUSIC, None, at(TUE, "19:30"))
    assert (d.allowed, d.reason, d.params) == (False, "lockout", {"until": "07:00"})
    assert d.unlocks_at == at(WED, "07:00")
    assert not evaluate(BEDTIME, Usage(), GROUPS, MUSIC, None, at(WED, "06:59")).allowed
    assert evaluate(BEDTIME, Usage(), GROUPS, MUSIC, None, at(WED, "07:00")).allowed


def test_no_screens_during_lunch_only_affects_cartoons() -> None:
    rules = [rule(1, ALL_DAYS, CARTOONS, lockouts=["12:00-13:30"])]
    assert not evaluate(rules, Usage(), GROUPS, CARTOONS, None, at(SUN, "12:00")).allowed
    assert evaluate(rules, Usage(), GROUPS, MUSIC, None, at(SUN, "12:00")).allowed
    assert evaluate(rules, Usage(), GROUPS, CARTOONS, None, at(SUN, "13:30")).allowed


def test_grace_lets_the_episode_finish_at_bedtime() -> None:
    rules = [rule(1, ALL_DAYS, lockouts=["19:30-07:00"], grace_minutes=10)]
    d = evaluate(rules, Usage(started={21: MOON}), GROUPS, MOON, 21, at(SAT, "19:30"))
    assert not d.allowed
    granted = grace_rule(d, item_remaining_s=8 * 60)
    assert granted is not None and granted.rule_id == 1 and granted.grace_minutes == 10
    assert grace_rule(d, item_remaining_s=25 * 60) is None


@pytest.mark.parametrize(
    ("day", "hhmm", "locked"),
    [
        (SUN, "20:00", True),
        (MON, "06:00", True),
        (THU, "23:00", True),
        (FRI, "06:30", True),
        (FRI, "20:00", False),
        (SAT, "06:00", False),
        (SAT, "20:00", False),
        (SUN, "06:00", False),
    ],
)
def test_school_nights_only(day: int, hhmm: str, locked: bool) -> None:
    rules = [rule(1, frozenset({SUN, MON, TUE, WED, THU}), lockouts=["19:30-07:00"])]
    d = evaluate(rules, Usage(), GROUPS, MUSIC, None, at(day, hhmm))
    assert d.allowed is not locked
    if locked:
        assert d.reason == "lockout" and d.params == {"until": "07:00"}


def test_weekend_lie_in() -> None:
    rules = [
        rule(1, frozenset({SUN, MON, TUE, WED, THU}), lockouts=["19:30-07:00"]),
        rule(2, frozenset({FRI, SAT}), lockouts=["20:30-08:30"]),
    ]
    d = evaluate(rules, Usage(), GROUPS, MUSIC, None, at(SAT, "08:00"))
    assert not d.allowed and d.params == {"until": "08:30"}
    assert evaluate(rules, Usage(), GROUPS, MUSIC, None, at(MON, "07:30")).allowed


def test_midnight_starting_lockout_gets_a_countdown() -> None:
    rules = [rule(1, frozenset({SAT}), lockouts=["00:00-06:00"])]
    d = evaluate(rules, Usage(), GROUPS, MUSIC, 1, at(FRI, "23:50"))
    assert d.allowed and d.remaining_seconds == 600
    d = evaluate(rules, Usage(), GROUPS, MUSIC, 1, at(SAT, "00:00"))
    assert (d.allowed, d.reason, d.params) == (False, "lockout", {"until": "06:00"})
    graced = [rule(1, frozenset({SAT}), lockouts=["00:00-06:00"], grace_minutes=10)]
    assert (
        grace_rule(evaluate(graced, Usage(), GROUPS, MUSIC, 1, at(SAT, "00:00")), 120) is not None
    )


def test_block_starting_at_midnight_gets_a_countdown_but_no_grace() -> None:
    rules = [rule(1, frozenset({SAT}), mode="block", grace_minutes=10)]
    d = evaluate(rules, Usage(), GROUPS, MUSIC, 1, at(FRI, "23:50"))
    assert d.allowed and d.remaining_seconds == 600
    d = evaluate(rules, Usage(), GROUPS, MUSIC, 1, at(SAT, "00:00"))
    assert d.reason == "blocked_today" and grace_rule(d, 60) is None


def test_grace_never_starts_a_new_file() -> None:
    rules = [rule(1, WEEKEND, MOON, max_items=1, grace_minutes=10)]
    d = evaluate(rules, Usage(started={21: MOON}), GROUPS, MOON, 22, at(SAT, "15:00"))
    assert d.reason == "item_limit"
    assert grace_rule(d, item_remaining_s=120) is None  # even a 2-minute bonus track


# ---- edge cases ---------------------------------------------------------------------


def test_a_quick_tap_does_not_use_up_the_story() -> None:
    """§3.4 "Accidental taps": with count_after_seconds=10, a file played less than
    10 s today doesn't count, so the child may still pick another one."""
    stories = [rule(1, WEEKEND, MOON, FOX, max_items=1, count_after_seconds=10)]
    sat = at(SAT, "14:20")
    # Tapped episodes 21 and 22 for a few seconds each:
    tapped = Usage(started={21: MOON, 22: FOX}, played={21: 4.0, 22: 9.9})
    d = evaluate(stories, tapped, GROUPS, MOON, 23, sat)
    assert d.allowed and d.items_left == 1
    # Episode 23 reaches 10 s: now it is the story of the day ...
    listened = Usage(started={21: MOON, 22: FOX, 23: MOON}, played={21: 4.0, 22: 9.9, 23: 10.0})
    assert evaluate(stories, listened, GROUPS, MOON, 23, sat).allowed  # it may go on
    assert evaluate(stories, listened, GROUPS, MOON, 23, sat).items_left == 0
    # ... and the tapped ones are new files again, so they are denied now:
    for other in (21, 22, None):
        d = evaluate(stories, listened, GROUPS, MOON, other, sat)
        assert not d.allowed and d.reason == "item_limit"


def test_count_after_seconds_adds_up_over_the_day() -> None:
    """Stopping at 9 s and starting again doesn't dodge it: seconds are per file per day."""
    stories = [rule(1, ALL_DAYS, MOON, max_items=1, count_after_seconds=10)]
    now = at(WED, "10:00")
    twice = Usage(started={21: MOON}, played={21: 9.0 + 6.0})
    assert not evaluate(stories, twice, GROUPS, MOON, 22, now).allowed


def test_count_after_seconds_is_per_rule() -> None:
    """The same play may count for one rule (threshold 0) and not yet for another."""
    rules_ = [
        rule(1, ALL_DAYS, MOON, max_items=1),  # counts at start
        rule(2, ALL_DAYS, MOON, FOX, max_items=1, count_after_seconds=60),
    ]
    now = at(WED, "10:00")
    usage = Usage(started={21: MOON}, played={21: 30.0})
    assert not evaluate(rules_, usage, GROUPS, MOON, 22, now).allowed  # rule 1
    assert evaluate(rules_, usage, GROUPS, FOX, 31, now).allowed  # rule 2 hasn't counted it


def test_a_counted_story_has_a_listening_budget() -> None:
    """§3.4 "Replay budget": with replay_percent=120 a 30-min episode may play 36 min today."""
    stories = [rule(1, WEEKEND, MOON, max_items=1, count_after_seconds=10, replay_percent=120)]
    sat = at(SAT, "14:20")
    lengths = {21: 1800.0}
    first = Usage(started={21: MOON}, played={21: 1800.0}, durations=lengths)
    d = evaluate(stories, first, GROUPS, MOON, 21, sat)
    assert d.allowed and d.remaining_seconds == 360  # 6 min of replay left
    done = Usage(started={21: MOON}, played={21: 2160.0}, durations=lengths)
    d = evaluate(stories, done, GROUPS, MOON, 21, sat)
    assert (d.allowed, d.reason, d.params) == (False, "item_limit", {"limit": 1})
    # The stream check skips item limits (preloading), heartbeats and starts don't:
    assert evaluate(stories, done, GROUPS, MOON, 21, sat, ignore_item_limit=True).allowed


def test_the_budget_applies_even_with_items_left_and_not_before_counting() -> None:
    rules_ = [rule(1, ALL_DAYS, MOON, max_items=2, count_after_seconds=10, replay_percent=100)]
    now = at(WED, "10:00")
    lengths = {21: 600.0, 22: 600.0}
    # 21 is used up, but a second story is still free:
    usage = Usage(started={21: MOON, 22: MOON}, played={21: 600.0, 22: 5.0}, durations=lengths)
    assert evaluate(rules_, usage, GROUPS, MOON, 21, now).reason == "item_limit"
    assert evaluate(rules_, usage, GROUPS, MOON, 23, now).allowed
    # 22 was only tapped: no budget countdown yet.
    assert evaluate(rules_, usage, GROUPS, MOON, 22, now).remaining_seconds is None


def test_unlimited_replays_without_a_budget() -> None:
    stories = [rule(1, ALL_DAYS, MOON, max_items=1)]
    usage = Usage(started={21: MOON}, played={21: 99_999.0}, durations={21: 600.0})
    assert evaluate(stories, usage, GROUPS, MOON, 21, at(WED, "10:00")).allowed


def test_budget_and_count_after_only_apply_with_an_item_limit() -> None:
    """SPEC §3.1: replay_percent and count_after_seconds belong to max_items_per_day."""
    music = [rule(1, ALL_DAYS, MUSIC, max_minutes=90, count_after_seconds=10, replay_percent=100)]
    usage = Usage(
        started={11: MUSIC}, played={11: 600.0}, seconds={MUSIC: 600.0}, durations={11: 180.0}
    )
    d = evaluate(music, usage, GROUPS, MUSIC, 11, at(WED, "10:00"))
    assert d.allowed and d.remaining_seconds == 80 * 60  # only the minute limit


def test_no_rules_means_no_limits() -> None:
    d = evaluate([], Usage(), GROUPS, MUSIC, None, at(MON, "03:00"))
    assert d.allowed and d.remaining_seconds is None and d.items_left is None


def test_disabled_rules_are_ignored() -> None:
    off = [Rule(id=1, days=ALL_DAYS, all_groups=True, mode="block", enabled=False)]
    assert evaluate(off, Usage(), GROUPS, MUSIC, None, at(MON, "12:00")).allowed


def test_reason_priority_block_over_lockout_over_limits() -> None:
    rules = [
        rule(1, ALL_DAYS, MUSIC, max_minutes=10),
        rule(2, ALL_DAYS, lockouts=["12:00-13:00"]),
        rule(3, ALL_DAYS, MUSIC, mode="block"),
    ]
    used = Usage(seconds={MUSIC: 3600})
    d = evaluate(rules, used, GROUPS, MUSIC, None, at(MON, "12:30"))
    assert d.reason == "blocked_today" and {x.reason for x in d.denials} == {
        "blocked_today",
        "lockout",
        "minute_limit",
    }
    d = evaluate(rules[:2], used, GROUPS, MUSIC, None, at(MON, "12:30"))
    assert d.reason == "lockout"


def test_window_edges() -> None:
    rules = [rule(1, ALL_DAYS, lockouts=["12:00-13:00"])]
    assert evaluate(rules, Usage(), GROUPS, MUSIC, None, at(MON, "11:59", 59)).allowed
    assert not evaluate(rules, Usage(), GROUPS, MUSIC, None, at(MON, "12:00")).allowed
    assert not evaluate(rules, Usage(), GROUPS, MUSIC, None, at(MON, "12:59", 59)).allowed
    assert evaluate(rules, Usage(), GROUPS, MUSIC, None, at(MON, "13:00")).allowed


def test_chained_windows_unlock_at_the_last_end() -> None:
    rules = [
        rule(1, ALL_DAYS, lockouts=["19:00-20:00"]),
        rule(2, ALL_DAYS, lockouts=["19:45-21:00"]),
    ]
    d = evaluate(rules, Usage(), GROUPS, MUSIC, None, at(MON, "19:10"))
    assert d.params == {"until": "21:00"}
    assert lockout_free_at(rules, at(MON, "19:10")) == at(MON, "21:00")


def test_remaining_is_the_earliest_stop() -> None:
    rules = [rule(1, ALL_DAYS, MUSIC, max_minutes=60), rule(2, ALL_DAYS, lockouts=["19:30-07:00"])]
    early = evaluate(rules, Usage(), GROUPS, MUSIC, None, at(MON, "19:00"))
    assert early.remaining_seconds == 30 * 60  # bedtime comes first
    noon = evaluate(rules, Usage(), GROUPS, MUSIC, None, at(MON, "12:00"))
    assert noon.remaining_seconds == 60 * 60  # the minute limit comes first


def test_limits_are_shared_across_the_scope() -> None:
    rules = [rule(1, ALL_DAYS, MOON, FOX, max_minutes=60, max_items=2)]
    used = Usage(started={21: MOON, 31: FOX}, seconds={MOON: 1800, FOX: 1800})
    d = evaluate(rules, used, GROUPS, MOON, None, at(MON, "10:00"))
    # both limits are used up across the two groups; item_limit wins by §3.2 priority
    assert d.reason == "item_limit" and d.items_left == 0 and d.minutes_left == 0
    assert {x.reason for x in d.denials} == {"item_limit", "minute_limit"}


def test_stream_check_ignores_the_item_limit() -> None:
    used = Usage(started={21: MOON})
    assert evaluate(
        STORIES, used, GROUPS, FOX, 31, at(SAT, "10:00"), ignore_item_limit=True
    ).allowed
    assert not evaluate(
        STORIES, used, GROUPS, FOX, 31, at(TUE, "10:00"), ignore_item_limit=True
    ).allowed


def test_bonus_adjustments_extend_limits() -> None:
    rules = [rule(7, ALL_DAYS, MUSIC, max_minutes=30, max_items=1)]
    used = Usage(
        started={1: MUSIC}, seconds={MUSIC: 1800}, extra_items={7: 1}, extra_minutes={7: 15}
    )
    d = evaluate(rules, used, GROUPS, MUSIC, None, at(MON, "10:00"))
    assert d.allowed and d.items_left == 1 and d.minutes_left == 15


def test_grace_needs_every_expiring_rule_to_agree() -> None:
    rules = [
        rule(1, ALL_DAYS, MUSIC, max_minutes=10, grace_minutes=5),
        rule(2, ALL_DAYS, MUSIC, max_minutes=10),  # no grace
    ]
    d = evaluate(rules, Usage(seconds={MUSIC: 600}), GROUPS, MUSIC, 1, at(MON, "10:00"))
    assert grace_rule(d, 60) is None
    d = evaluate(rules[:1], Usage(seconds={MUSIC: 600}), GROUPS, MUSIC, 1, at(MON, "10:00"))
    assert grace_rule(d, 60) is not None


def test_dst_change_counts_real_seconds() -> None:
    # 2026-10-25: clocks go back 03:00 -> 02:00 in Berlin, so 01:00 -> 12:00 is 12 real hours.
    rules = [rule(1, ALL_DAYS, lockouts=["12:00-13:00"])]
    d = evaluate(rules, Usage(), GROUPS, MUSIC, None, datetime(2026, 10, 25, 1, 0, tzinfo=TZ))
    assert d.allowed and d.remaining_seconds == 12 * 3600
    # and the window still starts at 12:00 wall-clock time
    assert not evaluate(
        rules, Usage(), GROUPS, MUSIC, None, datetime(2026, 10, 25, 12, 0, tzinfo=TZ)
    ).allowed


def test_window_validation() -> None:
    with pytest.raises(ValueError):
        Window.parse("07:00", "07:00")
    with pytest.raises(ValueError):
        Window.parse("25:00", "07:00")


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError):
        evaluate([], Usage(), GROUPS, MUSIC, None, datetime(2026, 11, 2, 12, 0))


@pytest.mark.parametrize(("bedtime_grace", "expected"), [(0, None), (5, frozenset({1, 2}))])
def test_a_running_grace_only_covers_its_own_rules(
    bedtime_grace: int, expected: frozenset[int] | None
) -> None:
    """§3.3a "Multiple rules": a music-limit grace doesn't carry a song through bedtime."""
    rules = [
        rule(1, ALL_DAYS, MUSIC, max_minutes=5, grace_minutes=10),
        rule(2, ALL_DAYS, lockouts=["14:30-07:00"], grace_minutes=bedtime_grace),
    ]
    used = Usage(started={10: MUSIC}, seconds={MUSIC: 300.0})
    limit = evaluate(rules, used, GROUPS, MUSIC, 10, at(SAT, "14:25"))
    granted = grace_rule(limit, 600)
    assert granted is not None and granted.rule_id == 1
    covered = frozenset({granted.rule_id})
    assert extend_grace(limit, 590, covered) == covered  # nothing new: still covered
    bed = evaluate(rules, used, GROUPS, MUSIC, 10, at(SAT, "14:30"))
    assert extend_grace(bed, 300, covered) == expected
    assert extend_grace(bed, 301, covered) is None  # too much left for bedtime's 5 min


def test_a_running_grace_never_covers_a_block_or_item_limit() -> None:
    rules = [rule(1, ALL_DAYS, max_items=1, grace_minutes=10)]
    other = evaluate(rules, Usage(started={11: MUSIC}), GROUPS, MUSIC, 10, at(SAT, "10:00"))
    assert other.reason == "item_limit"
    assert extend_grace(other, 60, frozenset({1})) is None


def test_a_playing_file_keeps_the_limits_of_its_session_day() -> None:
    """§3.3: a session belongs to the day it started. Videos 30 min on weekdays, 60 at
    weekends: a Friday-evening video keeps Friday's limit after midnight."""
    rules = [
        rule(1, WEEKDAYS, CARTOONS, max_minutes=30),
        rule(2, WEEKEND, CARTOONS, max_minutes=60),
    ]
    friday = Usage(started={10: CARTOONS}, seconds={CARTOONS: 25 * 60.0})
    d = evaluate(rules, friday, GROUPS, CARTOONS, 10, at(SAT, "00:05"), limits_day=FRI)
    assert d.allowed and d.remaining_seconds == 5 * 60
    used_up = Usage(started={10: CARTOONS}, seconds={CARTOONS: 30 * 60.0})
    d = evaluate(rules, used_up, GROUPS, CARTOONS, 10, at(SAT, "00:10"), limits_day=FRI)
    assert (d.allowed, d.reason, d.params) == (False, "minute_limit", {"limit": 30})
    # A new start on Saturday uses Saturday's limits and usage.
    new = evaluate(rules, Usage(), GROUPS, CARTOONS, 11, at(SAT, "00:10"))
    assert new.remaining_seconds == 3600


def test_lockouts_and_blocks_follow_the_clock_for_a_playing_file() -> None:
    rules = [
        rule(1, frozenset({SAT}), lockouts=["00:00-06:00"]),
        rule(2, frozenset({SUN}), mode="block"),
    ]
    sat = evaluate(rules, Usage(), GROUPS, MUSIC, 10, at(SAT, "00:01"), limits_day=FRI)
    assert sat.reason == "lockout"
    sun = evaluate(rules, Usage(), GROUPS, MUSIC, 10, at(SUN, "08:00"), limits_day=SAT)
    assert sun.reason == "blocked_today"


# ---- gems (§16, §3.4 "Gems …" rows) ---------------------------------------------------

G = rule(10, ALL_DAYS, CARTOONS, gem_cost=2, count_after_seconds=10, replay_percent=120)
H = rule(11, ALL_DAYS, CARTOONS, gem_cost=1, count_after_seconds=10)
BED = rule(12, ALL_DAYS, lockouts=["19:30-07:00"], grace_minutes=10)
VIDEOS = rule(13, ALL_DAYS, CARTOONS, max_minutes=30)
VIDEO, OTHER, THIRD = 41, 42, 43
TEN_MIN = {VIDEO: 600.0, OTHER: 600.0, THIRD: 600.0}


def gem_usage(
    balance: int,
    played: dict[int, float] | None = None,
    paid: dict[int, frozenset[int]] | None = None,
    seconds: float | None = None,
) -> Usage:
    """Usage of Cartoons files: `played` seconds per file, the gem balance, paid listens."""
    played = played or {}
    total = sum(played.values()) if seconds is None else seconds
    return Usage(
        started=dict.fromkeys(played, CARTOONS),
        played=played,
        seconds={CARTOONS: total},
        durations=TEN_MIN,
        gem_balance=balance,
        gems_paid=paid or {},
    )


Params = dict[str, str | int | None]
PAID_G = {G.id: frozenset({VIDEO})}
WED_10 = at(WED, "10:00")

# (§3.4 row, rules, usage, media (None = a new file), time, allowed, reason, params)
GEM_CASES: list[tuple[str, list[Rule], Usage, int | None, datetime, bool, str | None, Params]] = [
    ("unlock: 3 gems open a new file", [G], gem_usage(3), None, WED_10, True, None, {}),
    ("unlock: the video starts", [G], gem_usage(3), VIDEO, WED_10, True, None, {}),
    (
        "unlock: counted and paid, it goes on",
        [G],
        gem_usage(1, {VIDEO: 10.0}, PAID_G),
        VIDEO,
        WED_10,
        True,
        None,
        {},
    ),
    (
        "unlock: replayed up to 12 min in total",
        [G],
        gem_usage(1, {VIDEO: 719.0}, PAID_G),
        VIDEO,
        WED_10,
        True,
        None,
        {},
    ),
    (
        "unlock: then item_limit",
        [G],
        gem_usage(1, {VIDEO: 720.0}, PAID_G),
        VIDEO,
        WED_10,
        False,
        "item_limit",
        {},
    ),
    (
        "unlock: another video can't start",
        [G],
        gem_usage(1, {VIDEO: 10.0}, PAID_G),
        OTHER,
        WED_10,
        False,
        "gems",
        {"need": 2, "have": 1},
    ),
    (
        "unlock: tomorrow the first video costs again",
        [G],
        gem_usage(1),
        VIDEO,
        at(THU, "10:00"),
        False,
        "gems",
        {"need": 2, "have": 1},
    ),
    (
        "other rules: enough gems don't open a lockout",
        [G, BED, VIDEOS],
        gem_usage(5),
        None,
        at(WED, "19:30"),
        False,
        "lockout",
        {"until": "07:00"},
    ),
    (
        "other rules: enough gems don't extend minutes",
        [G, BED, VIDEOS],
        gem_usage(5, {VIDEO: 10.0}, PAID_G, seconds=1800.0),
        VIDEO,
        WED_10,
        False,
        "minute_limit",
        {"limit": 30},
    ),
    (
        "other rules: a spent listen stops at 19:30",
        [G, BED, VIDEOS],
        gem_usage(0, {VIDEO: 60.0}, PAID_G),
        VIDEO,
        at(WED, "19:30"),
        False,
        "lockout",
        {"until": "07:00"},
    ),
    (
        "two rules: 2 gems aren't enough",
        [G, H],
        gem_usage(2),
        None,
        WED_10,
        False,
        "gems",
        {"need": 3, "have": 2},
    ),
    ("two rules: 3 gems are", [G, H], gem_usage(3), VIDEO, WED_10, True, None, {}),
    (
        "taken away: the next heartbeat denies before the count",
        [G],
        gem_usage(1, {VIDEO: 5.0}),
        VIDEO,
        WED_10,
        False,
        "gems",
        {"need": 2, "have": 1},
    ),
    (
        "taken away: after the count it goes on",
        [G],
        gem_usage(0, {VIDEO: 15.0}, PAID_G),
        VIDEO,
        WED_10,
        True,
        None,
        {},
    ),
]


@pytest.mark.parametrize(
    ("rules_", "usage", "media", "now", "allowed", "reason", "params"),
    [case[1:] for case in GEM_CASES],
    ids=[case[0] for case in GEM_CASES],
)
def test_gem_worked_examples(
    rules_: list[Rule],
    usage: Usage,
    media: int | None,
    now: datetime,
    allowed: bool,
    reason: str | None,
    params: Params,
) -> None:
    """§3.4 "Gems unlock a listen", "Gems with other rules", "Gems: two rules on one group",
    "Gems taken away while playing"."""
    d = evaluate(rules_, usage, GROUPS, CARTOONS, media, now)
    assert (d.allowed, d.reason, d.params) == (allowed, reason, params)


def test_gems_are_spent_when_the_file_counts() -> None:
    """§16.2 "Spending": due at count_after_seconds, not before; once per rule and file."""
    assert due_gem_spends([G], gem_usage(3, {VIDEO: 9.9}), CARTOONS, VIDEO, WED) == ([], 2)
    assert due_gem_spends([G], gem_usage(3, {VIDEO: 10.0}), CARTOONS, VIDEO, WED) == ([G], 2)
    assert due_gem_spends([G], gem_usage(1, {VIDEO: 60.0}, PAID_G), CARTOONS, VIDEO, WED) == (
        [],
        0,
    )
    # §3.4 "A tap of 5 s on a third video spends nothing"
    assert due_gem_spends([G], gem_usage(1, {THIRD: 5.0}), CARTOONS, THIRD, WED)[0] == []
    # Two rules: both are due at the count (2 + 1); not on a day the rule isn't active.
    assert due_gem_spends([G, H], gem_usage(3, {VIDEO: 10.0}), CARTOONS, VIDEO, WED) == (
        [G, H],
        3,
    )
    weekend = rule(14, WEEKEND, CARTOONS, gem_cost=2)
    assert due_gem_spends([weekend], gem_usage(3, {VIDEO: 10.0}), CARTOONS, VIDEO, WED) == (
        [],
        0,
    )
    # Another group isn't covered.
    assert due_gem_spends([G], gem_usage(3, {VIDEO: 10.0}), MUSIC, VIDEO, WED) == ([], 0)


def test_gem_status_fields_and_remaining_time() -> None:
    """§16.3: gem_cost for a new file, paid per item, and the budget in remaining_seconds."""
    new = evaluate([G, H], gem_usage(3), GROUPS, CARTOONS, None, WED_10)
    assert (new.gem_cost, new.gems_paid) == (3, False)
    half = evaluate([G, H], gem_usage(3, {VIDEO: 10.0}, PAID_G), GROUPS, CARTOONS, VIDEO, WED_10)
    assert (half.gem_cost, half.gems_paid) == (1, False)  # H isn't paid yet
    paid = evaluate([G], gem_usage(1, {VIDEO: 600.0}, PAID_G), GROUPS, CARTOONS, VIDEO, WED_10)
    assert (paid.gem_cost, paid.gems_paid) == (0, True)
    assert paid.remaining_seconds == 120  # 12 min budget, 10 played
    # No gem rule: no cost, nothing to pay.
    none = evaluate([VIDEOS], gem_usage(0), GROUPS, CARTOONS, VIDEO, WED_10)
    assert (none.gem_cost, none.gems_paid) == (None, False)
    # Not active today: no cost either.
    weekend = rule(14, WEEKEND, CARTOONS, gem_cost=2)
    assert evaluate([weekend], gem_usage(0), GROUPS, CARTOONS, None, WED_10).gem_cost is None


def test_gems_are_not_graceable_and_not_checked_by_the_stream() -> None:
    """§16.2: grace never skips gems; stream skips them like the item limit."""
    rules_ = [G, BED]
    usage = gem_usage(1, {VIDEO: 5.0})
    d = evaluate(rules_, usage, GROUPS, CARTOONS, VIDEO, at(WED, "19:30"))
    assert d.reason == "lockout" and {x.reason for x in d.denials} == {"lockout", "gems"}
    assert grace_rule(d, 60) is None  # the lockout alone would grant grace
    assert extend_grace(d, 60, frozenset({BED.id})) is None
    assert evaluate(rules_, usage, GROUPS, CARTOONS, VIDEO, WED_10, ignore_item_limit=True).allowed
    used_up = gem_usage(0, {VIDEO: 720.0}, PAID_G)
    assert evaluate([G], used_up, GROUPS, CARTOONS, VIDEO, WED_10, ignore_item_limit=True).allowed


def test_gems_are_the_last_reason() -> None:
    rules_ = [G, VIDEOS]
    d = evaluate(rules_, gem_usage(0, seconds=1800.0), GROUPS, CARTOONS, None, WED_10)
    assert d.reason == "minute_limit" and {x.reason for x in d.denials} == {
        "minute_limit",
        "gems",
    }


def test_a_gem_rule_with_an_item_limit_and_custom_text() -> None:
    """Both conditions hold together, and the parent's own words come with `gems`."""
    both = Rule(
        id=20,
        days=ALL_DAYS,
        group_ids=frozenset({CARTOONS}),
        max_items=2,
        gem_cost=2,
        count_after_seconds=10,
        message_en="Earn gems first!",
    )
    d = evaluate([both], gem_usage(1), GROUPS, CARTOONS, None, WED_10)
    assert (d.reason, d.params) == (
        "gems",
        {"need": 2, "have": 1, "message_en": "Earn gems first!"},
    )
    used = gem_usage(5, {VIDEO: 10.0, OTHER: 10.0}, {20: frozenset({VIDEO, OTHER})})
    d = evaluate([both], used, GROUPS, CARTOONS, THIRD, WED_10)
    assert (d.reason, d.params["limit"]) == ("item_limit", 2)


def test_a_playing_file_keeps_the_gems_paid_on_its_session_day() -> None:
    """§3.3: after midnight a paid Friday listen keeps going with Friday's usage."""
    friday = rule(15, frozenset({FRI}), CARTOONS, gem_cost=2, count_after_seconds=10)
    paid = gem_usage(0, {VIDEO: 60.0}, {15: frozenset({VIDEO})})
    d = evaluate([friday], paid, GROUPS, CARTOONS, VIDEO, at(SAT, "00:05"), limits_day=FRI)
    assert d.allowed
