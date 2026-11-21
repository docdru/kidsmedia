"""Device: activities, the off-screen ideas (SPEC §13.3, §14, §15). Shared loaders for the
admin too."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import Connection, Row, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from starlette.concurrency import run_in_threadpool

from kidsmedia import photos, thumbs
from kidsmedia.activities import (
    DEFAULT_LEVEL,
    KINDS,
    ChapterTexts,
    Kind,
    Lang,
    Level,
    PackTask,
    Picture,
    PlannedWorld,
    Progress,
    Schedule,
    TaskTexts,
    Window,
    WorldContent,
    WorldTexts,
    done_allowed,
    load_content,
    load_langs,
    load_windows,
    open_state,
    schedule,
    story_lang,
)
from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import (
    activity_done,
    activity_photos,
    activity_settings,
    begin_immediate,
    insert_id,
    reward_ledger,
    worlds,
)
from kidsmedia.deps import Device, get_clock, get_conn, get_settings, require_device
from kidsmedia.errors import ApiError
from kidsmedia.library import ThumbRef, thumb_ref
from kidsmedia.rewards import (
    CHAPTER_KIND,
    CHECKED_KINDS,
    EARNING_KINDS,
    RefRow,
    active_chapter_award,
    chapter_complete,
)
from kidsmedia.speech import (
    AudioLookup,
    ProblemAudio,
    QuestionAudio,
    chapter_fields,
    task_fields,
    world_fields,
)
from kidsmedia.usage import day_of, local_now

router = APIRouter(prefix="/api", tags=["device: activities"])


# ---- shared loaders -----------------------------------------------------------------------


def ensure_settings(conn: Connection, profile_id: int) -> Row[Any]:
    """The profile's activity settings, created with the defaults on first use."""
    row = conn.execute(
        select(activity_settings).where(activity_settings.c.profile_id == profile_id)
    ).first()
    if row is None:
        conn.execute(insert(activity_settings).values(profile_id=profile_id, queue=[], progress={}))
        row = conn.execute(
            select(activity_settings).where(activity_settings.c.profile_id == profile_id)
        ).one()
    return row


def world_rows(conn: Connection) -> dict[int, Row[Any]]:
    return {r.id: r for r in conn.execute(select(worlds)).all()}


def content_of(row: Row[Any]) -> WorldContent:
    return load_content(row.content)[0]


def chapter_pictures(row: Row[Any]) -> list[Picture | None]:
    """The stored chapter pictures, by position (index 0 = chapter 1)."""
    return load_content(row.content)[1]


def picture_ref(picture: Picture | None) -> ThumbRef | None:
    return thumb_ref(picture.key, picture.aspect, "upload") if picture is not None else None


def level_of(row: Row[Any]) -> Level:
    """The profile's level (§14.1); an unknown stored value counts as normal."""
    value = row.level
    return value if value in ("easier", "normal", "harder") else DEFAULT_LEVEL


def langs_of(row: Row[Any]) -> dict[int, Lang]:
    """The story language the parent chose per queued world (§14.7)."""
    return load_langs(row.langs)


def windows_of(row: Row[Any]) -> list[Window]:
    """The profile's opening times for Ideas (§14.4a); empty = always open."""
    return load_windows(row.windows)


def done_days(
    conn: Connection, profile_id: int, world_ids: list[int]
) -> dict[int, dict[int, list[date]]]:
    """world id -> chapter -> the day of each done idea (one per kind), for the progress."""
    out: dict[int, dict[int, list[date]]] = {}
    if not world_ids:
        return out
    for r in conn.execute(
        select(activity_done.c.world_id, activity_done.c.position, activity_done.c.day).where(
            activity_done.c.profile_id == profile_id, activity_done.c.world_id.in_(world_ids)
        )
    ).all():
        out.setdefault(r.world_id, {}).setdefault(r.position, []).append(date.fromisoformat(r.day))
    return out


