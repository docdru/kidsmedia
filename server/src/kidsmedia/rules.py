"""The rules engine (SPEC §3). Pure and I/O-free: everything comes in as arguments.

Vocabulary
    rule      a usage constraint for some groups on some weekdays (§3.1)
    window    a lockout window "HH:MM-HH:MM"; it belongs to the day it starts (§3.3b)
    usage     what was played today: distinct files started, seconds per file and group,
              the gem balance and the listens paid with gems today (§16)
    decision  allowed?, the child-facing reason code + params, and how long until
              something will stop playback (remaining_seconds, §3.2)

All times are local, timezone-aware datetimes in the server's TZ.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

Reason = Literal["blocked_today", "lockout", "item_limit", "minute_limit", "gems"]
Mode = Literal["limit", "block"]

# §3.2: the first denying rule gives the reason, in this priority order.
PRIORITY: dict[Reason, int] = {
    "blocked_today": 0,
    "lockout": 1,
    "item_limit": 2,
    "minute_limit": 3,
    "gems": 4,
}
# Grace never spends or skips gems (§16.2): `gems` isn't graceable.
GRACEABLE: frozenset[Reason] = frozenset({"lockout", "minute_limit"})

DAY = 24 * 3600


@dataclass(frozen=True)
class Window:
    """Lockout window in seconds since midnight. start > end crosses midnight."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start == self.end:
            raise ValueError("a lockout window can't start and end at the same time")
        if not (0 <= self.start < DAY and 0 <= self.end < DAY):
            raise ValueError("window times must be within one day")

    @classmethod
    def parse(cls, start: str, end: str) -> "Window":
        return cls(_hhmm(start), _hhmm(end))

    @property
    def crosses_midnight(self) -> bool:
        return self.start > self.end


def _hhmm(value: str) -> int:
    h, m = value.split(":")
    hours, minutes = int(h), int(m)
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise ValueError(f"bad time {value!r}")
    return hours * 3600 + minutes * 60


def fmt_hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


@dataclass(frozen=True)
class Rule:
    id: int
    days: frozenset[int]  # weekday 0-6, Monday = 0
    enabled: bool = True
    all_groups: bool = False
    group_ids: frozenset[int] = frozenset()
    mode: Mode = "limit"
    max_items: int | None = None
    max_minutes: int | None = None
    lockouts: tuple[Window, ...] = ()
    grace_minutes: int = 0
    # A started file uses up an item only after this much playing today (§3.3): taps
    # and quick browsing don't count. 0 = counts at start.
    count_after_seconds: int = 0
    # Listening budget of a counted file, in % of its length (§3.3): once it has played
    # this much today, it is done for the day. None = replays are unlimited. Applies with
    # max_items or gem_cost (§16.2).
    replay_percent: int | None = None
    # Gems a listen of a file in the scope costs (§16.2): a file that isn't paid today may
    # only start if the balance covers it; spent when it counts. None = no gems needed.
    gem_cost: int | None = None
    name: str = ""
    message_en: str | None = None  # parent's own reason text (M6)
    message_de: str | None = None
    message_el: str | None = None

    def covers(self, group_id: int) -> bool:
        return self.enabled and (self.all_groups or group_id in self.group_ids)

    def scope(self, all_group_ids: Iterable[int]) -> frozenset[int]:
        return frozenset(all_group_ids) if self.all_groups else self.group_ids


@dataclass(frozen=True)
class Usage:
    """Today's usage. `started` maps each distinct file started today to its group,
    `played` the same keys to that file's playing seconds today. `gem_balance` is the
    profile's gem balance now (§16.1), `gems_paid` the files whose listen each gem rule
    has been paid for today (a `spend` row exists, §16.2)."""

    started: Mapping[int, int] = field(default_factory=dict)
    seconds: Mapping[int, float] = field(default_factory=dict)  # group_id -> seconds today
    played: Mapping[int, float] = field(default_factory=dict)  # file key -> seconds today
    durations: Mapping[int, float] = field(default_factory=dict)  # file key -> length in s
    extra_items: Mapping[int, int] = field(default_factory=dict)  # rule_id -> bonus (M6)
    extra_minutes: Mapping[int, int] = field(default_factory=dict)  # rule_id -> bonus (M6)
    gem_balance: int = 0
    gems_paid: Mapping[int, frozenset[int]] = field(default_factory=dict)  # rule_id -> files


