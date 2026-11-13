"""Activities (SPEC §13, §14, §15): the schedule over activity days, and the story-pack format.

Pure: no I/O and no clock. The routes load settings and content, call these functions
with the local time, and persist the housekeeping result (§13.2 rule 5).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Kind = Literal["draw", "calc", "write", "read"]
Lang = Literal["de", "en", "el"]
AgeBand = Literal["4-5", "6-7", "8-9"]
Level = Literal["easier", "normal", "harder"]

KINDS: tuple[Kind, ...] = ("draw", "calc", "write", "read")
LANGS: tuple[Lang, ...] = ("de", "en", "el")
LEVELS: tuple[Level, ...] = ("easier", "normal", "harder")
DEFAULT_LEVEL: Level = "normal"
HORIZON_DAYS = 400  # chapters further out than this get no date
DEFAULT_DAYS = frozenset(range(6))  # Mon-Sat
DEFAULT_UNLOCK = time(7, 0)


# ---- story language (§14.7) ---------------------------------------------------------------

STORY_LANG_ORDER: tuple[Lang, ...] = ("en", "de", "el")  # after DEFAULT_LOCALE


def story_lang(chosen: object, available: Iterable[str], default: Lang) -> Lang:
    """The language a queued world is told in (§14.7): the parent's choice if the world has
    it, else `default` (DEFAULT_LOCALE) if it has that, else English, German, Greek, the
    first it has. `available` = the languages of the world's own texts."""
    have = set(available)
    for lang in (chosen, default, *STORY_LANG_ORDER):
        if lang in LANGS and lang in have:
            return lang
    return default


def load_langs(value: object) -> dict[int, Lang]:
    """`activity_settings.langs` as {world id: lang}; anything unreadable is dropped."""
    out: dict[int, Lang] = {}
    if isinstance(value, Mapping):
        for key, lang in value.items():
            try:
                world_id = int(key)
            except (TypeError, ValueError):
                continue
            if lang in LANGS:
                out[world_id] = lang
    return out


def merge_langs(
    stored: Mapping[int, Lang], changes: Mapping[int, Lang], queue: Sequence[int]
) -> dict[str, Lang]:
    """The langs to store for `queue`: the stored ones updated by `changes`, only for queued
    worlds (a world leaving the queue loses its entry, §14.7). JSON keys are strings."""
    merged = {**stored, **changes}
    return {str(i): merged[i] for i in queue if i in merged}


# ---- schedule (§13.2) ---------------------------------------------------------------------


@dataclass(frozen=True)
class DayOff:
    start: date
    end: date  # inclusive

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True)
class WorldSpan:
    """One queued world and the dates of its chapters (None = past the horizon)."""

    world_id: int
    dates: tuple[date | None, ...]

    @property
    def first(self) -> date | None:
        return self.dates[0] if self.dates else None

    @property
    def last(self) -> date | None:
        return self.dates[-1] if self.dates else None


@dataclass(frozen=True)
class Chapter:
    position: int  # 1-based
    date: date | None
    unlocked: bool


@dataclass(frozen=True)
class Schedule:
    queue: tuple[int, ...]  # after housekeeping
    queue_start: date | None  # after housekeeping
    spans: tuple[WorldSpan, ...]  # one per queue entry, in order
    current: int | None  # world id shown on the tablet
    chapters: tuple[Chapter, ...] = field(default=())  # of the current world
    next_unlock_at: datetime | None = None
    today_is_activity_day: bool = False


def is_activity_day(day: date, days: frozenset[int], days_off: Sequence[DayOff]) -> bool:
    return day.weekday() in days and not any(o.covers(day) for o in days_off)


def activity_days(
    start: date, count: int, days: frozenset[int], days_off: Sequence[DayOff]
) -> list[date]:
    """The first `count` activity days from `start` (inclusive), within the horizon."""
    out: list[date] = []
    if not days:
        return out
    day = start
    end = start + timedelta(days=HORIZON_DAYS)
    while len(out) < count and day < end:
        if is_activity_day(day, days, days_off):
            out.append(day)
        day += timedelta(days=1)
    return out


def lay_out(
    queue: Sequence[tuple[int, int]],
    days: frozenset[int],
    days_off: Sequence[DayOff],
    queue_start: date | None,
) -> list[WorldSpan]:
    """Chapters over the activity days in queue order (rule 2). queue = (world id, chapters)."""
    total = sum(n for _, n in queue)
    dates: list[date | None] = []
    if queue_start is not None:
        dates.extend(activity_days(queue_start, total, days, days_off))
    dates.extend([None] * (total - len(dates)))
    spans: list[WorldSpan] = []
    i = 0
    for world_id, n in queue:
        spans.append(WorldSpan(world_id, tuple(dates[i : i + n])))
        i += n
    return spans


