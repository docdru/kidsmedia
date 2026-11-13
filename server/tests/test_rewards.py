"""Rewards logic without I/O: stars, balance, album and swap checks (SPEC §14.4)."""

import pytest

from kidsmedia.rewards import (
    AlbumPage,
    DoneTask,
    Sticker,
    album,
    balance,
    earned_stars,
    reopen_delta,
    rule_active_on,
    spent_stars,
    stars_for,
    swap_refusal,
    swap_suits_rule,
)


def done(
    order: int,
    world: int = 1,
    position: int = 1,
    kind: str = "draw",
    day: str = "2026-11-07",
    attempts: int | None = None,
) -> DoneTask:
    return DoneTask(order, world, position, kind, day, attempts)


@pytest.mark.parametrize(
    ("kind", "attempts", "stars"),
    [
        ("draw", None, 1),
        ("calc", None, 1),
        ("calc", 1, 2),  # all problems right at the first try: 1 more (§15.2)
        ("calc", 3, 1),
        ("write", 1, 1),  # attempts only count for reading and maths
        ("read", 1, 2),  # read right at the first try: 1 more
        ("read", 2, 1),
        ("read", None, 1),  # an old app without attempts
    ],
)
def test_stars_per_done_task(kind: str, attempts: int | None, stars: int) -> None:
    assert stars_for(kind, attempts) == stars


def test_earned_spent_and_balance_come_from_the_ledger() -> None:
    ledger = [("earn", 1), ("earn", 1), ("earn", 2), ("swap", -3), ("bonus", 1), ("wish", -1)]
    assert earned_stars(ledger) == 5
    assert spent_stars(ledger) == 4
    assert balance(ledger) == 1
    assert (earned_stars([]), spent_stars([]), balance([])) == (0, 0, 0)


def test_open_again_takes_back_earned_stars_not_spent_ones() -> None:
    """A `reopen` row is negative earning, not spending (§17.3)."""
    ledger = [("earn", 2), ("bonus", 1), ("earn", 1), ("swap", -1), ("reopen", -3)]
    assert earned_stars(ledger) == 1
    assert spent_stars(ledger) == 1
    assert balance(ledger) == 0


@pytest.mark.parametrize(
    ("stars", "balance_now", "delta"),
    [(2, 5, -2), (2, 2, -2), (3, 1, -1), (2, 0, 0), (2, -1, 0), (0, 4, 0)],
)
def test_open_again_never_goes_below_zero(stars: int, balance_now: int, delta: int) -> None:
    assert reopen_delta(stars, balance_now) == delta


@pytest.mark.parametrize(
    ("mode", "max_items", "max_minutes", "extra_items", "extra_minutes", "suits"),
    [
        ("limit", 1, None, 1, 0, True),
        ("limit", None, 30, 0, 15, True),
        ("limit", 0, 0, 1, 0, True),  # a limit of 0 can still be raised
        ("block", 1, 30, 1, 0, False),  # a swap never opens a block
        ("limit", None, 30, 1, 0, False),  # extra items without an item limit give nothing
        ("limit", 1, None, 0, 15, False),  # extra minutes without a minute limit, too
    ],
)
def test_a_swap_must_give_something_under_its_rule(
    mode: str,
    max_items: int | None,
    max_minutes: int | None,
    extra_items: int,
    extra_minutes: int,
    suits: bool,
) -> None:
    assert swap_suits_rule(mode, max_items, max_minutes, extra_items, extra_minutes) is suits


def test_rule_days() -> None:
    assert rule_active_on("01234", 0)
    assert not rule_active_on("01234", 5)
    assert not rule_active_on("", 3)


def test_album_pages_follow_the_done_order_and_stickers_the_chapters() -> None:
    tasks = [
        done(1, world=7, position=2, day="2026-11-09"),
        done(2, world=7, position=1, day="2026-11-10"),  # caught up later
        done(3, world=7, position=2, kind="calc", day="2026-11-11"),  # an extra: same sticker
        done(4, world=3, position=1, day="2026-11-16"),
    ]
    assert album(tasks) == [
        AlbumPage(7, (Sticker(1, "2026-11-10"), Sticker(2, "2026-11-09"))),
        AlbumPage(3, (Sticker(1, "2026-11-16"),)),
    ]
    assert album([]) == []


@pytest.mark.parametrize(
    ("stars", "cost", "used", "limit", "reason"),
    [
        (5, 5, 0, 1, None),
        (4, 5, 0, 1, "not_enough_stars"),
        (9, 5, 1, 1, "swap_limit_reached"),
        (0, 5, 2, 2, "swap_limit_reached"),  # the limit is reported first
        (9, 5, 7, None, None),  # no daily limit
    ],
)
def test_swap_refusal(
    stars: int, cost: int, used: int, limit: int | None, reason: str | None
) -> None:
    assert swap_refusal(stars, cost, used, limit) == reason