@dataclass(frozen=True)
class Denial:
    rule_id: int
    reason: Reason
    grace_minutes: int


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: Reason | None = None
    params: dict[str, str | int | None] = field(default_factory=dict)
    denials: tuple[Denial, ...] = ()
    remaining_seconds: int | None = None  # allowed only; None = no limit in sight
    items_left: int | None = None
    minutes_left: int | None = None
    unlocks_at: datetime | None = None
    # §16.3: the gems this file (a new file for media_id=None) still needs today, summed over
    # the active gem rules that cover it and haven't been paid; None = no gem rule applies.
    gem_cost: int | None = None
    # §16.3 `paid_today`: an active gem rule covers the file, and every one of them is paid.
    gems_paid: bool = False


def counted_items(rule: Rule, usage: Usage) -> dict[int, int]:
    """The files that use up an item of `rule` today (§3.3): started and played for at
    least its `count_after_seconds`. File key -> group id."""
    need = rule.count_after_seconds
    return {k: g for k, g in usage.started.items() if usage.played.get(k, 0.0) >= need}


def replay_left(rule: Rule, usage: Usage, media_id: int) -> float | None:
    """Seconds of `media_id`'s listening budget left today under `rule` (§3.3), or None
    if the rule has no budget or the file's length is unknown. No length means no budget
    on purpose (SPEC §3.3): only deleted media or a missing duration_s get here."""
    length = usage.durations.get(media_id)
    if rule.replay_percent is None or not length:
        return None
    return length * rule.replay_percent / 100 - usage.played.get(media_id, 0.0)


def is_paid(rule: Rule, usage: Usage, media_id: int | None) -> bool:
    """The listen of `media_id` is paid today for the gem rule `rule` (§16.2)."""
    return media_id is not None and media_id in usage.gems_paid.get(rule.id, frozenset())


def active_gem_rules(rules: Sequence[Rule], group_id: int, weekday: int) -> list[Rule]:
    """The gem rules whose `gem_cost` applies to a file of `group_id` on `weekday` (the
    weekday of the session's day, §3.3): enabled, covering, `limit` mode, active."""
    return [
        r
        for r in rules
        if r.gem_cost is not None and r.mode != "block" and r.covers(group_id) and weekday in r.days
    ]


def due_gem_spends(
    rules: Sequence[Rule], usage: Usage, group_id: int, media_id: int, weekday: int
) -> tuple[list[Rule], int]:
    """§16.2 "Spending": the gem rules for which `media_id` counts now (it has played the
    rule's `count_after_seconds` on the usage's day) but isn't paid yet, and the gems that
    all unpaid gem rules covering it need (counting or not), i.e. condition 5's need."""
    unpaid = [
        r for r in active_gem_rules(rules, group_id, weekday) if not is_paid(r, usage, media_id)
    ]
    played = usage.played.get(media_id, 0.0)
    due = [r for r in unpaid if media_id in usage.started and played >= r.count_after_seconds]
    return due, sum(r.gem_cost or 0 for r in unpaid)


def _rule(rules: Sequence[Rule], rule_id: int) -> Rule:
    return next(r for r in rules if r.id == rule_id)


def _until(later: datetime, now: datetime) -> float:
    """Real seconds between two local times (correct across DST changes)."""
    return (later.astimezone(UTC) - now.astimezone(UTC)).total_seconds()