def compute(
    conn: Connection, profile_id: int, now: datetime, persist: bool = True
) -> tuple[Schedule, dict[int, Row[Any]]]:
    """Where the story is at `now` (local time, §13.2). Persists the progress and the
    housekeeping (finished worlds leave the queue) and drops queue entries whose world no
    longer exists; with `persist=False` (judging a queued done tap at a past moment) nothing
    is written."""
    st = ensure_settings(conn, profile_id)
    rows = world_rows(conn)
    stored_queue = [int(i) for i in st.queue]
    queue = [
        PlannedWorld(i, tuple(len(c.tasks) for c in content_of(rows[i]).chapters))
        for i in stored_queue
        if i in rows
    ]
    stored = Progress.from_json(st.progress)
    done = done_days(conn, profile_id, [w.world_id for w in queue])
    sched = schedule(queue, stored, done, st.chapter_needed, st.max_new_per_day, now)
    progress = sched.progress.to_json()
    if persist and (list(sched.queue) != stored_queue or progress != st.progress):
        conn.execute(
            update(activity_settings)
            .where(activity_settings.c.profile_id == profile_id)
            .values(queue=list(sched.queue), progress=progress)
        )
    return sched, rows


def world_cover(row: Row[Any]) -> ThumbRef | None:
    return thumb_ref(row.cover_key, row.cover_aspect, "upload")


def done_kinds(conn: Connection, profile_id: int, world_id: int) -> dict[int, list[Kind]]:
    found: dict[int, set[str]] = {}
    for r in conn.execute(
        select(activity_done.c.position, activity_done.c.kind).where(
            activity_done.c.profile_id == profile_id, activity_done.c.world_id == world_id
        )
    ).all():
        found.setdefault(r.position, set()).add(r.kind)
    return {pos: [k for k in KINDS if k in kinds] for pos, kinds in found.items()}


# ---- device API ---------------------------------------------------------------------------


# `audio` (§14.3): {field: "/api/speech/<key>.m4a"} for the fields that are rendered; a field
# without audio is read by the tablet's TextToSpeech.
Audio = dict[str, str]


class DeviceWorldTexts(BaseModel):
    name: str
    guide: str
    audio: Audio = Field(default_factory=dict)


class DeviceChapterTexts(BaseModel):
    title: str
    intro: str
    outro: str
    audio: Audio = Field(default_factory=dict)


class DeviceQuestion(BaseModel):
    question: str
    answers: list[str]
    correct: int  # the tablet checks offline (§14.2)
    audio: QuestionAudio | None = None  # None = nothing of it rendered


class DeviceProblem(BaseModel):
    text: str
    answer: int  # the tablet checks offline (§15.2)
    hint: str  # shown after the second wrong answer
    audio: ProblemAudio  # {text, hint}: a URL each, None where not rendered


class DeviceTaskTexts(BaseModel):
    """Only the child's level: `text` is that level's text; never easier/harder or `say`."""

    title: str
    text: str
    materials: list[str]
    questions: list[DeviceQuestion] = Field(default_factory=list)  # read tasks only
    problems: list[DeviceProblem] = Field(default_factory=list)  # calc tasks only (§15.2)
    audio: Audio = Field(default_factory=dict)  # title, text (not a read task's text)


class DeviceTask(BaseModel):
    kind: Kind
    minutes: int
    texts: dict[Lang, DeviceTaskTexts]


class DeviceChapter(BaseModel):
    position: int
    date: str | None  # ISO date the chapter opened; None = not open yet
    unlocked: bool
    done_kinds: list[Kind]
    needed: int  # ideas to do here to open the next chapter (§13.2)
    image: ThumbRef | None
    texts: dict[Lang, DeviceChapterTexts]
    tasks: list[DeviceTask]


class DeviceWorld(BaseModel):
    id: int
    slug: str
    age_band: str
    color: str
    lang: Lang  # the language the story is told in (§14.7)
    cover: ThumbRef | None
    texts: dict[Lang, DeviceWorldTexts]
    chapters: list[DeviceChapter]