def schedule(
    queue: Sequence[tuple[int, int]],
    days: frozenset[int],
    days_off: Sequence[DayOff],
    queue_start: date | None,
    now: datetime,
    unlock_time: time = DEFAULT_UNLOCK,
) -> Schedule:
    """Rules 1-5 and 8 of §13.2. `now` is the local, timezone-aware time."""
    today = now.date()
    today_active = is_activity_day(today, days, days_off)
    spans = lay_out(queue, days, days_off, queue_start)
    current_index: int | None = None
    for i, span in enumerate(spans):
        if span.first is not None and span.first <= today:
            current_index = i
    queue_ids = tuple(world_id for world_id, _ in queue)
    if current_index is None:
        return Schedule(queue_ids, queue_start, tuple(spans), None, (), None, today_active)
    # Rule 5: housekeeping drops the worlds before the current one.
    spans = spans[current_index:]
    queue_ids = queue_ids[current_index:]
    queue_start = spans[0].first
    span = spans[0]
    chapters: list[Chapter] = []
    next_unlock: datetime | None = None
    for pos, day in enumerate(span.dates, start=1):
        unlocked = day is not None and (day < today or (day == today and now.time() >= unlock_time))
        chapters.append(Chapter(pos, day, unlocked))
        if not unlocked and day is not None and next_unlock is None:
            next_unlock = datetime.combine(day, unlock_time, tzinfo=now.tzinfo)
    return Schedule(
        queue_ids,
        queue_start,
        tuple(spans),
        span.world_id,
        tuple(chapters),
        next_unlock,
        today_active,
    )


def queue_start_after_change(
    old_queue: Sequence[int], new_queue: Sequence[int], old_start: date | None, today: date
) -> date | None:
    """Rule 7: a new first world starts fresh today; reordering later entries keeps the start.

    `old_queue` is the queue after housekeeping, so its head is the current (or the
    upcoming) world.
    """
    if not new_queue:
        return None
    if old_start is not None and old_queue and old_queue[0] == new_queue[0]:
        return old_start
    return today


def parse_days(text: str) -> frozenset[int]:
    return frozenset(int(c) for c in text if c.isdigit() and int(c) < 7)


def format_days(days: Iterable[int]) -> str:
    return "".join(str(d) for d in sorted(set(days)))


# ---- opening times (§14.4a) ---------------------------------------------------------------

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
MAX_WINDOWS = 14
DONE_GRACE = timedelta(minutes=15)  # "I'm done!" is accepted until `to` + this (exclusive)
OPENS_AHEAD_DAYS = 8  # how far `open_state` looks for the next opening


def _clock_time(text: str) -> time:
    hh, _, mm = text.partition(":")
    return time(int(hh), int(mm))


class Window(BaseModel):
    """An opening window for Ideas: on `days` (Mon=0) from `from` to `to`, same day; `to` is
    exclusive."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    days: list[int] = Field(min_length=1, max_length=7)
    from_: str = Field(alias="from", pattern=TIME_PATTERN)
    to: str = Field(pattern=TIME_PATTERN)

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("days are 0-6 (Mon=0)")
        if len(set(days)) != len(days):
            raise ValueError("each day at most once")
        return sorted(days)

    @model_validator(mode="after")
    def _from_before_to(self) -> "Window":
        if self.from_ >= self.to:  # "HH:MM" compares like the time
            raise ValueError("from must be before to")
        return self

    @property
    def start(self) -> time:
        return _clock_time(self.from_)

    @property
    def end(self) -> time:
        return _clock_time(self.to)


def is_open(windows: Sequence[Window], now: datetime) -> bool:
    """Inside a window of today's weekday; no windows = always open. `now` is local time."""
    if not windows:
        return True
    t = now.time()
    return any(now.weekday() in w.days and w.start <= t < w.end for w in windows)


def open_state(windows: Sequence[Window], now: datetime) -> tuple[bool, datetime | None]:
    """(open now, next opening). The next opening is a local datetime in `now`'s zone, None
    when open or when no window opens within `OPENS_AHEAD_DAYS`."""
    if is_open(windows, now):
        return True, None
    today = now.date()
    for offset in range(OPENS_AHEAD_DAYS + 1):
        day = today + timedelta(days=offset)
        starts = [
            datetime.combine(day, w.start, tzinfo=now.tzinfo)
            for w in windows
            if day.weekday() in w.days
        ]
        later = [s for s in starts if s > now]
        if later:
            return False, min(later)
    return False, None


