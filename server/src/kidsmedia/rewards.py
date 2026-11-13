"""Rewards (SPEC §14.4, §15.2, §15.3, §17.3): stars, the balance, the sticker album and the swap
checks.

Pure functions, no I/O: the routes load the rows and call these.
"""

from collections.abc import Iterable
from dataclasses import dataclass

# Kinds with a check that counts tries (§14.2, §15.2): right at the first try earns 1 more.
CHECKED_KINDS = frozenset({"read", "calc"})


@dataclass(frozen=True)
class DoneTask:
    """One `activity_done` row. `order` is its id, i.e. the order tasks were done in."""

    order: int
    world_id: int
    position: int
    kind: str
    day: str  # ISO date
    attempts: int | None = None


@dataclass(frozen=True)
class Sticker:
    position: int
    earned_on: str  # the day of the chapter's first done task


@dataclass(frozen=True)
class AlbumPage:
    world_id: int
    stickers: tuple[Sticker, ...]


def stars_for(kind: str, attempts: int | None) -> int:
    """1 star per done task, 1 more for a `read` or `calc` task right at the first try
    (§15.2)."""
    return 2 if kind in CHECKED_KINDS and attempts == 1 else 1


# What a done task earned or gave back (§14.4, §15.3, §17.3); every other kind spends.
EARNING_KINDS = frozenset({"earn", "bonus", "reopen"})

LedgerRow = tuple[str, int]  # (kind, delta) of a `reward_ledger` row


def earned_stars(rows: Iterable[LedgerRow]) -> int:
    """The stars earned: `earn` and `bonus` rows, minus what "open again" took back
    (`reopen`, §17.3: negative earning, not spending)."""
    return sum(d for k, d in rows if k in EARNING_KINDS)


def spent_stars(rows: Iterable[LedgerRow]) -> int:
    """The stars spent (swaps, wishes), as a positive number."""
    return -sum(d for k, d in rows if k not in EARNING_KINDS)


def balance(rows: Iterable[LedgerRow]) -> int:
    """The sum of the ledger. Earned stars are ledger rows too, so deleting a world (and its
    done rows) never takes stars away (§14.4); only "open again" does (§17.3)."""
    return sum(d for _, d in rows)


def reopen_delta(stars: int, balance_now: int) -> int:
    """The `reopen` row of "open again" (§17.3): the task's stars back, but never more than
    the balance, so it can't go below 0 (stars already swapped stay swapped)."""
    return -max(0, min(stars, balance_now))


def album(tasks: Iterable[DoneTask]) -> list[AlbumPage]:
    """One page per world, in the order the worlds were first worked on; one sticker per
    chapter with at least one done task, by position."""
    first_order: dict[int, int] = {}
    earned_on: dict[int, dict[int, tuple[int, str]]] = {}
    for t in tasks:
        if t.world_id not in first_order or t.order < first_order[t.world_id]:
            first_order[t.world_id] = t.order
        chapters = earned_on.setdefault(t.world_id, {})
        seen = chapters.get(t.position)
        if seen is None or t.order < seen[0]:
            chapters[t.position] = (t.order, t.day)
    return [
        AlbumPage(
            world_id=world_id,
            stickers=tuple(
                Sticker(position=pos, earned_on=earned_on[world_id][pos][1])
                for pos in sorted(earned_on[world_id])
            ),
        )
        for world_id in sorted(first_order, key=lambda w: first_order[w])
    ]


def swap_refusal(stars: int, cost: int, used_today: int, max_per_day: int | None) -> str | None:
    """Why a swap can't be redeemed now (a reason code), or None."""
    if max_per_day is not None and used_today >= max_per_day:
        return "swap_limit_reached"
    if stars < cost:
        return "not_enough_stars"
    return None


def swap_suits_rule(
    mode: str,
    max_items: int | None,
    max_minutes: int | None,
    extra_items: int,
    extra_minutes: int,
) -> bool:
    """Whether a swap can give anything under its rule: never for a `block` rule; extra
    items only with an item limit, extra minutes only with a minute limit (§14.4)."""
    if mode == "block":
        return False
    if extra_items > 0 and max_items is None:
        return False
    return not (extra_minutes > 0 and max_minutes is None)


def rule_active_on(days: str, weekday: int) -> bool:
    """`rules.days` (e.g. "01234", Mon=0) contains the weekday: its limits apply that day."""
    return str(weekday) in days