def _midnight(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _seconds_of_day(now: datetime) -> float:
    return (now - _midnight(now)).total_seconds()


def window_end_if_active(rule: Rule, window: Window, now: datetime) -> datetime | None:
    """If `window` of `rule` locks `now` (evening-anchored, §3.3b), when does it end?"""
    day = now.weekday()
    t = _seconds_of_day(now)
    today = _midnight(now)
    if not window.crosses_midnight:
        if day in rule.days and window.start <= t < window.end:
            return today + timedelta(seconds=window.end)
        return None
    if day in rule.days and t >= window.start:  # evening part, ends tomorrow
        return today + timedelta(days=1, seconds=window.end)
    if (day - 1) % 7 in rule.days and t < window.end:  # morning part of yesterday's window
        return today + timedelta(seconds=window.end)
    return None


def next_window_start_today(rule: Rule, now: datetime) -> datetime | None:
    """The next lockout of `rule` that starts later today and will be in effect."""
    if now.weekday() not in rule.days:
        return None
    t = _seconds_of_day(now)
    starts = [w.start for w in rule.lockouts if w.start > t]
    return _midnight(now) + timedelta(seconds=min(starts)) if starts else None


def lockout_free_at(rules: Sequence[Rule], now: datetime) -> datetime:
    """When no window of `rules` locks anymore (follows chained/overlapping windows)."""
    t = now
    for _ in range(16):
        ends = [
            end
            for r in rules
            for w in r.lockouts
            if (end := window_end_if_active(r, w, t)) is not None
        ]
        if not ends:
            return t
        t = max(ends)
    return t


def _next_unblocked_day(rules: Sequence[Rule], day: int) -> int | None:
    for k in range(1, 8):
        candidate = (day + k) % 7
        if not any(r.mode == "block" and candidate in r.days for r in rules):
            return candidate
    return None


def _denies_at_midnight(rules: Sequence[Rule], now: datetime) -> bool:
    """§3.2 'Midnight boundary': would a lockout or block deny a fresh start at 00:00?"""
    tomorrow = (now.weekday() + 1) % 7
    for r in rules:
        if tomorrow not in r.days:
            continue
        if r.mode == "block":
            return True
        if any(w.start == 0 for w in r.lockouts):
            return True
    return False


def evaluate(
    rules: Sequence[Rule],
    usage: Usage,
    all_group_ids: Iterable[int],
    group_id: int,
    media_id: int | None,
    now: datetime,
    *,
    ignore_item_limit: bool = False,
    limits_day: int | None = None,
) -> Decision:
    """May `media_id` of `group_id` play at `now`? media_id=None asks about a NEW file.

    Conditions 1-5 of §3.2; condition 5 (gems, §16.2) needs `usage.gem_balance` to cover
    the costs of every active gem rule that hasn't been paid for this file today.

    ignore_item_limit: used by the stream endpoint (§5.3), which can't know whether
    ExoPlayer is preloading the next queue item. It also skips the listening budget and
    the gems (condition 5, §16.2), like the item limit.
    limits_day: the weekday whose item and minute limits apply. A file that is already
    playing belongs to the day its session started (§3.3), so after midnight it keeps
    that day's limits (and `usage` is that day's usage). Lockouts and `block` always
    follow the clock. None = today.
    """
    if now.tzinfo is None:
        raise ValueError("evaluate() needs an aware local datetime")
    all_ids = frozenset(all_group_ids)
    day = now.weekday()
    limit_day = day if limits_day is None else limits_day
    covering = [r for r in rules if r.covers(group_id)]

    denials: list[Denial] = []
    remaining: list[float] = []
    items_left: int | None = None
    minutes_left: int | None = None
    gem_rules = 0  # active gem rules covering the file
    gem_need = 0  # their costs that aren't paid yet
    gem_denier: int | None = None  # the first of those: it gives the `gems` reason

    for r in covering:
        for w in r.lockouts:
            if window_end_if_active(r, w, now) is not None:
                denials.append(Denial(r.id, "lockout", r.grace_minutes))
                break
        start = next_window_start_today(r, now)
        if start is not None:
            remaining.append(_until(start, now))

        if r.mode == "block":
            if day in r.days:  # block uses the current weekday (§3.3b)
                denials.append(Denial(r.id, "blocked_today", 0))
            continue
        if limit_day not in r.days:
            continue  # limits use the weekday of the session's day (§3.3)
        scope = r.scope(all_ids)
        already_counted = False
        if r.max_items is not None:
            limit = r.max_items + usage.extra_items.get(r.id, 0)
            counted = counted_items(r, usage)
            count = sum(1 for g in counted.values() if g in scope)
            left = max(0, limit - count)
            items_left = left if items_left is None else min(items_left, left)
            # Only a file that already counts may go on once the limit is used up. One
            # that was just tapped (under count_after_seconds) is a new file again.
            already_counted = media_id is not None and media_id in counted
            if left == 0 and not already_counted and not ignore_item_limit:
                denials.append(Denial(r.id, "item_limit", r.grace_minutes))
        paid = False
        if r.gem_cost is not None:
            gem_rules += 1
            # Condition 5 (§16.2): a paid listen goes on; the others need gems together.
            paid = is_paid(r, usage, media_id)
            if not paid:
                gem_need += r.gem_cost
                gem_denier = r.id if gem_denier is None else gem_denier
        if (already_counted or paid) and media_id is not None and not ignore_item_limit:
            # A counted (or paid) file has a listening budget (replay_percent): once it is
            # used up, the file is done for today, like a new file over the limit.
            budget = replay_left(r, usage, media_id)
            if budget is not None and budget <= 0:
                denials.append(Denial(r.id, "item_limit", r.grace_minutes))
            elif budget is not None:
                remaining.append(budget)
        if r.max_minutes is not None:
            limit_s = (r.max_minutes + usage.extra_minutes.get(r.id, 0)) * 60
            left_s = limit_s - sum(usage.seconds.get(g, 0.0) for g in scope)
            whole = max(0, int(left_s // 60))
            minutes_left = whole if minutes_left is None else min(minutes_left, whole)
            if left_s <= 0:
                denials.append(Denial(r.id, "minute_limit", r.grace_minutes))
            else:
                remaining.append(left_s)
    if gem_denier is not None and gem_need > usage.gem_balance and not ignore_item_limit:
        denials.append(Denial(gem_denier, "gems", 0))
    gem_cost = gem_need if gem_rules else None
    gems_paid = gem_rules > 0 and gem_need == 0 and media_id is not None

    if denials:
        order = {r.id: i for i, r in enumerate(covering)}
        first = min(denials, key=lambda d: (PRIORITY[d.reason], order[d.rule_id]))
        params: dict[str, str | int | None] = {}
        unlocks_at: datetime | None = None
        if first.reason == "lockout":
            unlocks_at = lockout_free_at(covering, now)
            params["until"] = fmt_hhmm(unlocks_at)
        elif first.reason == "blocked_today":
            params["next_day"] = _next_unblocked_day(covering, day)
        else:
            rule = _rule(covering, first.rule_id)
            if first.reason == "gems":
                params["need"] = gem_need
                params["have"] = usage.gem_balance
            elif first.reason == "item_limit":
                # A used-up listening budget of a gem rule without an item limit has no
                # `limit` to show (§16.2).
                if rule.max_items is not None:
                    params["limit"] = rule.max_items + usage.extra_items.get(rule.id, 0)
            else:
                params["limit"] = (rule.max_minutes or 0) + usage.extra_minutes.get(rule.id, 0)
        # The parent's own words for this rule, if any (M6). Still params, not a
        # server sentence: the app shows them instead of its built-in text.
        denying = _rule(covering, first.rule_id)
        if denying.message_en:
            params["message_en"] = denying.message_en
        if denying.message_de:
            params["message_de"] = denying.message_de
        if denying.message_el:
            params["message_el"] = denying.message_el
        return Decision(
            allowed=False,
            reason=first.reason,
            params=params,
            denials=tuple(denials),
            items_left=items_left,
            minutes_left=minutes_left,
            unlocks_at=unlocks_at,
            gem_cost=gem_cost,
            gems_paid=gems_paid,
        )

    if _denies_at_midnight(covering, now):
        remaining.append(_until(_midnight(now) + timedelta(days=1), now))
    return Decision(
        allowed=True,
        remaining_seconds=int(min(remaining)) if remaining else None,
        items_left=items_left,
        minutes_left=minutes_left,
        gem_cost=gem_cost,
        gems_paid=gems_paid,
    )


def grace_rule(decision: Decision, item_remaining_s: float) -> Denial | None:
    """§3.3a: may the item that is already playing finish despite `decision`?

    Every denial must be a minute limit or a lockout whose rule grants enough grace
    for the rest of the item. Returns the most restrictive granting denial (its
    grace_minutes sets the wall-clock cap), or None.
    """
    if decision.allowed or not decision.denials:
        return None
    for d in decision.denials:
        if d.reason not in GRACEABLE or d.grace_minutes <= 0:
            return None
        if item_remaining_s > d.grace_minutes * 60:
            return None
    return min(decision.denials, key=lambda d: d.grace_minutes)


def extend_grace(
    decision: Decision, item_remaining_s: float, covered: frozenset[int]
) -> frozenset[int] | None:
    """§3.3a "Multiple rules", for a grace that is already running.

    `covered` are the rules that granted it. Their lockouts and minute limits stay
    covered; a rule that expires later must grant grace for the rest of the item on its
    own. Returns the rules covered from now on, or None if playback has to stop.
    """
    if decision.allowed:
        return covered
    for d in decision.denials:
        if d.reason not in GRACEABLE:
            return None
        if d.rule_id not in covered and (
            d.grace_minutes <= 0 or item_remaining_s > d.grace_minutes * 60
        ):
            return None
    return covered | {d.rule_id for d in decision.denials}
