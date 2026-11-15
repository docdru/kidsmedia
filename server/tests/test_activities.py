"""How the story moves on, pack format and opening times, pure (SPEC §13.2, §13.5,
§14.1-§14.4a, §15.2, §15.5).

The §13.2 example table doubles as these tests: Saturday 7 Nov 2026, 2 ideas of 4 needed.
"""

from datetime import date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from kidsmedia.activities import (
    SIMPLE_MATERIALS,
    Pack,
    PlannedWorld,
    Progress,
    Schedule,
    TaskTexts,
    Window,
    done_allowed,
    is_complete,
    is_simple_material,
    load_langs,
    merge_langs,
    open_state,
    pack_warnings,
    schedule,
    story_lang,
)
from tests.conftest import BERLIN

SAT = date(2026, 11, 7)
SUN = date(2026, 11, 8)


def at(day: int, hh: int = 14, mm: int = 20) -> datetime:
    return datetime(2026, 11, day, hh, mm, tzinfo=BERLIN)


def world(world_id: int, chapters: int = 4, tasks: int = 4) -> PlannedWorld:
    return PlannedWorld(world_id, (tasks,) * chapters)


def started(world_id: int, *opened: date) -> Progress:
    return Progress(world_id, opened or (SAT,))


def ideas(day: date, count: int = 2) -> list[date]:
    """`count` done ideas (distinct kinds) of one chapter, all on `day`."""
    return [day] * count


def unlocked(s: Schedule) -> list[bool]:
    return [c.unlocked for c in s.chapters]


# The §13.2 example table: needed 2 of 4, Saturday 7 Nov 2026.


def test_a_new_first_world_opens_chapter_one_now() -> None:
    s = schedule([world(1)], Progress(), {}, 2, None, at(7))
    assert s.current == 1
    assert unlocked(s) == [True, False, False, False]
    assert s.chapters[0].opened_on == SAT
    assert s.progress == Progress(1, (SAT,), {})  # chapter 1 doesn't count for the limit
    assert s.next_unlock_at is None
    assert not s.story_done


def test_one_idea_is_not_enough() -> None:
    s = schedule([world(1)], started(1), {1: {1: ideas(SAT, 1)}}, 2, None, at(7))
    assert unlocked(s) == [True, False, False, False]


def test_two_ideas_open_the_next_chapter_the_same_day() -> None:
    s = schedule([world(1)], started(1), {1: {1: ideas(SAT)}}, 2, None, at(7))
    assert unlocked(s) == [True, True, False, False]
    assert s.chapters[1].opened_on == SAT
    assert s.opened_today == 1


def test_the_setting_decides_how_many() -> None:
    done = {1: {1: ideas(SAT, 3)}}
    assert unlocked(schedule([world(1)], started(1), done, 4, None, at(7)))[1] is False
    assert unlocked(schedule([world(1)], started(1), done, 3, None, at(7)))[1] is True
    assert unlocked(schedule([world(1)], started(1), done, 1, None, at(7)))[1] is True


def test_a_chapter_with_fewer_tasks_needs_at_most_all_of_them() -> None:
    small = PlannedWorld(1, (1, 4))
    s = schedule([small], started(1), {1: {1: ideas(SAT, 1)}}, 2, None, at(7))
    assert [c.needed for c in s.chapters] == [1, 2]
    assert unlocked(s) == [True, True]


def test_several_chapters_in_one_afternoon_without_a_limit() -> None:
    done = {1: {1: ideas(SAT), 2: ideas(SAT), 3: ideas(SAT)}}
    s = schedule([world(1)], started(1), done, 2, None, at(7))
    assert unlocked(s) == [True, True, True, True]
    assert s.opened_today == 3