class ActivitiesOut(BaseModel):
    server_time: datetime
    # Kept for apps before v0.22, which unlocked chapters by date: "00:00" and always true.
    unlock_time: str = "00:00"
    level: Level
    today_is_activity_day: bool = True
    # The next chapter is held back by the daily limit until then (§13.2); None otherwise.
    next_unlock_at: datetime | None
    # For the tablet's offline unlock (§13.3): the daily limit (None = none) and how many
    # chapters opened by finishing today.
    max_new_per_day: int | None
    chapter_stars: int  # stars for a chapter with every idea done (§14.4); 0 = none
    opened_today: int
    story_done: bool  # the last chapter is done and no story is queued after it
    # Opening times (§14.4a): the tablet re-evaluates `windows` with its trusted clock.
    windows: list[Window]
    open_now: bool
    opens_at: datetime | None  # next opening (local); None when open or never
    world: DeviceWorld | None


# A done tap queued offline is accepted up to this long after it happened (§14.4a).
DONE_MAX_AGE = timedelta(days=7)


class DoneIn(BaseModel):
    world_id: int
    position: int = Field(ge=1)
    kind: Kind
    # read and calc tasks only (§14.2, §15.2); ignored for the other kinds
    attempts: int | None = Field(default=None, ge=1, le=50)
    # When the child tapped "I'm done!" (the tablet's trusted clock, with offset); None = now.
    done_at: AwareDatetime | None = None


def done_moment(done_at: datetime | None, now: datetime) -> datetime:
    """The moment a done tap is judged at (§14.4a), in `now`'s zone: `done_at`, but never
    later than now (a tablet clock a little ahead); 409 activity_too_old when it is more than
    7 days ago."""
    if done_at is None:
        return now
    moment = min(done_at.astimezone(now.tzinfo), now)
    if now - moment > DONE_MAX_AGE:
        raise ApiError(409, "activity_too_old", max_days=DONE_MAX_AGE.days)
    return moment


def device_world_texts(
    texts: dict[Lang, WorldTexts], audio: AudioLookup | None = None
) -> dict[Lang, DeviceWorldTexts]:
    """World texts for the tablet: without the spoken forms, with their audio (§14.3) when
    `audio` is given."""
    return {
        lang: DeviceWorldTexts(
            name=t.name,
            guide=t.guide,
            audio=audio.fields(lang, world_fields(t)) if audio is not None else {},
        )
        for lang, t in texts.items()
    }


def device_chapter_texts(
    texts: dict[Lang, ChapterTexts], audio: AudioLookup
) -> dict[Lang, DeviceChapterTexts]:
    return {
        lang: DeviceChapterTexts(
            title=t.title,
            intro=t.intro,
            outro=t.outro,
            audio=audio.fields(lang, chapter_fields(t)),
        )
        for lang, t in texts.items()
    }


def device_task(task: PackTask, level: Level, audio: AudioLookup) -> DeviceTask:
    """A task for the tablet at the child's level (§14.1, §14.2), with its audio (§14.3)."""
    texts: dict[Lang, DeviceTaskTexts] = {}
    for lang, t in task.texts.items():
        questions: list[DeviceQuestion] = []
        problems: list[DeviceProblem] = []
        if task.kind == "calc":
            problems = [
                DeviceProblem(
                    text=p.text, answer=p.answer, hint=p.hint, audio=audio.problem(lang, p)
                )
                for p in t.level_problems(level)
            ]
        if task.kind == "read":
            questions = [
                DeviceQuestion(
                    question=q.question,
                    answers=list(q.answers),
                    correct=q.correct,
                    audio=audio.question(lang, q),
                )
                for q in t.level_questions(level)
            ]
        texts[lang] = DeviceTaskTexts(
            title=t.title,
            text=t.level_text(level),
            materials=list(t.materials),
            questions=questions,
            problems=problems,
            audio=audio.fields(lang, task_fields(task.kind, t, level)),
        )
    return DeviceTask(kind=task.kind, minutes=task.minutes, texts=texts)


