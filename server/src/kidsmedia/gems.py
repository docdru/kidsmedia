"""Gems 💎 (SPEC §16): the per-profile ledger, the balance and booking spends.

The rules engine (rules.py) decides with the balance and the paid listens it is given;
this module reads them from `gem_ledger` and books a `spend` row when a file counts.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kidsmedia.clock import to_db
from kidsmedia.db import begin_immediate, gem_ledger
from kidsmedia.rules import Rule, Usage, due_gem_spends


def gem_balance(conn: Connection, profile_id: int) -> int:
    """The sum of the profile's ledger (§16.1)."""
    total = conn.execute(
        select(func.coalesce(func.sum(gem_ledger.c.delta), 0)).where(
            gem_ledger.c.profile_id == profile_id
        )
    ).scalar_one()
    return int(total)


def gems_paid(conn: Connection, profile_id: int, day: str) -> dict[int, frozenset[int]]:
    """rule_id -> the files whose listen that rule has been paid for on `day` (§16.2)."""
    paid: dict[int, set[int]] = {}
    for rule_id, media_id in conn.execute(
        select(gem_ledger.c.rule_id, gem_ledger.c.media_id).where(
            gem_ledger.c.profile_id == profile_id,
            gem_ledger.c.kind == "spend",
            gem_ledger.c.day == day,
            gem_ledger.c.rule_id.is_not(None),
            gem_ledger.c.media_id.is_not(None),
        )
    ).tuples():
        paid.setdefault(rule_id, set()).add(media_id)
    return {rule_id: frozenset(ids) for rule_id, ids in paid.items()}


def uses_gems(rules: Sequence[Rule]) -> bool:
    """An enabled gem rule exists, so the tablet shows the balance (§16.3 `used`)."""
    return any(r.enabled and r.gem_cost is not None for r in rules)


def book_gem_spends(
    conn: Connection,
    profile_id: int,
    rules: Sequence[Rule],
    usage: Usage,
    *,
    group_id: int,
    media_id: int,
    title: str,
    day: str,
    weekday: int,
    now: datetime,
    down_to_zero: bool,
) -> bool:
    """Book the gems of a file that counts now (§16.2 "Spending"), once per rule, file and
    day. Call it where `plays.seconds` grew, with the usage after that growth, before the
    evaluation of the same request. Returns whether a row was booked.

    `usage` is the session's day (`day`, weekday `weekday`). A heartbeat
    (`down_to_zero=False`) books only if the balance covers every gem rule of the file
    that isn't paid yet; otherwise it books nothing and condition 5 denies (§3.4 "Gems
    taken away while playing"). The offline batch (`down_to_zero=True`) books the
    listen that already happened with what is left, never below 0.
    """
    due, need = due_gem_spends(rules, usage, group_id, media_id, weekday)
    if not due:
        return False
    begin_immediate(conn)  # the balance check and the spend are one atomic step
    balance = gem_balance(conn, profile_id)
    if not down_to_zero and balance < need:
        return False
    booked = False
    for r in due:
        cost = min(r.gem_cost or 0, max(0, balance))
        result = conn.execute(
            sqlite_insert(gem_ledger)
            .values(
                profile_id=profile_id,
                delta=-cost,
                kind="spend",
                rule_id=r.id,
                media_id=media_id,
                day=day,
                title=title,
                created_at=to_db(now),
            )
            .on_conflict_do_nothing()
        )
        if result.rowcount:
            balance -= cost
            booked = True
    return booked