def done_allowed(windows: Sequence[Window], now: datetime) -> bool:
    """Open now, or a window closed less than `DONE_GRACE` ago (§14.4a): a window ending at
    18:30 accepts "I'm done!" until 18:44:59. A window ending late in the evening still counts
    just after midnight."""
    if is_open(windows, now):
        return True
    today = now.date()
    for day in (today, today - timedelta(days=1)):
        for w in windows:
            if day.weekday() not in w.days:
                continue
            end = datetime.combine(day, w.end, tzinfo=now.tzinfo)
            if end <= now < end + DONE_GRACE:
                return True
    return False


def load_windows(data: Iterable[Any] | None) -> list[Window]:
    """`activity_settings.windows` -> the windows."""
    return [Window.model_validate(w) for w in data or []]


# ---- pack format (§13.5) ------------------------------------------------------------------

SLUG_PATTERN = r"^[a-z0-9-]{1,40}$"
COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


# Output-only keys of the admin's text blocks (§14.3): ignored on input, so the editor can send
# a block back as it got it.
OUTPUT_ONLY = ("audio", "question_audio", "problem_audio")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    keeps_audio: ClassVar[bool] = False  # True in the admin's output models

    @model_validator(mode="before")
    @classmethod
    def _drop_output_only(cls, data: Any) -> Any:
        if cls.keeps_audio or not isinstance(data, dict):
            return data
        return {k: v for k, v in data.items() if k not in OUTPUT_ONLY}


SayText = Annotated[str, Field(max_length=900)]


def _drop_empty(say: dict[str, str] | None) -> dict[str, str] | None:
    """An empty spoken form means none; no entries at all means no `say`."""
    if say is None:
        return None
    kept = {k: v for k, v in say.items() if v}
    return kept or None


class WorldTexts(_Strict):
    name: str = Field(min_length=1, max_length=80)
    guide: str = Field(default="", max_length=80)
    # §14.3: the spoken form of a field, for audio only; never sent to the tablet.
    say: dict[Literal["name", "guide"], SayText] | None = None

    _say = field_validator("say")(_drop_empty)


class ChapterTexts(_Strict):
    title: str = Field(min_length=1, max_length=80)
    intro: str = Field(default="", max_length=600)
    outro: str = Field(default="", max_length=600)
    say: dict[Literal["title", "intro", "outro"], SayText] | None = None

    _say = field_validator("say")(_drop_empty)


Material = Annotated[str, Field(min_length=1, max_length=30)]
Answer = Annotated[str, Field(min_length=1, max_length=60)]
TEXT_MAX = 600  # text / easier / harder of draw, calc, write (§13.5, §14.1)
READ_TEXT_MAX = 900  # the reading texts of a read task (§14.2)


class Question(_Strict):
    """A multiple-choice question of a read task (§14.2)."""

    question: str = Field(min_length=1, max_length=140)
    answers: list[Answer] = Field(min_length=3, max_length=3)
    correct: int = Field(ge=0, le=2)

    @field_validator("answers")
    @classmethod
    def _distinct_answers(cls, answers: list[str]) -> list[str]:
        if len(set(answers)) != len(answers):
            raise ValueError("the three answers must differ")
        return answers


Questions = Annotated[list[Question], Field(min_length=1, max_length=2)]


class Problem(_Strict):
    """A small story problem of a calc task (§15.2): the child types `answer`; `hint` says how
    to work it out with paper and pencil (never the answer)."""

    text: str = Field(min_length=1, max_length=300)
    answer: int = Field(ge=0, le=999, strict=True)
    hint: str = Field(min_length=1, max_length=140)


Problems = Annotated[list[Problem], Field(min_length=1, max_length=6)]