def test_the_daily_limit_holds_the_next_chapter_until_tomorrow() -> None:
    done = {1: {1: ideas(SAT), 2: ideas(SAT)}}
    s = schedule([world(1)], started(1), done, 2, 1, at(7))
    assert unlocked(s) == [True, True, False, False]
    assert s.next_unlock_at == datetime(2026, 11, 8, 0, 0, tzinfo=BERLIN)
    # Sunday: chapter 3 opens, dated Sunday.
    s = schedule([world(1)], s.progress, done, 2, 1, at(8, 9, 0))
    assert unlocked(s) == [True, True, True, False]
    assert s.chapters[2].opened_on == SUN
    assert s.next_unlock_at is None


def test_finishing_the_last_chapter_starts_the_next_world() -> None:
    done = {1: {1: ideas(SAT), 2: ideas(SAT)}}
    s = schedule([world(1, chapters=2), world(2)], started(1), done, 2, None, at(7))
    assert s.current == 2
    assert s.queue == (2,)  # housekeeping drops the finished world
    assert unlocked(s) == [True, False, False, False]
    assert s.progress.opened == (SAT,)
    assert s.opened_today == 2  # its chapter 1 counts: it opened by finishing


def test_the_next_world_waits_for_the_daily_limit_too() -> None:
    done = {1: {1: ideas(SAT), 2: ideas(SAT)}}
    s = schedule([world(1, chapters=2), world(2)], started(1), done, 2, 1, at(7))
    assert s.current == 1
    assert s.next_unlock_at == datetime(2026, 11, 8, 0, 0, tzinfo=BERLIN)
    assert schedule([world(1, chapters=2), world(2)], s.progress, done, 2, 1, at(8)).current == 2


def test_the_last_story_done_and_nothing_queued() -> None:
    done = {1: {1: ideas(SAT), 2: ideas(SAT)}}
    s = schedule([world(1, chapters=2)], started(1), done, 2, None, at(7))
    assert s.current == 1
    assert s.story_done
    assert unlocked(s) == [True, True]
    # The parent queues the next story: it starts at once.
    s = schedule([world(1, chapters=2), world(2)], s.progress, done, 2, None, at(9))
    assert s.current == 2
    assert not s.story_done


def test_open_chapters_stay_open_when_an_idea_is_opened_again() -> None:
    s = schedule([world(1)], started(1, SAT, SAT), {1: {1: ideas(SAT, 1)}}, 2, None, at(7))
    assert unlocked(s) == [True, True, False, False]


def test_a_new_first_world_starts_fresh_and_reordering_later_ones_keeps_the_progress() -> None:
    progress = started(1, SAT, SAT)
    kept = schedule([world(1), world(3), world(2)], progress, {}, 2, None, at(9))
    assert kept.progress.opened == (SAT, SAT)
    fresh = schedule([world(2), world(1)], progress, {}, 2, None, at(9))
    assert fresh.current == 2
    assert fresh.progress.opened == (date(2026, 11, 9),)


def test_ideas_done_before_a_world_started_open_its_chapters_from_its_start() -> None:
    """A world queued again: earlier done rows count, from the day it started again."""
    done = {1: {1: ideas(date(2026, 10, 1))}}
    s = schedule([world(1)], Progress(), done, 2, None, at(7))
    assert unlocked(s) == [True, True, False, False]
    assert s.chapters[1].opened_on == SAT


def test_at_an_earlier_moment_a_later_chapter_is_still_locked() -> None:
    """A queued tap is judged at its moment (§14.4a) with the progress as of now."""
    s = schedule([world(1)], started(1, SAT, SUN), {}, 2, None, at(7, 18, 0))
    assert unlocked(s) == [True, False, False, False]


def test_empty_queue() -> None:
    s = schedule([], started(1), {}, 2, None, at(7))
    assert s.current is None
    assert s.chapters == ()
    assert s.progress == Progress()


def test_old_counts_are_dropped() -> None:
    progress = Progress(1, (SAT,), {date(2026, 10, 1): 3, SAT: 1})
    assert schedule([world(1)], progress, {}, 2, None, at(7)).progress.opens == {SAT: 1}


@pytest.mark.parametrize("raw", [None, [], {"opened": ["x"]}, {"opens": {"2026-11-07": "a"}}, 5])
def test_unreadable_progress_is_a_fresh_start(raw: object) -> None:
    assert Progress.from_json(raw) == Progress()


