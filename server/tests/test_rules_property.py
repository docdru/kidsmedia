"""Property tests for the rules engine (SPEC §10.3)."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from kidsmedia.rules import Rule, Usage, Window, evaluate

TZ = ZoneInfo("Europe/Berlin")
GROUPS = (1, 2, 3)


@st.composite
def windows(draw: st.DrawFn) -> Window:
    start = draw(st.integers(0, 47)) * 1800
    end = draw(st.integers(0, 47).filter(lambda e: e * 1800 != start)) * 1800
    return Window(start, end)


@st.composite
def rules(draw: st.DrawFn, rid: int = 1) -> Rule:
    return Rule(
        id=rid,
        days=frozenset(draw(st.sets(st.integers(0, 6), min_size=1))),
        all_groups=draw(st.booleans()),
        group_ids=frozenset(draw(st.sets(st.sampled_from(GROUPS), min_size=1))),
        mode=draw(st.sampled_from(["limit", "block"])),
        max_items=draw(st.none() | st.integers(0, 3)),
        max_minutes=draw(st.none() | st.integers(0, 120)),
        lockouts=tuple(draw(st.lists(windows(), max_size=2))),
        grace_minutes=draw(st.integers(0, 15)),
        count_after_seconds=draw(st.integers(0, 30)),
        replay_percent=draw(st.none() | st.integers(100, 200)),
        gem_cost=draw(st.none() | st.integers(1, 5)),
    )


usages = st.builds(
    Usage,
    started=st.dictionaries(st.integers(1, 6), st.sampled_from(GROUPS), max_size=4),
    seconds=st.dictionaries(st.sampled_from(GROUPS), st.floats(0, 9000), max_size=3),
    played=st.dictionaries(st.integers(1, 6), st.floats(0, 2000), max_size=4),
    durations=st.dictionaries(st.integers(1, 6), st.floats(1, 1800), max_size=6),
    gem_balance=st.integers(0, 10),
    gems_paid=st.dictionaries(
        st.sampled_from([1, 99]), st.frozensets(st.integers(1, 6), max_size=3), max_size=2
    ),
)
moments = st.integers(0, 7 * 24 * 60 - 1).map(
    lambda m: datetime(2026, 11, 2, tzinfo=TZ) + timedelta(minutes=m)
)


@settings(max_examples=400, deadline=None)
@given(
    st.lists(rules(), max_size=3),
    rules(rid=99),
    usages,
    st.sampled_from(GROUPS),
    st.none() | st.integers(1, 6),
    moments,
)
def test_adding_a_rule_never_allows_more(
    base: list[Rule], extra: Rule, usage: Usage, group: int, media: int | None, now: datetime
) -> None:
    """Also for gem rules (§16): a gem cost, a budget under it, never opens anything."""
    before = evaluate(base, usage, GROUPS, group, media, now)
    after = evaluate([*base, extra], usage, GROUPS, group, media, now)
    if not before.allowed:
        assert not after.allowed
    if after.allowed and before.remaining_seconds is not None:
        assert after.remaining_seconds is not None
        assert after.remaining_seconds <= before.remaining_seconds


@settings(max_examples=300, deadline=None)
@given(
    st.lists(rules(), max_size=3),
    rules(rid=99),
    st.integers(1, 20),
    usages,
    st.sampled_from(GROUPS),
    st.none() | st.integers(1, 6),
    moments,
)
def test_adding_a_gem_rule_never_allows_more(
    base: list[Rule],
    extra: Rule,
    cost: int,
    usage: Usage,
    group: int,
    media: int | None,
    now: datetime,
) -> None:
    """§16.2: a gem rule only adds conditions (gems, the listening budget), so it never
    makes something allowed that wasn't, and never gives more time."""
    gem = replace(extra, mode="limit", gem_cost=cost)
    before = evaluate(base, usage, GROUPS, group, media, now)
    after = evaluate([*base, gem], usage, GROUPS, group, media, now)
    if not before.allowed:
        assert not after.allowed
    if after.allowed and before.remaining_seconds is not None:
        assert after.remaining_seconds is not None
        assert after.remaining_seconds <= before.remaining_seconds


@settings(max_examples=200, deadline=None)
@given(
    st.lists(rules(), max_size=3),
    usages,
    st.integers(0, 10),
    st.sampled_from(GROUPS),
    st.none() | st.integers(1, 6),
    moments,
)
def test_fewer_gems_never_allow_more(
    rs: list[Rule], usage: Usage, less: int, group: int, media: int | None, now: datetime
) -> None:
    poorer = replace(usage, gem_balance=max(0, usage.gem_balance - less))
    if not evaluate(rs, usage, GROUPS, group, media, now).allowed:
        assert not evaluate(rs, poorer, GROUPS, group, media, now).allowed


@settings(max_examples=200, deadline=None)
@given(st.lists(rules(), max_size=3), usages, st.sampled_from(GROUPS), moments)
def test_remaining_time_never_negative_and_denials_have_reasons(
    rs: list[Rule], usage: Usage, group: int, now: datetime
) -> None:
    d = evaluate(rs, usage, GROUPS, group, None, now)
    if d.allowed:
        assert d.reason is None and (d.remaining_seconds is None or d.remaining_seconds >= 0)
    else:
        assert d.reason is not None and d.denials