def activities_out(
    conn: Connection, profile_id: int, now: datetime, audio: AudioLookup, default_lang: Lang
) -> ActivitiesOut:
    sched, rows = compute(conn, profile_id, now)
    st = ensure_settings(conn, profile_id)
    level = level_of(st)
    windows = windows_of(st)
    open_now, opens_at = open_state(windows, now)
    world: DeviceWorld | None = None
    if sched.current is not None:
        row = rows[sched.current]
        content, pictures = load_content(row.content)
        done = done_kinds(conn, profile_id, row.id)
        chapters = [
            DeviceChapter(
                position=slot.position,
                date=slot.opened_on.isoformat() if slot.unlocked and slot.opened_on else None,
                unlocked=slot.unlocked,
                done_kinds=done.get(slot.position, []),
                needed=slot.needed,
                image=picture_ref(picture),
                texts=device_chapter_texts(chapter.texts, audio),
                tasks=[device_task(task, level, audio) for task in chapter.tasks],
            )
            for slot, chapter, picture in zip(
                sched.chapters, content.chapters, pictures, strict=True
            )
        ]
        world = DeviceWorld(
            id=row.id,
            slug=row.slug,
            age_band=row.age_band,
            color=row.color,
            lang=story_lang(langs_of(st).get(row.id), content.texts, default_lang),
            cover=world_cover(row),
            texts=device_world_texts(content.texts, audio),
            chapters=chapters,
        )
    return ActivitiesOut(
        server_time=now,
        level=level,
        next_unlock_at=sched.next_unlock_at,
        max_new_per_day=st.max_new_per_day,
        chapter_stars=st.chapter_stars,
        opened_today=sched.opened_today,
        story_done=sched.story_done,
        windows=windows,
        open_now=open_now,
        opens_at=opens_at,
        world=world,
    )