def test_progress_round_trip() -> None:
    progress = Progress(4, (SAT, SUN), {SUN: 2})
    assert Progress.from_json(progress.to_json()) == progress
    assert progress.to_json() == {
        "world": 4,
        "opened": ["2026-11-07", "2026-11-08"],
        "opens": {"2026-11-08": 2},
    }


# ---- story language (§14.7) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("chosen", "available", "default", "expected"),
    [
        ("el", ["de", "en", "el"], "de", "el"),  # the parent's choice
        (None, ["de", "en", "el"], "de", "de"),  # nothing chosen: DEFAULT_LOCALE
        ("el", ["de", "en"], "de", "de"),  # the world lost its Greek texts
        (None, ["de", "el"], "en", "de"),  # no English: German, then Greek
        (None, ["el"], "en", "el"),
        ("fr", ["en"], "de", "en"),  # an unknown stored value
    ],
)
def test_story_lang(chosen: object, available: list[str], default: str, expected: str) -> None:
    assert story_lang(chosen, available, default) == expected  # type: ignore[arg-type]


def test_langs_load_and_merge() -> None:
    assert load_langs({"3": "el", "x": "de", "4": "fr", "5": "en"}) == {3: "el", 5: "en"}
    assert load_langs(None) == {}
    # A change updates; a world leaving the queue loses its entry; JSON keys are strings.
    assert merge_langs({1: "el", 2: "de"}, {3: "en"}, [2, 3]) == {"2": "de", "3": "en"}


# ---- pack format ------------------------------------------------------------------------


def question(text: str = "Who?", correct: int = 0) -> dict[str, object]:
    return {"question": text, "answers": ["Mira", "Pip", "Bo"], "correct": correct}


def problem(
    text: str = "Pip has 2 shells and finds 3. How many?", answer: int = 5
) -> dict[str, object]:
    return {"text": text, "answer": answer, "hint": "Draw the shells and count them."}


def problems(*answers: int) -> list[dict[str, object]]:
    return [problem(f"Problem {i}", a) for i, a in enumerate(answers, start=1)]


FIRST_MATERIAL: dict[str, str] = {lang: m[0] for lang, m in SIMPLE_MATERIALS.items()}
LEVEL_ANSWERS: dict[str, tuple[int, ...]] = {
    "easier": (3, 7, 9),
    "normal": (12, 5, 18, 20),
    "harder": (45, 13, 60, 99, 8),
}


def texts(kind: str, lang: str = "en") -> dict[str, object]:
    """A complete text block: all three levels, a simple material of the language (§15.5),
    questions per level for read (§14.2), problems per level for calc (§15.2)."""
    block: dict[str, object] = {
        "title": "T",
        "text": "Do it",
        "materials": [FIRST_MATERIAL[lang]],
        "easier": "Do a bit",
        "harder": "Do more",
    }
    if kind == "read":
        block["materials"] = []
        block["questions"] = {level: [question()] for level in ("easier", "normal", "harder")}
    if kind == "calc":
        block["problems"] = {level: problems(*a) for level, a in LEVEL_ANSWERS.items()}
    return block


def task(kind: str, langs: tuple[str, ...] = ("de", "en", "el")) -> dict[str, object]:
    return {"kind": kind, "minutes": 10, "texts": {lang: texts(kind, lang) for lang in langs}}


def read(**over: object) -> dict[str, object]:
    """A read task in English only, its text block changed by `over`."""
    return {"kind": "read", "minutes": 5, "texts": {"en": texts("read") | over}}


def calc(**over: object) -> dict[str, object]:
    """A calc task in English only, its text block changed by `over`."""
    return {"kind": "calc", "minutes": 10, "texts": {"en": texts("calc") | over}}


def one_chapter(*tasks: dict[str, object]) -> dict[str, object]:
    return {"chapters": [{"texts": {"de": {"title": "x"}}, "tasks": list(tasks)}]}


