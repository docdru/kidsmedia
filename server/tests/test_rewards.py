"""Rewards logic without I/O: stars, balance, album and swap checks (SPEC §14.4)."""

import pytest

from kidsmedia.rewards import (
    AlbumPage,
    DoneTask,
    Sticker,
    active_chapter_award,
    album,
    balance,
    chapter_complete,
    earned_stars,
    held_stars,
    reopen_delta,
    rule_active_on,
    spent_stars,
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


def test_a_chapter_is_complete_when_every_idea_is_done() -> None:
    """§14.4: stars only for a chapter with every idea done, extras included."""
    kinds = ["draw", "calc", "write", "read"]
    assert not chapter_complete(["draw", "calc"], kinds)  # enough to open the next one
    assert not chapter_complete(["draw", "calc", "write"], kinds)
    assert chapter_complete(["read", "write", "calc", "draw"], kinds)
    assert not chapter_complete([], [])  # a chapter without tasks earns nothing


def test_the_chapter_award_is_held_until_taken_back() -> None:
    """§14.4, §17.3: a `chapter` row of one of the chapter's done tasks holds its stars
    until a `reopen` row with that id takes them back."""
    ledger = [("earn", 1, 1), ("chapter", 4, 3), ("bonus", 2, 1)]
    assert active_chapter_award(ledger, [1, 2, 3, 4]) == 4
    assert active_chapter_award(ledger, [5, 6]) is None  # another chapter
    taken = [*ledger, ("reopen", 4, -3)]
    assert active_chapter_award(taken, [1, 2, 3, 4]) is None
    again = [*taken, ("chapter", 7, 3)]  # finished again after "open again"
    assert active_chapter_award(again, [1, 2, 3, 7]) == 7
    assert active_chapter_award([("chapter", 4, 3), ("reopen", 4, 0)], [4]) is None


def test_held_stars_of_a_done_task() -> None:
    ledger = [("earn", 1, 1), ("chapter", 4, 3), ("bonus", 4, 1), ("reopen", 4, -3)]
    assert held_stars(ledger, 4) == 1  # the chapter's stars were taken back, the bonus stays
    assert held_stars(ledger, 1) == 1  # an `earn` row from before v0.24
    assert held_stars(ledger, 9) == 0


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