@router.get("/activities", response_model=ActivitiesOut)
def get_activities(
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> ActivitiesOut:
    """The current story world with all its chapters (SPEC §13.3)."""
    now = local_now(clock, settings)
    return activities_out(
        conn, device.profile_id, now, AudioLookup(settings), settings.default_locale
    )


@dataclass(frozen=True)
class JudgedTask:
    """A task a done tap (or photo) may finish: its world, the task and the moment it is
    judged at (§14.4a)."""

    world: Row[Any]
    content: WorldContent
    task: PackTask
    moment: datetime
    position: int


def judge_done(
    conn: Connection,
    profile_id: int,
    now: datetime,
    world_id: int,
    position: int,
    kind: Kind,
    done_at: datetime | None,
) -> JudgedTask:
    """The checks of a done tap, shared by `done` and `photo` (§13.3, §14.4a, §15.3): 404
    unknown_task, 409 activities_closed outside the opening times plus 15 minutes, 409
    activity_locked for a locked chapter or a world that isn't current. With `done_at` (a tap
    queued offline) the opening times and the unlock are judged at that moment (clamped to
    now; 409 activity_too_old past 7 days). Nothing is written but housekeeping."""
    moment = done_moment(done_at, now)
    sched, rows = compute(conn, profile_id, now)  # housekeeping as of now
    if moment != now:
        sched, rows = compute(conn, profile_id, moment, persist=False)
    row = rows.get(world_id)
    if row is None:
        raise ApiError(404, "unknown_task")
    content = content_of(row)
    if position > len(content.chapters):
        raise ApiError(404, "unknown_task")
    task = next((t for t in content.chapters[position - 1].tasks if t.kind == kind), None)
    if task is None:
        raise ApiError(404, "unknown_task")
    windows = windows_of(ensure_settings(conn, profile_id))
    if not done_allowed(windows, moment):
        opens_at = open_state(windows, now)[1]
        raise ApiError(
            409, "activities_closed", opens_at=opens_at.isoformat() if opens_at else None
        )
    if sched.current != world_id or not sched.chapters[position - 1].unlocked:
        raise ApiError(409, "activity_locked")
    return JudgedTask(row, content, task, moment, position)


def next_done_id(conn: Connection) -> int:
    """The id for a new done row: past every done row and every ledger row that refers to
    one (`earn`, `bonus`, `reopen`). SQLite would hand out the id of a deleted last row
    again ("open again", §17.3, or a deleted world), and its old ledger rows would then count
    for the new row. Call with the write lock held."""
    top_done = conn.execute(select(func.max(activity_done.c.id))).scalar()
    top_ref = conn.execute(
        select(func.max(reward_ledger.c.ref_id)).where(reward_ledger.c.kind.in_(EARNING_KINDS))
    ).scalar()
    return max(int(top_done or 0), int(top_ref or 0)) + 1


def chapter_done(conn: Connection, profile_id: int, world_id: int, position: int) -> dict[str, int]:
    """{kind: done id} of a chapter's done tasks."""
    return {
        r.kind: r.id
        for r in conn.execute(
            select(activity_done.c.kind, activity_done.c.id).where(
                activity_done.c.profile_id == profile_id,
                activity_done.c.world_id == world_id,
                activity_done.c.position == position,
            )
        ).all()
    }


def star_rows(conn: Connection, profile_id: int, done_ids: Iterable[int]) -> list[RefRow]:
    """The earning ledger rows (§14.4, §17.3) of these done ids, as (kind, ref_id, delta)."""
    ids = list(done_ids)
    if not ids:
        return []
    return [
        (r.kind, r.ref_id, r.delta)
        for r in conn.execute(
            select(reward_ledger.c.kind, reward_ledger.c.ref_id, reward_ledger.c.delta)
            .where(
                reward_ledger.c.profile_id == profile_id,
                reward_ledger.c.kind.in_(EARNING_KINDS),
                reward_ledger.c.ref_id.in_(ids),
            )
            .order_by(reward_ledger.c.id)
        ).all()
    ]


def store_done(
    conn: Connection,
    device: Device,
    world_id: int,
    position: int,
    kind: Kind,
    moment: datetime,
    attempts: int | None,
    task_kinds: Iterable[str],
) -> int:
    """Store a done row (idempotent) and return its id. `attempts` is kept for read and calc
    tasks only, and a repeated done keeps the first value (§14.2, §15.2). A done task earns
    no stars itself (§14.4): the done row that makes every idea of the chapter (`task_kinds`)
    done earns `chapter_stars` as a `chapter` row in the ledger (`ref_id` = that done id),
    unless the chapter already holds its stars. Its `day` and `done_at` are those of
    `moment`. After "open again" (§17.3) took the chapter's stars back, finishing the chapter
    again earns them again."""
    attempts = attempts if kind in CHECKED_KINDS else None
    begin_immediate(conn)  # next_done_id, the insert and the chapter check are one step
    result = conn.execute(
        sqlite_insert(activity_done)
        .values(
            id=next_done_id(conn),
            profile_id=device.profile_id,
            world_id=world_id,
            position=position,
            kind=kind,
            day=day_of(moment),
            done_at=to_db(moment),
            device_id=device.id,
            attempts=attempts,
        )
        .on_conflict_do_nothing()
        .returning(activity_done.c.id)
    )
    done_id = result.scalar()
    if done_id is not None:  # a new done row: does it finish the chapter? (§14.4)
        done = chapter_done(conn, device.profile_id, world_id, position)
        stars = int(ensure_settings(conn, device.profile_id).chapter_stars)
        ledger = star_rows(conn, device.profile_id, done.values())
        if (
            stars > 0
            and chapter_complete(done, task_kinds)
            and active_chapter_award(ledger, done.values()) is None
        ):
            conn.execute(
                insert(reward_ledger).values(
                    profile_id=device.profile_id,
                    delta=stars,
                    kind=CHAPTER_KIND,
                    ref_id=done_id,
                    created_at=to_db(moment),
                )
            )
        return int(done_id)
    existing: int = conn.execute(
        select(activity_done.c.id).where(
            activity_done.c.profile_id == device.profile_id,
            activity_done.c.world_id == world_id,
            activity_done.c.position == position,
            activity_done.c.kind == kind,
        )
    ).scalar_one()
    return existing


def task_kinds_of(judged: JudgedTask) -> list[str]:
    """The kinds of the judged task's chapter."""
    position = judged.position
    return [t.kind for t in judged.content.chapters[position - 1].tasks]


@router.post("/activities/done", response_model=ActivitiesOut)
def mark_done(
    body: DoneIn,
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> ActivitiesOut:
    """One task done. Idempotent; judged by `judge_done` (404 unknown_task, 409
    activities_closed / activity_locked / activity_too_old, SPEC §13.3, §14.4a). `attempts`
    is stored for read and calc tasks (§14.2, §15.2). The done that makes every idea of the
    chapter done earns the chapter's stars (§14.4). Draw and write may still be
    done without a photo (older apps, §15.3)."""
    now = local_now(clock, settings)
    judged = judge_done(
        conn, device.profile_id, now, body.world_id, body.position, body.kind, body.done_at
    )
    store_done(
        conn,
        device,
        body.world_id,
        body.position,
        body.kind,
        judged.moment,
        body.attempts,
        task_kinds_of(judged),
    )
    return activities_out(
        conn, device.profile_id, now, AudioLookup(settings), settings.default_locale
    )


# ---- photos (§15.3) -----------------------------------------------------------------------

PHOTO_KINDS: frozenset[Kind] = frozenset({"draw", "write"})
FALLBACK_LANGS: tuple[Lang, ...] = ("en", "de", "el")  # §13.1


def pick_lang[T](texts: Mapping[Lang, T], lang: Lang) -> T:
    """The block in `lang`, else English, German, Greek (§13.1), else the first."""
    for candidate in (lang, *FALLBACK_LANGS):
        if candidate in texts:
            return texts[candidate]
    return next(iter(texts.values()))


async def read_body(request: Request, limit: int, reason: str) -> bytes:
    """A raw request body, at most `limit` bytes: 413 `reason` with `max_mb` beyond."""
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise ApiError(413, reason, max_mb=limit // (1024 * 1024))
    return bytes(data)


@router.post("/activities/photo", response_model=ActivitiesOut)
async def upload_photo(
    request: Request,
    world_id: int = Query(),
    position: int = Query(ge=1),
    kind: Kind = Query(),
    done_at: AwareDatetime | None = Query(default=None),
    device: Device = Depends(require_device),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> ActivitiesOut:
    """A photo of a drawing or a story (§15.3): raw JPEG body, at most 15 MB (413
    photo_too_large). Only draw and write (422 photo_not_allowed). Judged exactly like `done`
    (`done_at` = when the photo was taken), then the photo is stored (EXIF-rotated, without
    any metadata, WebP lg/sm) and the task is marked done. A second photo of a done task is
    stored too. 422 bad_image for a body that doesn't decode. World name
    and task title are kept as snapshots in the story language (§14.7)."""
    if kind not in PHOTO_KINDS:
        raise ApiError(422, "photo_not_allowed", kind=kind)
    data = await read_body(request, photos.MAX_PHOTO_BYTES, "photo_too_large")
    now = local_now(clock, settings)
    judged = judge_done(conn, device.profile_id, now, world_id, position, kind, done_at)
    # Decoding and the WebP encodes are CPU work: off the event loop, which also serves the
    # heartbeats of a playing file.
    try:
        img = await run_in_threadpool(photos.decode, data)
    except thumbs.MediaError as exc:
        raise ApiError(422, "bad_image") from exc
    done_id = store_done(
        conn, device, world_id, position, kind, judged.moment, None, task_kinds_of(judged)
    )
    lang = story_lang(
        langs_of(ensure_settings(conn, device.profile_id)).get(world_id),
        judged.content.texts,
        settings.default_locale,
    )
    task_texts: TaskTexts = pick_lang(judged.task.texts, lang)
    photo_id = insert_id(
        conn.execute(
            insert(activity_photos).values(
                profile_id=device.profile_id,
                done_id=done_id,
                world_id=world_id,
                world_name=pick_lang(judged.content.texts, lang).name,
                position=position,
                kind=kind,
                task_title=task_texts.title,
                key="",  # set below: the key contains the id
                width=0,
                height=0,
                taken_at=to_db(judged.moment),
                created_at=to_db(now),
            )
        )
    )
    stored = await run_in_threadpool(photos.write_photo, settings.photos_dir, photo_id, img)
    conn.execute(
        update(activity_photos)
        .where(activity_photos.c.id == photo_id)
        .values(key=stored.key, width=stored.width, height=stored.height)
    )
    return activities_out(
        conn, device.profile_id, now, AudioLookup(settings), settings.default_locale
    )