class TaskTexts(_Strict):
    """A task's text block. `text` is the normal level; `easier` and `harder` are full texts
    for those levels (§14.1), empty = falls back to `text`. Length limits per kind are checked
    by `PackTask`."""

    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=READ_TEXT_MAX)
    materials: list[Material] = Field(default_factory=list, max_length=6)
    easier: str = Field(default="", max_length=READ_TEXT_MAX)
    harder: str = Field(default="", max_length=READ_TEXT_MAX)
    questions: dict[Level, Questions] | None = None  # read tasks only (§14.2)
    problems: dict[Level, Problems] | None = None  # calc tasks only (§15.2)
    say: dict[Literal["title", "text", "easier", "harder"], SayText] | None = None

    _say = field_validator("say")(_drop_empty)

    def level_text(self, level: Level) -> str:
        """The text for a level; a missing level text falls back to `text` (§14.1)."""
        if level == "easier" and self.easier:
            return self.easier
        if level == "harder" and self.harder:
            return self.harder
        return self.text

    def level_questions(self, level: Level) -> list[Question]:
        """The questions for a level; a missing level falls back to `normal` (§14.2)."""
        if not self.questions:
            return []
        return self.questions.get(level) or self.questions.get(DEFAULT_LEVEL) or []

    def level_problems(self, level: Level) -> list[Problem]:
        """The problems for a level; a missing level falls back to `normal` (§15.2)."""
        if not self.problems:
            return []
        return self.problems.get(level) or self.problems.get(DEFAULT_LEVEL) or []


class PackTask(_Strict):
    kind: Kind
    minutes: int = Field(ge=1, le=60)
    texts: dict[Lang, TaskTexts] = Field(min_length=1)

    @model_validator(mode="after")
    def _limits_per_kind(self) -> "PackTask":
        if self.kind != "calc":
            for lang, t in self.texts.items():
                if t.problems is not None:
                    raise ValueError(f"texts.{lang}.problems: only calc tasks have problems")
        if self.kind == "read":
            return self
        for lang, t in self.texts.items():
            for name in ("text", "easier", "harder"):
                if len(getattr(t, name)) > TEXT_MAX:
                    raise ValueError(f"texts.{lang}.{name}: at most {TEXT_MAX} characters")
            if t.questions is not None:
                raise ValueError(f"texts.{lang}.questions: only read tasks have questions")
        return self


PictureName = Annotated[str, Field(min_length=1, max_length=200)]  # a file next to world.json


class PackChapter(_Strict):
    image: PictureName | None = None
    texts: dict[Lang, ChapterTexts] = Field(min_length=1)
    tasks: list[PackTask] = Field(min_length=1, max_length=4)

    @field_validator("tasks")
    @classmethod
    def _distinct_kinds(cls, tasks: list[PackTask]) -> list[PackTask]:
        kinds = [t.kind for t in tasks]
        if len(set(kinds)) != len(kinds):
            raise ValueError("each kind at most once per chapter")
        return tasks


class WorldContent(_Strict):
    """What `worlds.content` stores: the world's texts and its chapters."""

    texts: dict[Lang, WorldTexts] = Field(min_length=1)
    chapters: list[PackChapter] = Field(min_length=1, max_length=12)


class Pack(WorldContent):
    """A story pack, `world.json` (§13.5)."""

    format: Literal[1] = 1
    slug: str = Field(pattern=SLUG_PATTERN)
    age_band: AgeBand = "6-7"
    color: str = Field(default="#4552C7", pattern=COLOR_PATTERN)
    cover: PictureName | None = None

    def content(self) -> WorldContent:
        return WorldContent(texts=self.texts, chapters=self.chapters)


# ---- stored content (§13.6) ---------------------------------------------------------------


@dataclass(frozen=True)
class Picture:
    """A stored picture: a thumbnail key (§6.4) and its aspect ratio."""

    key: str
    aspect: float


def stored_content(content: WorldContent, pictures: Sequence[Picture | None]) -> dict[str, Any]:
    """`worlds.content` as JSON: the content without picture file names, each chapter with
    its stored picture (`image_key`, `image_aspect`; server-side only)."""
    data = content.model_dump(mode="json")
    for i, chapter in enumerate(data["chapters"]):
        chapter.pop("image", None)
        picture = pictures[i] if i < len(pictures) else None
        if picture is not None:
            chapter["image_key"] = picture.key
            chapter["image_aspect"] = picture.aspect
    return data


def load_content(data: Mapping[str, Any]) -> tuple[WorldContent, list[Picture | None]]:
    """`worlds.content` -> the validated content and the chapter pictures, by position."""
    chapters: list[dict[str, Any]] = []
    pictures: list[Picture | None] = []
    for raw in data.get("chapters") or []:
        chapter = dict(raw)
        key = chapter.pop("image_key", None)
        aspect = chapter.pop("image_aspect", None)
        pictures.append(Picture(str(key), float(aspect or 1.0)) if key else None)
        chapters.append(chapter)
    return WorldContent.model_validate({**data, "chapters": chapters}), pictures