def pack(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "slug": "test-world",
        "texts": {"de": {"name": "Welt"}, "en": {"name": "World"}, "el": {"name": "Κόσμος"}},
        "chapters": [
            {
                "texts": {"de": {"title": "Eins"}, "en": {"title": "One"}, "el": {"title": "Ένα"}},
                "tasks": [task("draw"), task("calc"), task("write"), task("read")],
            }
        ],
    }
    base.update(over)
    return base


def test_a_full_pack_is_complete() -> None:
    p = Pack.model_validate(pack())
    assert pack_warnings(p.content()) == []
    assert p.age_band == "6-7"


def test_warnings_for_missing_translations_and_kinds() -> None:
    p = Pack.model_validate(
        pack(
            texts={"de": {"name": "Welt"}},
            chapters=[{"texts": {"en": {"title": "One"}}, "tasks": [task("draw", ("de",))]}],
        )
    )
    codes = {(w.code, w.chapter, w.kind, w.lang) for w in pack_warnings(p.content())}
    assert codes == {
        ("missing_translation", None, None, "en"),
        ("missing_translation", None, None, "el"),
        ("missing_translation", 1, None, "de"),
        ("missing_translation", 1, None, "el"),
        ("missing_kind", 1, "calc", None),
        ("missing_kind", 1, "write", None),
        ("missing_kind", 1, "read", None),
        ("missing_translation", 1, "draw", "en"),
        ("missing_translation", 1, "draw", "el"),
    }


def test_a_pack_without_greek_is_not_complete() -> None:
    """v0.12: `complete` needs all three languages (§13.5)."""
    p = Pack.model_validate(
        pack(
            texts={"de": {"name": "Welt"}, "en": {"name": "World"}},
            chapters=[
                {
                    "texts": {"de": {"title": "Eins"}, "en": {"title": "One"}},
                    "tasks": [task(k, ("de", "en")) for k in ("draw", "calc", "write", "read")],
                }
            ],
        )
    )
    codes = {(w.code, w.chapter, w.kind, w.lang) for w in pack_warnings(p.content())}
    assert codes == {
        ("missing_translation", None, None, "el"),
        ("missing_translation", 1, None, "el"),
        ("missing_translation", 1, "draw", "el"),
        ("missing_translation", 1, "calc", "el"),
        ("missing_translation", 1, "write", "el"),
        ("missing_translation", 1, "read", "el"),
    }


def test_a_greek_only_pack_is_valid() -> None:
    p = Pack.model_validate(
        pack(
            texts={"el": {"name": "Κόσμος"}},
            chapters=[{"texts": {"el": {"title": "Ένα"}}, "tasks": [task("draw", ("el",))]}],
        )
    )
    assert p.texts["el"].name == "Κόσμος"
    assert {w.lang for w in pack_warnings(p.content())} == {"de", "en", None}


@pytest.mark.parametrize(
    "over",
    [
        {"format": 2},
        {"slug": "Bad Slug"},
        {"age_band": "10-11"},
        {"color": "red"},
        {"chapters": []},
        {"texts": {"fr": {"name": "Monde"}}},
        {"chapters": [{"texts": {"de": {"title": "x"}}, "tasks": [task("draw"), task("draw")]}]},
        {"chapters": [{"texts": {"de": {"title": "x"}}, "tasks": [task("draw") | {"minutes": 0}]}]},
        {"chapters": [{"texts": {"de": {"title": "x" * 81}}, "tasks": [task("draw")]}]},
        {"unknown": True},
        one_chapter(*[task(k) for k in ("draw", "calc", "write", "read", "draw")]),
        one_chapter(task("paint")),
        # Level texts of draw/calc/write: at most 600 (§14.1).
        one_chapter(task("draw") | {"texts": {"en": texts("draw") | {"easier": "x" * 601}}}),
        one_chapter(task("calc") | {"texts": {"en": texts("calc") | {"text": "x" * 601}}}),
        # Only read tasks have questions.
        one_chapter(task("write") | {"texts": {"en": texts("read") | {"materials": []}}}),
        # Read tasks: reading texts at most 900, 1-2 questions, 3 distinct answers, correct 0-2.
        one_chapter(read(text="x" * 901)),
        one_chapter(read(harder="x" * 901)),
        one_chapter(read(questions={"normal": []})),
        one_chapter(read(questions={"normal": [question()] * 3})),
        one_chapter(read(questions={"hard": [question()]})),
        one_chapter(read(questions={"normal": [question(correct=3)]})),
        one_chapter(read(questions={"normal": [question("x" * 141)]})),
        one_chapter(read(questions={"normal": [question() | {"answers": ["a", "b"]}]})),
        one_chapter(read(questions={"normal": [question() | {"answers": ["a", "b", "c", "d"]}]})),
        one_chapter(read(questions={"normal": [question() | {"answers": ["a", "a", "b"]}]})),
        one_chapter(read(questions={"normal": [question() | {"answers": ["a", "b", "x" * 61]}]})),
        one_chapter(read(questions={"normal": [question() | {"hint": "no"}]})),
        # Problems (§15.2): calc only, 1-6 per level, text 1-300, answer an integer 0-999,
        # a hint of 1-140.
        one_chapter(read(problems={"normal": [problem()]})),
        one_chapter(task("draw") | {"texts": {"en": texts("draw") | {"problems": {}}}}),
        one_chapter(calc(problems={"normal": []})),
        one_chapter(calc(problems={"normal": [problem()] * 7})),
        one_chapter(calc(problems={"hard": [problem()]})),
        one_chapter(calc(problems={"normal": [problem("")]})),
        one_chapter(calc(problems={"normal": [problem("x" * 301)]})),
        one_chapter(calc(problems={"normal": [problem(answer=-1)]})),
        one_chapter(calc(problems={"normal": [problem(answer=1000)]})),
        one_chapter(calc(problems={"normal": [problem() | {"answer": "5"}]})),
        one_chapter(calc(problems={"normal": [problem() | {"answer": 2.5}]})),
        one_chapter(calc(problems={"normal": [problem() | {"hint": ""}]})),
        one_chapter(calc(problems={"normal": [problem() | {"hint": "x" * 141}]})),
        one_chapter(calc(problems={"normal": [{"text": "1 + 1?", "answer": 2}]})),
        one_chapter(calc(problems={"normal": [problem() | {"say": {"text": "x"}}]})),
        # Spoken forms: known fields of the block only, at most 900.
        one_chapter(read(say={"text": "x" * 901})),
        one_chapter(read(say={"intro": "Hello"})),
        {"texts": {"en": {"name": "World", "say": {"title": "x"}}}},
        {
            "chapters": [
                {"texts": {"de": {"title": "x", "say": {"name": "x"}}}, "tasks": [task("draw")]}
            ]
        },
    ],
)
def test_invalid_packs_are_rejected(over: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Pack.model_validate(pack(**over))


def test_four_kinds_with_level_texts_and_questions_are_complete() -> None:
    p = Pack.model_validate(pack())
    assert [t.kind for t in p.chapters[0].tasks] == ["draw", "calc", "write", "read"]
    assert pack_warnings(p.content()) == []


def test_read_tasks_take_long_texts_no_materials_and_up_to_two_questions() -> None:
    p = Pack.model_validate(
        pack(
            **one_chapter(
                read(
                    text="x" * 900,
                    easier="y" * 900,
                    harder="z" * 900,
                    questions={"normal": [question(), question("Where?", 2)]},
                )
            )
        )
    )
    t = p.chapters[0].tasks[0].texts["en"]
    assert t.materials == []
    assert len(t.level_questions("normal")) == 2
    # Draw, calc and write may have no materials too.
    Pack.model_validate(
        pack(**one_chapter(task("draw") | {"texts": {"en": texts("draw") | {"materials": []}}}))
    )


def test_level_text_falls_back_to_text() -> None:
    t = TaskTexts(title="T", text="normal", easier="easy")
    assert t.level_text("normal") == "normal"
    assert t.level_text("easier") == "easy"
    assert t.level_text("harder") == "normal"


def test_level_questions_fall_back_to_normal() -> None:
    q = {"question": "Who?", "answers": ["a", "b", "c"], "correct": 0}
    t = TaskTexts.model_validate(
        {
            "title": "T",
            "text": "x",
            "questions": {"normal": [q], "harder": [q | {"question": "Why?"}]},
        }
    )
    assert [x.question for x in t.level_questions("easier")] == ["Who?"]
    assert [x.question for x in t.level_questions("harder")] == ["Why?"]
    assert TaskTexts(title="T", text="x").level_questions("normal") == []
    only_easier = TaskTexts.model_validate(
        {"title": "T", "text": "x", "questions": {"easier": [q]}}
    )
    assert only_easier.level_questions("harder") == []


def test_warnings_for_missing_levels_and_questions() -> None:
    draw: dict[str, Any] = task("draw")
    draw["texts"]["de"]["harder"] = ""
    reading: dict[str, Any] = task("read")
    del reading["texts"]["en"]["easier"]
    reading["texts"]["el"]["questions"] = {"normal": [question()]}
    del reading["texts"]["de"]["questions"]
    chapters = [
        {
            "texts": {"de": {"title": "Eins"}, "en": {"title": "One"}, "el": {"title": "Ένα"}},
            "tasks": [draw, task("calc"), task("write"), reading],
        }
    ]
    p = Pack.model_validate(pack(chapters=chapters))
    found = {(w.code, w.chapter, w.kind, w.lang, w.level) for w in pack_warnings(p.content())}
    assert found == {
        ("missing_level", 1, "draw", "de", "harder"),
        ("missing_level", 1, "read", "en", "easier"),
        ("missing_questions", 1, "read", "el", "easier"),
        ("missing_questions", 1, "read", "el", "harder"),
        ("missing_questions", 1, "read", "de", "easier"),
        ("missing_questions", 1, "read", "de", "normal"),
        ("missing_questions", 1, "read", "de", "harder"),
    }


def test_spoken_forms_are_kept_and_empty_ones_dropped() -> None:
    body = pack()
    body["texts"] = {
        "de": {"name": "Welt", "say": {"name": "Wellt", "guide": ""}},
        "en": {"name": "World", "say": {"guide": ""}},
        "el": {"name": "Κόσμος"},
    }
    chapters: Any = body["chapters"]
    chapters[0]["texts"]["en"]["say"] = {"intro": "In-tro", "outro": "x" * 900}
    chapters[0]["tasks"][3]["texts"]["en"]["say"] = {"text": "Spoken", "harder": "Harder"}
    p = Pack.model_validate(body)
    assert p.texts["de"].say == {"name": "Wellt"}
    assert p.texts["en"].say is None
    assert p.chapters[0].texts["en"].say == {"intro": "In-tro", "outro": "x" * 900}
    assert p.chapters[0].tasks[3].texts["en"].say == {"text": "Spoken", "harder": "Harder"}
    assert pack_warnings(p.content()) == []


# ---- opening times (§14.4a) -------------------------------------------------------------------

# The §14.4a example: Mon-Fri 15:00-18:30, Sat-Sun 09:00-18:00. Tue = 3 Nov 2026.
WINDOWS = [
    Window.model_validate({"days": [0, 1, 2, 3, 4], "from": "15:00", "to": "18:30"}),
    Window.model_validate({"days": [5, 6], "from": "09:00", "to": "18:00"}),
]


@pytest.mark.parametrize(
    ("now", "open_now", "opens_at"),
    [
        (at(3, 7, 30), False, at(3, 15, 0)),  # Tue 07:30 -> Tue 15:00
        (at(3, 15, 0), True, None),  # from is inclusive
        (at(3, 16, 0), True, None),
        (at(3, 18, 30), False, at(4, 15, 0)),  # to is exclusive
        (at(6, 19, 0), False, at(7, 9, 0)),  # Fri 19:00 -> Sat 09:00
        (at(8, 18, 10), False, at(9, 15, 0)),  # Sun 18:10 -> Mon 15:00
    ],
)
def test_open_state(now: datetime, open_now: bool, opens_at: datetime | None) -> None:
    assert open_state(WINDOWS, now) == (open_now, opens_at)


@pytest.mark.parametrize(
    ("now", "allowed"),
    [
        (at(3, 18, 20), True),  # open
        (at(3, 18, 40), True),  # a task opened at 18:20, done 10 min after closing
        (at(3, 18, 44), True),
        (datetime(2026, 11, 3, 18, 44, 59, tzinfo=BERLIN), True),
        (at(3, 18, 45), False),  # to + 15 min is exclusive
        (at(3, 18, 50), False),
        (at(3, 14, 59), False),  # before opening there is no grace
        (at(7, 18, 10), True),  # Sat window ends 18:00
    ],
)
def test_done_allowed(now: datetime, allowed: bool) -> None:
    assert done_allowed(WINDOWS, now) is allowed


def test_no_windows_is_always_open() -> None:
    assert open_state([], at(3, 3, 0)) == (True, None)
    assert done_allowed([], at(3, 3, 0))


def test_done_grace_crosses_midnight() -> None:
    late = [Window.model_validate({"days": [1], "from": "20:00", "to": "23:55"})]
    assert done_allowed(late, at(4, 0, 9))  # Wed 00:09, 14 min after Tue 23:55
    assert not done_allowed(late, at(4, 0, 10))


def test_opens_at_a_week_later() -> None:
    tue = [Window.model_validate({"days": [1], "from": "15:00", "to": "16:00"})]
    assert open_state(tue, at(3, 16, 0)) == (False, at(10, 15, 0))


def test_window_round_trip_uses_from() -> None:
    w = Window.model_validate({"days": [3, 1], "from": "15:00", "to": "18:30"})
    assert w.model_dump(by_alias=True) == {"days": [1, 3], "from": "15:00", "to": "18:30"}


@pytest.mark.parametrize(
    "raw",
    [
        {"days": [], "from": "15:00", "to": "18:30"},
        {"days": [7], "from": "15:00", "to": "18:30"},
        {"days": [-1], "from": "15:00", "to": "18:30"},
        {"days": [1, 1], "from": "15:00", "to": "18:30"},
        {"days": [1], "from": "18:30", "to": "18:30"},
        {"days": [1], "from": "19:00", "to": "18:30"},
        {"days": [1], "from": "15:00", "to": "24:00"},
        {"days": [1], "from": "9:00", "to": "18:30"},
        {"days": [1], "from": "15:00"},
        {"days": [1], "from": "15:00", "to": "18:30", "note": "x"},
    ],
)
def test_invalid_windows(raw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Window.model_validate(raw)


# ---- maths problems (§15.2) and materials (§15.5) ---------------------------------------


def test_calc_problems_are_kept_per_level() -> None:
    p = Pack.model_validate(
        pack(**one_chapter(calc(problems={"normal": [problem(), problem("x" * 300, 999)]})))
    )
    t = p.chapters[0].tasks[0].texts["en"]
    assert [x.answer for x in t.level_problems("normal")] == [5, 999]
    assert t.level_problems("normal")[0].hint == "Draw the shells and count them."
    # Six problems and the answer 0 are fine.
    Pack.model_validate(pack(**one_chapter(calc(problems={"easier": [problem(answer=0)] * 6}))))


def test_level_problems_fall_back_to_normal() -> None:
    t = TaskTexts.model_validate(
        {
            "title": "T",
            "text": "x",
            "problems": {"normal": [problem(answer=1)], "harder": [problem(answer=2)]},
        }
    )
    assert [x.answer for x in t.level_problems("easier")] == [1]
    assert [x.answer for x in t.level_problems("harder")] == [2]
    assert TaskTexts(title="T", text="x").level_problems("normal") == []
    only_easier = TaskTexts.model_validate(
        {"title": "T", "text": "x", "problems": {"easier": [problem()]}}
    )
    assert only_easier.level_problems("harder") == []


@pytest.mark.parametrize("kind", ["draw", "write", "read"])
def test_only_calc_tasks_have_problems(kind: str) -> None:
    block = texts(kind) | {"problems": {"normal": [problem()]}}
    with pytest.raises(ValidationError, match="only calc tasks have problems"):
        Pack.model_validate(
            pack(**one_chapter({"kind": kind, "minutes": 5, "texts": {"en": block}}))
        )


def test_warnings_for_missing_and_mismatched_problems() -> None:
    calc_task: dict[str, Any] = task("calc")
    del calc_task["texts"]["de"]["problems"]["harder"]  # missing in German
    calc_task["texts"]["el"]["problems"]["normal"][1]["answer"] = 6  # another answer
    calc_task["texts"]["el"]["problems"]["easier"].pop()  # fewer problems
    calc_task["texts"]["en"]["problems"]["normal"][0]["text"] = "Other words, same answer"
    chapters = [
        {
            "texts": {"de": {"title": "Eins"}, "en": {"title": "One"}, "el": {"title": "Ένα"}},
            "tasks": [task("draw"), calc_task, task("write"), task("read")],
        }
    ]
    warnings = pack_warnings(Pack.model_validate(pack(chapters=chapters)).content())
    found = {(w.code, w.chapter, w.kind, w.lang, w.level) for w in warnings}
    assert found == {
        ("missing_problems", 1, "calc", "de", "harder"),
        ("problems_mismatch", 1, "calc", "el", "normal"),
        ("problems_mismatch", 1, "calc", "el", "easier"),
    }
    assert not is_complete(warnings)


def test_a_calc_task_without_problems_is_not_complete() -> None:
    calc_task: dict[str, Any] = task("calc")
    for block in calc_task["texts"].values():
        del block["problems"]
    chapters = [
        {
            "texts": {"de": {"title": "Eins"}, "en": {"title": "One"}, "el": {"title": "Ένα"}},
            "tasks": [task("draw"), calc_task, task("write"), task("read")],
        }
    ]
    warnings = pack_warnings(Pack.model_validate(pack(chapters=chapters)).content())
    assert {(w.code, w.lang, w.level) for w in warnings} == {
        ("missing_problems", lang, level)
        for lang in ("de", "en", "el")
        for level in ("easier", "normal", "harder")
    }
    assert not is_complete(warnings)


def test_materials_off_the_simple_list_warn_but_stay_complete() -> None:
    draw: dict[str, Any] = task("draw")
    draw["texts"]["en"]["materials"] = ["Pencil", "coloured pencils ", "Buttons"]
    draw["texts"]["de"]["materials"] = ["Paper"]  # English, not German
    draw["texts"]["el"]["materials"] = ["Ξυλομπογιές", "Ψαλίδι", "Κόλλα"]
    chapters = [
        {
            "texts": {"de": {"title": "Eins"}, "en": {"title": "One"}, "el": {"title": "Ένα"}},
            "tasks": [draw, task("calc"), task("write"), task("read")],
        }
    ]
    warnings = pack_warnings(Pack.model_validate(pack(chapters=chapters)).content())
    assert {(w.code, w.chapter, w.kind, w.lang, w.material) for w in warnings} == {
        ("unlisted_material", 1, "draw", "en", "Buttons"),
        ("unlisted_material", 1, "draw", "de", "Paper"),
    }
    assert is_complete(warnings)  # custom packs may use other materials (§15.5)


def test_the_simple_materials() -> None:
    assert SIMPLE_MATERIALS["en"] == ("Paper", "Pencil", "Coloured pencils", "Scissors", "Glue")
    assert SIMPLE_MATERIALS["de"] == ("Papier", "Bleistift", "Buntstifte", "Schere", "Kleber")
    assert SIMPLE_MATERIALS["el"] == ("Χαρτί", "Μολύβι", "Ξυλομπογιές", "Ψαλίδι", "Κόλλα")
    assert is_simple_material("de", " buntstifte")
    assert not is_simple_material("de", "Coloured pencils")