# §15.5: the simple materials, per language. Bundled packs use only these; custom packs may use
# others, which the admin is warned about (`unlisted_material`).
SIMPLE_MATERIALS: dict[Lang, tuple[str, ...]] = {
    "en": ("Paper", "Pencil", "Coloured pencils", "Scissors", "Glue"),
    "de": ("Papier", "Bleistift", "Buntstifte", "Schere", "Kleber"),
    "el": ("Χαρτί", "Μολύβι", "Ξυλομπογιές", "Ψαλίδι", "Κόλλα"),
}


def is_simple_material(lang: Lang, material: str) -> bool:
    """On the §15.5 list of the language (case and surrounding spaces ignored)."""
    wanted = material.strip().casefold()
    return any(m.casefold() == wanted for m in SIMPLE_MATERIALS[lang])


WarningCode = Literal[
    "missing_translation",
    "missing_kind",
    "missing_level",
    "missing_questions",
    "missing_problems",
    "problems_mismatch",
    "unlisted_material",
]
# Warnings that don't make a world incomplete: custom packs may use other materials (§15.5).
ADVISORY_WARNINGS: frozenset[str] = frozenset({"unlisted_material"})


class PackWarning(BaseModel):
    code: WarningCode
    chapter: int | None = None  # 1-based; None = the world's own texts
    kind: Kind | None = None
    lang: Lang | None = None
    # missing_level: easier/harder; missing_questions, missing_problems, problems_mismatch: any
    level: Level | None = None
    material: str | None = None  # unlisted_material: the material as written


def is_complete(warnings: Iterable[PackWarning]) -> bool:
    """Complete (§13.4, §14.2, §15.2): no warnings except advisory ones."""
    return all(w.code in ADVISORY_WARNINGS for w in warnings)


def _problem_warnings(pos: int, task: PackTask) -> list[PackWarning]:
    """A calc task's problems (§15.2): every level in every language, and per level the same
    number of problems with the same answers across languages. The first language (LANGS
    order) that has a level is the reference; every other one that differs is reported."""
    out: list[PackWarning] = []
    for level in LEVELS:
        reference: list[int] | None = None
        for lang in LANGS:
            t = task.texts.get(lang)
            if t is None:
                continue  # reported as missing_translation
            problems = (t.problems or {}).get(level)
            if not problems:
                out.append(
                    PackWarning(
                        code="missing_problems", chapter=pos, kind=task.kind, lang=lang, level=level
                    )
                )
                continue
            answers = [p.answer for p in problems]
            if reference is None:
                reference = answers
            elif answers != reference:
                out.append(
                    PackWarning(
                        code="problems_mismatch",
                        chapter=pos,
                        kind=task.kind,
                        lang=lang,
                        level=level,
                    )
                )
    return out


def pack_warnings(content: WorldContent) -> list[PackWarning]:
    """Missing translations, kinds, level texts, questions (§14.2) and problems (§15.2),
    problems that differ between languages, and materials not on the simple list (§15.5).
    A world whose warnings are all advisory is complete (`is_complete`)."""
    out: list[PackWarning] = [
        PackWarning(code="missing_translation", lang=lang)
        for lang in LANGS
        if lang not in content.texts
    ]
    for pos, chapter in enumerate(content.chapters, start=1):
        out.extend(
            PackWarning(code="missing_translation", chapter=pos, lang=lang)
            for lang in LANGS
            if lang not in chapter.texts
        )
        present = {t.kind for t in chapter.tasks}
        out.extend(
            PackWarning(code="missing_kind", chapter=pos, kind=kind)
            for kind in KINDS
            if kind not in present
        )
        for task in chapter.tasks:
            out.extend(
                PackWarning(code="missing_translation", chapter=pos, kind=task.kind, lang=lang)
                for lang in LANGS
                if lang not in task.texts
            )
            for lang, t in task.texts.items():
                out.extend(
                    PackWarning(
                        code="missing_level", chapter=pos, kind=task.kind, lang=lang, level=level
                    )
                    for level in ("easier", "harder")
                    if not getattr(t, level)
                )
                if task.kind == "read":
                    out.extend(
                        PackWarning(
                            code="missing_questions",
                            chapter=pos,
                            kind=task.kind,
                            lang=lang,
                            level=level,
                        )
                        for level in LEVELS
                        if not (t.questions or {}).get(level)
                    )
                out.extend(
                    PackWarning(
                        code="unlisted_material",
                        chapter=pos,
                        kind=task.kind,
                        lang=lang,
                        material=material,
                    )
                    for material in t.materials
                    if not is_simple_material(lang, material)
                )
            if task.kind == "calc":
                out.extend(_problem_warnings(pos, task))
    return out


def task_count(content: WorldContent) -> int:
    return sum(len(c.tasks) for c in content.chapters)
