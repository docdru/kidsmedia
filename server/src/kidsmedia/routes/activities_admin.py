"""Admin: activities, story worlds, story packs and the done tasks with their photos (SPEC
§13.4, §13.5, §14, §15.3, §17)."""

import contextlib
import io
import json
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import IO, Any, ClassVar, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Connection, Row, delete, insert, select, update

from kidsmedia import photos, speech, thumbs, webdav
from kidsmedia.activities import (
    LANGS,
    MAX_NEEDED,
    MAX_PER_DAY,
    MAX_WINDOWS,
    ChapterTexts,
    Kind,
    Lang,
    Level,
    Pack,
    PackTask,
    PackWarning,
    Picture,
    TaskTexts,
    Window,
    WorldTexts,
    is_complete,
    load_content,
    merge_langs,
    pack_warnings,
    stored_content,
    story_lang,
    task_count,
)
from kidsmedia.clock import Clock, from_db, to_db
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
from kidsmedia.deps import (
    Admin,
    get_clock,
    get_conn,
    get_settings,
    require_admin,
    require_admin_csrf,
)
from kidsmedia.errors import ApiError
from kidsmedia.library import ThumbRef
from kidsmedia.rewards import (
    BONUS_KIND,
    CHAPTER_KIND,
    REOPEN_KIND,
    active_chapter_award,
    held_stars,
    reopen_delta,
)
from kidsmedia.routes.activities import (
    chapter_done,
    chapter_pictures,
    compute,
    content_of,
    ensure_settings,
    langs_of,
    level_of,
    pick_lang,
    picture_ref,
    read_body,
    star_rows,
    windows_of,
    world_cover,
    world_rows,
)
from kidsmedia.routes.devices import DEFAULT_PROFILE_ID
from kidsmedia.routes.rewards import stars_of
from kidsmedia.routes.speech import get_worker
from kidsmedia.usage import local_now

router = APIRouter(prefix="/api/admin", tags=["admin: activities"])

PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"
# A pack zip may bring its recordings (speech/, §13.5). A six-chapter pack has about 20 MB and
# 400 files per language, so three languages and the pictures fit these limits with room to spare.
MAX_PACK_BYTES = 200 * 1024 * 1024
MAX_UNZIPPED_BYTES = 210 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_VOICES_JSON_BYTES = 64 * 1024
SPOOL_IN_MEMORY = 8 * 1024 * 1024  # a bigger upload is spooled to a file on the data disk
LOG_DAYS = 14
MAX_CHAPTER_STARS = 20  # highest "stars for a finished chapter" (§14.4)
# Decision 31: the order of the example worlds; unknown slugs follow alphabetically. Most of
# them come with a derived image through EXTRA_PACKS_DIR; only lighthouse-island is bundled.
EXAMPLE_ORDER = ("owl-mystery", "lighthouse-island", "rocket-kiwi", "forest-bakery", "coral-city")


# ---- models -------------------------------------------------------------------------------


class SettingsIn(BaseModel):
    """Every field omitted = kept."""

    level: Level | None = None  # §14.1
    windows: list[Window] | None = Field(default=None, max_length=MAX_WINDOWS)  # §14.4a
    # §13.2: ideas to do in a chapter to open the next one
    chapter_needed: int | None = Field(default=None, ge=1, le=MAX_NEEDED)
    # §13.2: most chapters opened by finishing per day; null = no limit
    max_new_per_day: int | None = Field(default=None, ge=1, le=MAX_PER_DAY)
    # §14.4: stars for a chapter with every idea done; 0 = none
    chapter_stars: int | None = Field(default=None, ge=0, le=MAX_CHAPTER_STARS)


class SettingsOut(BaseModel):
    level: Level
    windows: list[Window]  # opening times for Ideas (§14.4a); empty = always open
    chapter_needed: int
    max_new_per_day: int | None  # None = no limit
    chapter_stars: int


class QueueIn(BaseModel):
    world_ids: list[int] = Field(max_length=100)
    # The story language per queued world (§14.7); worlds not named keep theirs.
    langs: dict[int, Lang] | None = None


class QueueEntry(BaseModel):
    world_id: int
    status: Literal["running", "upcoming"]
    chapters: int
    chapters_open: int  # 0 for an upcoming world (§13.2)
    story_done: bool  # running, every chapter done and nothing queued after it
    lang: Lang  # the language the tablet tells this story in (§14.7)
    langs: list[Lang]  # the languages the world has its own texts in


class WorldSummary(BaseModel):
    id: int
    slug: str
    age_band: str
    color: str
    cover: ThumbRef | None
    texts: dict[Lang, WorldTexts]
    chapters: int
    tasks: int
    complete: bool
    warnings: list[PackWarning]


class LogRow(BaseModel):
    date: str
    world_id: int
    position: int
    kinds: list[str]
    done_at: datetime
    attempts: int | None = None  # of the read task in this row, if any (§14.2)
    calc_attempts: int | None = None  # of the calc task in this row, if any (§15.2)
    # The latest photo of a draw or write task in this row (§15.3); None = no picture.
    photo_id: int | None = None


class ExampleName(BaseModel):
    name: str


class ExampleOut(BaseModel):
    slug: str
    texts: dict[Lang, ExampleName]
    imported: bool


class ActivitiesAdminOut(BaseModel):
    settings: SettingsOut
    queue: list[QueueEntry]
    worlds: list[WorldSummary]
    log: list[LogRow]
    examples: list[ExampleOut]
    webdav: bool = False  # a WebDAV folder of pack zips is set up (§13.5a)


class WebDavPack(BaseModel):
    name: str  # the zip's file name in the folder
    size: int | None
    modified: datetime | None


class WebDavPacksOut(BaseModel):
    packs: list[WebDavPack]


# The editor's text blocks: pack format plus `audio` (§14.3), {field: url} for the spoken fields
# that are rendered (GET /api/admin/speech/<key>.m4a). Output only: PUT ignores it.


class EditorWorldTexts(WorldTexts):
    keeps_audio: ClassVar[bool] = True
    audio: dict[str, str] = Field(default_factory=dict)  # name


class EditorChapterTexts(ChapterTexts):
    keeps_audio: ClassVar[bool] = True
    audio: dict[str, str] = Field(default_factory=dict)  # title, intro, outro


class EditorTaskTexts(TaskTexts):
    keeps_audio: ClassVar[bool] = True
    audio: dict[str, str] = Field(default_factory=dict)  # title; text, easier, harder (not read)
    # Read tasks: per level, one entry per question in `questions` (None = nothing rendered).
    question_audio: dict[Level, list[speech.QuestionAudio | None]] = Field(default_factory=dict)
    # Calc tasks: per level, one entry per problem in `problems` (§15.2).
    problem_audio: dict[Level, list[speech.ProblemAudio]] = Field(default_factory=dict)


class EditorTask(PackTask):
    texts: dict[Lang, EditorTaskTexts] = Field(min_length=1)  # type: ignore[assignment]


class WorldChapter(BaseModel):
    """A chapter in pack format with its stored picture instead of a file name."""

    image: ThumbRef | None
    texts: dict[Lang, EditorChapterTexts]
    tasks: list[EditorTask]


class WorldFull(BaseModel):
    """A world in pack format (§13.5), with its id, cover and chapter pictures; easier/harder
    included."""

    id: int
    format: Literal[1] = 1
    slug: str
    age_band: str
    color: str
    cover: ThumbRef | None
    texts: dict[Lang, EditorWorldTexts]
    chapters: list[WorldChapter]


class ImportReport(BaseModel):
    slug: str
    world_id: int | None  # None in a dry run of a new world
    replaced: bool
    chapters: int
    tasks: int
    complete: bool
    warnings: list[PackWarning]
    recordings: int = 0  # read-aloud files the zip brings (speech/, §13.5)


class DonePhotoOut(BaseModel):
    """A photo of a done task (§15.3, §17.3)."""

    id: int
    url_sm: str  # GET /api/admin/photos/<key>.webp?size=sm (admin session)
    url_lg: str
    taken_at: datetime


class DoneOut(BaseModel):
    """A done task (`activity_done` row, §17.3); names in the story language (§14.7)."""

    id: int
    world_id: int
    world_name: str
    position: int
    kind: Kind
    task_title: str
    done_at: datetime
    attempts: int | None  # read and calc (§14.2, §15.2), else None
    stars: int  # what it earned and still holds: its earn, chapter and bonus rows (§17.3)
    bonus: bool  # the parent gave it its bonus star
    chapter: bool  # it finished its chapter and holds the chapter's stars (§14.4)
    reopen_stars: int  # what "open again" takes back: its stars + the chapter's, if held
    photos: list[DonePhotoOut]  # oldest first; empty = no picture


class DoneListOut(BaseModel):
    """One page of Done activities (§17.3)."""

    entries: list[DoneOut]  # newest first
    next_before_id: int | None  # pass as before_id for the next page; None = no more


# ---- helpers ------------------------------------------------------------------------------


def _world_row(conn: Connection, world_id: int) -> Row[Any]:
    row = conn.execute(select(worlds).where(worlds.c.id == world_id)).first()
    if row is None:
        raise ApiError(404, "world_not_found", world_id=world_id)
    return row


def editor_task(task: PackTask, audio: speech.AudioLookup) -> EditorTask:
    texts: dict[Lang, EditorTaskTexts] = {}
    for lang, t in task.texts.items():
        question_audio = {
            level: [audio.question(lang, q) for q in questions]
            for level, questions in (t.questions or {}).items()
        }
        problem_audio = {
            level: [audio.problem(lang, p) for p in problems]
            for level, problems in (t.problems or {}).items()
        }
        texts[lang] = EditorTaskTexts.model_validate(
            {
                **t.model_dump(),
                "audio": audio.fields(lang, speech.task_editor_fields(task.kind, t)),
                "question_audio": question_audio if task.kind == "read" else {},
                "problem_audio": problem_audio if task.kind == "calc" else {},
            }
        )
    return EditorTask(kind=task.kind, minutes=task.minutes, texts=texts)


def world_full(row: Row[Any], settings: Settings) -> WorldFull:
    content, pictures = load_content(row.content)
    audio = speech.AudioLookup(settings, speech.ADMIN_PREFIX)
    return WorldFull(
        id=row.id,
        slug=row.slug,
        age_band=row.age_band,
        color=row.color,
        cover=world_cover(row),
        texts={
            lang: EditorWorldTexts.model_validate(
                {**t.model_dump(), "audio": audio.fields(lang, speech.world_fields(t))}
            )
            for lang, t in content.texts.items()
        },
        chapters=[
            WorldChapter(
                image=picture_ref(picture),
                texts={
                    lang: EditorChapterTexts.model_validate(
                        {**t.model_dump(), "audio": audio.fields(lang, speech.chapter_fields(t))}
                    )
                    for lang, t in chapter.texts.items()
                },
                tasks=[editor_task(task, audio) for task in chapter.tasks],
            )
            for chapter, picture in zip(content.chapters, pictures, strict=True)
        ],
    )


def world_summary(row: Row[Any]) -> WorldSummary:
    content = content_of(row)
    warnings = pack_warnings(content)
    return WorldSummary(
        id=row.id,
        slug=row.slug,
        age_band=row.age_band,
        color=row.color,
        cover=world_cover(row),
        texts=content.texts,
        chapters=len(content.chapters),
        tasks=task_count(content),
        complete=is_complete(warnings),
        warnings=warnings,
    )


def example_key(slug: str) -> tuple[int, str]:
    """Sort key for examples: EXAMPLE_ORDER first, then the rest by slug (decision 31)."""
    if slug in EXAMPLE_ORDER:
        return EXAMPLE_ORDER.index(slug), ""
    return len(EXAMPLE_ORDER), slug


@dataclass(frozen=True)
class Example:
    """An example pack: its world.json and the folder its pictures are in."""

    pack: Pack
    folder: Path


def example_packs(settings: Settings) -> dict[str, Example]:
    """The example packs by slug, in example order: the bundled ones
    (`kidsmedia/packs/<slug>/world.json`), then those in EXTRA_PACKS_DIR, which win on the
    same slug (§13.5). An unset or missing EXTRA_PACKS_DIR adds nothing (a derived image
    sets it; a plain one doesn't have it)."""
    found: dict[str, Example] = {}
    for root in (PACKS_DIR, settings.extra_packs_dir):
        if root is None or not root.is_dir():
            continue
        for path in sorted(root.glob("*/world.json")):
            pack = Pack.model_validate_json(path.read_bytes())
            found[pack.slug] = Example(pack, path.parent)
    return {slug: found[slug] for slug in sorted(found, key=example_key)}


def world_langs(row: Row[Any]) -> list[Lang]:
    """The languages a world has its own texts in, in LANGS order (§14.7)."""
    texts = content_of(row).texts
    return [lang for lang in LANGS if lang in texts]


def admin_state(conn: Connection, now: datetime, settings: Settings) -> ActivitiesAdminOut:
    profile = DEFAULT_PROFILE_ID
    default_lang = settings.default_locale
    sched, rows = compute(conn, profile, now)
    st = ensure_settings(conn, profile)
    chosen = langs_of(st)
    queue = []
    for world_id in sched.queue:
        have = world_langs(rows[world_id])
        running = world_id == sched.current
        queue.append(
            QueueEntry(
                world_id=world_id,
                status="running" if running else "upcoming",
                chapters=len(content_of(rows[world_id]).chapters),
                chapters_open=sum(c.unlocked for c in sched.chapters) if running else 0,
                story_done=running and sched.story_done,
                lang=story_lang(chosen.get(world_id), have, default_lang),
                langs=have,
            )
        )
    since = (now.date() - timedelta(days=LOG_DAYS - 1)).isoformat()
    done_rows = conn.execute(
        select(activity_done)
        .where(activity_done.c.profile_id == profile, activity_done.c.day >= since)
        .order_by(activity_done.c.done_at)
    ).all()
    latest_photo = latest_photos(conn, profile, [r.id for r in done_rows])
    grouped: dict[tuple[str, int, int], LogRow] = {}
    for r in done_rows:
        key = (r.day, r.world_id, r.position)
        if key in grouped:
            grouped[key].kinds.append(r.kind)
        else:
            grouped[key] = LogRow(
                date=r.day,
                world_id=r.world_id,
                position=r.position,
                kinds=[r.kind],
                done_at=from_db(r.done_at),
            )
        if r.kind == "read":
            grouped[key].attempts = r.attempts
        if r.kind == "calc":
            grouped[key].calc_attempts = r.attempts
        photo_id = latest_photo.get(r.id)
        if photo_id is not None and photo_id > (grouped[key].photo_id or 0):
            grouped[key].photo_id = photo_id
    log = sorted(grouped.values(), key=lambda x: (x.date, x.position), reverse=True)
    slugs = {r.slug for r in rows.values()}
    examples = [
        ExampleOut(
            slug=ex.pack.slug,
            texts={lang: ExampleName(name=t.name) for lang, t in ex.pack.texts.items()},
            imported=ex.pack.slug in slugs,
        )
        for ex in example_packs(settings).values()
    ]
    return ActivitiesAdminOut(
        settings=SettingsOut(
            level=level_of(st),
            windows=windows_of(st),
            chapter_needed=st.chapter_needed,
            max_new_per_day=st.max_new_per_day,
            chapter_stars=st.chapter_stars,
        ),
        queue=queue,
        worlds=[world_summary(r) for r in sorted(rows.values(), key=lambda r: r.id)],
        log=log,
        examples=examples,
        webdav=bool(settings.packs_webdav_url),
    )


def _set_queue(
    conn: Connection, now: datetime, new_queue: list[int], langs: dict[int, Lang] | None = None
) -> None:
    """Store a queue and the story languages (§14.7): `langs` updates the stored ones; worlds
    leaving the queue lose theirs. A new first world starts fresh on the next read (§13.2);
    reordering only later entries keeps the progress."""
    compute(conn, DEFAULT_PROFILE_ID, now)  # progress and housekeeping up to now first
    stored = langs_of(ensure_settings(conn, DEFAULT_PROFILE_ID))
    conn.execute(
        update(activity_settings)
        .where(activity_settings.c.profile_id == DEFAULT_PROFILE_ID)
        .values(
            queue=new_queue,
            langs=merge_langs(stored, langs or {}, new_queue),
        )
    )


def latest_photos(conn: Connection, profile_id: int, done_ids: list[int]) -> dict[int, int]:
    """{done id: id of its latest photo} for the done rows that have one (§15.3)."""
    if not done_ids:
        return {}
    out: dict[int, int] = {}
    for r in conn.execute(
        select(activity_photos.c.id, activity_photos.c.done_id).where(
            activity_photos.c.profile_id == profile_id,
            activity_photos.c.done_id.in_(done_ids),
        )
    ).all():
        out[r.done_id] = max(out.get(r.done_id, 0), r.id)
    return out


def _invalid(*errors: str) -> ApiError:
    return ApiError(422, "invalid_pack", errors=list(errors))


def _validation_errors(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(p) for p in err['loc']) or 'world.json'}: {err['msg']}"
        for err in exc.errors()
    ][:50]


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes:
    """Read one zip entry with a hard size cap (the declared size may lie)."""
    with zf.open(info) as f:
        data = f.read(budget + 1)
    if len(data) > budget:
        raise _invalid("zip: too large when unpacked")
    return data


# Reads a picture by the name world.json gives it; None = not there.
PictureReader = Callable[[str], bytes | None]


@dataclass(frozen=True)
class PackPictures:
    """The decoded pictures a pack names: its cover and one per chapter (None = none)."""

    cover: Image.Image | None = None
    chapters: tuple[Image.Image | None, ...] = ()


def _decode(data: bytes, where: str) -> Image.Image:
    """Decode like other images (§6.4), shrunk to the largest thumbnail size so a pack with
    many pictures stays small in memory."""
    try:
        img = thumbs.open_image(data)
    except thumbs.MediaError as exc:
        raise _invalid(f"{where}: {exc.reason}") from exc
    edge = max(thumbs.SIZES.values())
    img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return img


def read_pictures(pack: Pack, read: PictureReader, source: str) -> PackPictures:
    """The cover and chapter pictures that world.json names, read and decoded."""

    def picture(name: str | None, where: str) -> Image.Image | None:
        if name is None:
            return None
        data = read(name)
        if data is None:
            raise _invalid(f"{where}: {name} is not in the {source}")
        return _decode(data, where)

    return PackPictures(
        cover=picture(pack.cover, "cover"),
        chapters=tuple(
            picture(chapter.image, f"chapters.{i}.image") for i, chapter in enumerate(pack.chapters)
        ),
    )


@dataclass(frozen=True)
class ParsedPack:
    """An uploaded pack: world.json, its pictures, and the recordings it brings (key → zip
    entry, §13.5), read with `read` while the zip is open (`open_pack`)."""

    pack: Pack
    pictures: PackPictures
    recordings: dict[str, zipfile.ZipInfo]
    read: Callable[[zipfile.ZipInfo], bytes]


class _Budget:
    """What may still be unpacked from one zip: the declared sizes may lie, so every read
    counts what it really got."""

    def __init__(self, zf: zipfile.ZipFile, left: int) -> None:
        self.zf, self.left = zf, left

    def read(self, info: zipfile.ZipInfo, cap: int) -> bytes:
        data = _read_entry(self.zf, info, min(self.left, cap))
        self.left -= len(data)
        return data


def _no_recording(info: zipfile.ZipInfo) -> bytes:
    raise AssertionError(info.filename)  # a pack without a zip has no recordings


def _relative(name: str, base: PurePosixPath) -> str | None:
    """A zip entry's path below the folder of world.json; None outside it."""
    if str(base) == ".":
        return name
    prefix = f"{base}/"
    return name[len(prefix) :] if name.startswith(prefix) else None


def _zip_recordings(
    infos: list[zipfile.ZipInfo], base: PurePosixPath, pack: Pack, budget: _Budget
) -> dict[str, zipfile.ZipInfo]:
    """The `speech/` folder of a pack zip, checked like an example pack's (§13.5, §14.3):
    key → entry of every recording. Any problem rejects the whole pack."""
    prefix = f"{speech.RECORDED_DIR}/"
    entries: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = _relative(info.filename, base)
        if name is not None and name.startswith(prefix):
            entries[name[len(prefix) :]] = info
    if not entries:
        return {}
    voices_entry = entries.pop(speech.RECORDED_VOICES, None)
    if voices_entry is None:
        raise _invalid(f"speech: {speech.RECORDED_VOICES} is missing")
    try:
        voices = json.loads(budget.read(voices_entry, MAX_VOICES_JSON_BYTES))
    except ValueError as exc:  # also a UnicodeDecodeError
        raise _invalid(f"speech: {speech.RECORDED_VOICES} is not JSON") from exc
    content = pack.content()
    files = {name: info.file_size for name, info in entries.items()}
    problems = speech.check_recordings(content, voices, files)
    if problems:
        raise _invalid(*problems[:50])
    return {key: entries[path] for path, key in speech.recorded_files(content, voices).items()}


@contextlib.contextmanager
def open_pack(body: IO[bytes]) -> Iterator[ParsedPack]:
    """A zip with world.json (+ pictures, + recordings), or world.json alone. Entries are read
    by name only, never extracted to disk (§13.5). The zip stays open inside the block, so
    the recordings can be read once the pack has passed."""
    head = body.read(2)
    body.seek(0)
    if head != b"PK":
        yield ParsedPack(_validate(body.read()), PackPictures(), {}, _no_recording)
        return
    try:
        zf = zipfile.ZipFile(body)
    except zipfile.BadZipFile as exc:
        raise _invalid("zip: not a valid zip file") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_ENTRIES:
            raise _invalid("zip: too many files")
        if sum(i.file_size for i in infos) > MAX_UNZIPPED_BYTES:
            raise _invalid("zip: too large when unpacked")
        candidates = sorted(
            (i for i in infos if PurePosixPath(i.filename).name == "world.json"),
            key=lambda i: len(PurePosixPath(i.filename).parts),
        )
        if not candidates:
            raise _invalid("zip: world.json is missing")
        base = PurePosixPath(candidates[0].filename).parent
        raw = _read_entry(zf, candidates[0], MAX_UNZIPPED_BYTES)
        pack = _validate(raw)
        budget = _Budget(zf, MAX_UNZIPPED_BYTES - len(raw))

        def read(name: str) -> bytes | None:
            wanted = str(base / name) if str(base) != "." else name
            entry = next((i for i in infos if i.filename == wanted), None)
            if entry is None:
                return None
            return budget.read(entry, thumbs.MAX_IMAGE_UPLOAD_BYTES)

        pictures = read_pictures(pack, read, "zip")
        recordings = _zip_recordings(infos, base, pack, budget)
        yield ParsedPack(
            pack, pictures, recordings, lambda info: budget.read(info, speech.MAX_RECORDED_BYTES)
        )


def install_recordings(settings: Settings, parsed: ParsedPack) -> int:
    """Store the recordings of an uploaded pack under their keys, like an example pack's
    (§14.3). They don't depend on the world's id, so this runs before the world is saved: if
    the import fails afterwards, the files are pruned at the next start."""
    for key, info in parsed.recordings.items():
        speech.write_recording(settings, key, parsed.read(info))
    return len(parsed.recordings)


async def spool_body(
    request: Request, settings: Settings, limit: int, reason: str
) -> "tempfile.SpooledTemporaryFile[bytes]":
    """A raw request body, at most `limit` bytes (413 `reason` with `max_mb` beyond), kept in
    memory up to 8 MB and in a nameless temporary file on the data disk above that, so a pack
    with recordings never sits in memory whole. The caller closes it."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    f: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # noqa: SIM115
        max_size=SPOOL_IN_MEMORY, dir=str(settings.data_dir)
    )
    try:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise ApiError(413, reason, max_mb=limit // (1024 * 1024))
            f.write(chunk)
        f.seek(0)
    except BaseException:
        f.close()
        raise
    return f


def folder_reader(folder: Path) -> PictureReader:
    """Pictures of a pack folder: plain file names next to world.json, never a path outside
    the folder."""
    root = folder.resolve()

    def read(name: str) -> bytes | None:
        if "/" in name or "\\" in name or ".." in name:
            raise _invalid(f"{name}: not a file name next to world.json")
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            return None
        if path.stat().st_size > thumbs.MAX_IMAGE_UPLOAD_BYTES:
            raise _invalid(f"{name}: image_too_large")
        return path.read_bytes()

    return read


def load_folder_pack(folder: Path) -> tuple[Pack, PackPictures]:
    """A pack folder (§13.5): world.json and the pictures it names, next to it."""
    pack = _validate((folder / "world.json").read_bytes())
    return pack, read_pictures(pack, folder_reader(folder), "pack folder")


def _validate(raw: bytes) -> Pack:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise _invalid("world.json: not valid JSON") from exc
    try:
        return Pack.model_validate(data)
    except ValidationError as exc:
        raise _invalid(*_validation_errors(exc)) from exc


def _thumb(settings: Settings, img: Image.Image | None, prefix: str) -> Picture | None:
    if img is None:
        return None
    key, aspect = thumbs.write_thumb(img, settings.thumbs_dir, prefix)
    return Picture(key, aspect)


def picture_keys(row: Row[Any]) -> set[str]:
    """All thumbnail keys a world uses: its cover and its chapter pictures."""
    keys = {p.key for p in chapter_pictures(row) if p is not None}
    if row.cover_key:
        keys.add(row.cover_key)
    return keys


def _delete_pictures(settings: Settings, keys: set[str]) -> None:
    for key in keys:
        thumbs.delete_thumb_files(settings.thumbs_dir, key)


def _write_cover(
    conn: Connection, settings: Settings, row: Row[Any], img: Image.Image | None
) -> None:
    """Replace a world's cover; None removes it."""
    picture = _thumb(settings, img, f"w{row.id}")
    key = picture.key if picture is not None else None
    if row.cover_key and row.cover_key != key:
        thumbs.delete_thumb_files(settings.thumbs_dir, row.cover_key)
    conn.execute(
        update(worlds)
        .where(worlds.c.id == row.id)
        .values(cover_key=key, cover_aspect=picture.aspect if picture is not None else None)
    )


def _chapter_index(row: Row[Any], position: int) -> int:
    if not 1 <= position <= len(content_of(row).chapters):
        raise ApiError(404, "chapter_not_found", world_id=row.id, position=position)
    return position - 1


def _write_chapter_image(
    conn: Connection, settings: Settings, row: Row[Any], position: int, img: Image.Image | None
) -> None:
    """Replace a chapter's picture; None removes it."""
    index = _chapter_index(row, position)
    content, pictures = load_content(row.content)
    old = pictures[index]
    new = _thumb(settings, img, f"w{row.id}c{position}")
    if old is not None and (new is None or new.key != old.key):
        thumbs.delete_thumb_files(settings.thumbs_dir, old.key)
    pictures[index] = new
    conn.execute(
        update(worlds)
        .where(worlds.c.id == row.id)
        .values(content=stored_content(content, pictures))
    )


def import_pack(
    conn: Connection,
    settings: Settings,
    now: datetime,
    pack: Pack,
    pictures: PackPictures,
    dry_run: bool,
) -> ImportReport:
    """Create a world, or replace the content and pictures of the world with the same slug
    (its id, queue entries and done rows stay; pictures it no longer uses are deleted)."""
    content = pack.content()
    warnings = pack_warnings(content)
    existing = conn.execute(select(worlds).where(worlds.c.slug == pack.slug)).first()
    world_id: int | None = existing.id if existing is not None else None
    if not dry_run:
        values: dict[str, Any] = {
            "age_band": pack.age_band,
            "color": pack.color,
            "updated_at": to_db(now),
        }
        if existing is not None:
            target = int(existing.id)
        else:
            target = insert_id(
                conn.execute(
                    insert(worlds).values(
                        slug=pack.slug,
                        created_at=to_db(now),
                        content=stored_content(content, []),
                        **values,
                    )
                )
            )
        world_id = target
        cover = _thumb(settings, pictures.cover, f"w{target}")
        chapters = [
            _thumb(settings, img, f"w{target}c{pos}")
            for pos, img in enumerate(pictures.chapters, start=1)
        ]
        conn.execute(
            update(worlds)
            .where(worlds.c.id == target)
            .values(
                content=stored_content(content, chapters),
                cover_key=cover.key if cover is not None else None,
                cover_aspect=cover.aspect if cover is not None else None,
                **values,
            )
        )
        if existing is not None:
            unused = picture_keys(existing) - picture_keys(_world_row(conn, target))
            _delete_pictures(settings, unused)
    return ImportReport(
        slug=pack.slug,
        world_id=world_id,
        replaced=existing is not None,
        chapters=len(content.chapters),
        tasks=task_count(content),
        complete=is_complete(warnings),
        warnings=warnings,
    )


# ---- activities: settings, queue ----------------------------------------------------


@router.get("/activities", response_model=ActivitiesAdminOut)
def get_activities(
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> ActivitiesAdminOut:
    """Settings, the queue with its progress, worlds, the last 14 days, examples."""
    return admin_state(conn, local_now(clock, settings), settings)


@router.put("/activities/settings", response_model=ActivitiesAdminOut)
def put_settings(
    body: SettingsIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> ActivitiesAdminOut:
    """The child's level (§14.1), the opening times (§14.4a), the ideas per chapter, the
    daily limit of new chapters (§13.2) and the stars for a finished chapter (§14.4); omitted
    = kept, `max_new_per_day: null` = no limit.
    Chapters already open stay open; a change applies from the next idea on."""
    now = local_now(clock, settings)
    compute(conn, DEFAULT_PROFILE_ID, now)  # progress up to now under the old settings
    values: dict[str, Any] = {}
    if body.chapter_needed is not None:
        values["chapter_needed"] = body.chapter_needed
    if "max_new_per_day" in body.model_fields_set:
        values["max_new_per_day"] = body.max_new_per_day
    if body.chapter_stars is not None:
        values["chapter_stars"] = body.chapter_stars
    if body.level is not None:
        values["level"] = body.level
    if body.windows is not None:
        values["windows"] = [w.model_dump(by_alias=True) for w in body.windows]
    if values:
        conn.execute(
            update(activity_settings)
            .where(activity_settings.c.profile_id == DEFAULT_PROFILE_ID)
            .values(**values)
        )
    return admin_state(conn, now, settings)


@router.put("/activities/queue", response_model=ActivitiesAdminOut)
def put_queue(
    body: QueueIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> ActivitiesAdminOut:
    """The story queue. A new first world starts today; reordering later ones keeps the
    dates of the running one (§13.2 rule 7). `langs` sets the story language of queued worlds
    (§14.7); 422 lang_not_in_world for a language the world has no texts in."""
    if len(set(body.world_ids)) != len(body.world_ids):
        raise ApiError(422, "queue_duplicate")
    known = set(conn.execute(select(worlds.c.id)).scalars().all())
    missing = [i for i in body.world_ids if i not in known]
    if missing:
        raise ApiError(422, "world_not_found", world_id=missing[0])
    langs = body.langs or {}
    rows = world_rows(conn) if langs else {}
    for world_id, lang in langs.items():
        if world_id not in body.world_ids:
            raise ApiError(422, "lang_not_queued", world_id=world_id)
        if lang not in world_langs(rows[world_id]):
            raise ApiError(422, "lang_not_in_world", world_id=world_id, lang=lang)
    now = local_now(clock, settings)
    _set_queue(conn, now, list(body.world_ids), langs)
    return admin_state(conn, now, settings)


# ---- worlds -------------------------------------------------------------------------------


def get_pack_folder(settings: Settings = Depends(get_settings)) -> webdav.PackFolder | None:
    """The WebDAV folder of pack zips (§13.5a), or None when PACKS_WEBDAV_URL is empty."""
    if not settings.packs_webdav_url:
        return None
    return webdav.PackFolder(
        settings.packs_webdav_url, settings.packs_webdav_user, settings.packs_webdav_password
    )


def _folder(folder: webdav.PackFolder | None) -> webdav.PackFolder:
    if folder is None:
        raise ApiError(409, "webdav_not_configured")
    return folder


@router.get("/worlds/webdav", response_model=WebDavPacksOut)
async def list_webdav_packs(
    _: Admin = Depends(require_admin),
    folder: webdav.PackFolder | None = Depends(get_pack_folder),
) -> WebDavPacksOut:
    """The pack zips in the WebDAV folder (§13.5a), by name. Nothing is imported."""
    try:
        files = await _folder(folder).list()
    except webdav.WebDavError as exc:
        raise ApiError(exc.status_code, exc.reason, **exc.params) from exc
    return WebDavPacksOut(
        packs=[WebDavPack(name=f.name, size=f.size, modified=f.modified) for f in files]
    )


@router.get("/worlds/{world_id}", response_model=WorldFull)
def get_world(
    world_id: int,
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> WorldFull:
    """The world for the editor, with the audio of its rendered spoken fields (§14.3)."""
    return world_full(_world_row(conn, world_id), settings)


@router.put("/worlds/import", response_model=ImportReport)
async def import_world(
    request: Request,
    dry_run: bool = Query(default=False),
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
    worker: speech.SpeechWorker = Depends(get_worker),
) -> ImportReport:
    """A story pack: a zip with world.json, pictures and optionally its recordings
    (`speech/`), or world.json alone (§13.5). With dry_run=true it is only checked. An
    existing slug is replaced in place. The recordings are stored, and the spoken texts
    without one are queued for rendering (§14.3)."""
    body = await spool_body(request, settings, MAX_PACK_BYTES, "pack_too_large")
    with body:
        return _import_spooled(body, conn, settings, clock, worker, dry_run)


def _import_spooled(
    body: IO[bytes],
    conn: Connection,
    settings: Settings,
    clock: Clock,
    worker: speech.SpeechWorker,
    dry_run: bool,
) -> ImportReport:
    """Check and (unless dry_run) import a pack zip or world.json, from an upload or from
    the WebDAV folder."""
    with open_pack(body) as parsed:
        if not dry_run:
            install_recordings(settings, parsed)
        report = import_pack(conn, settings, clock.now(), parsed.pack, parsed.pictures, dry_run)
    if not dry_run:
        worker.enqueue_world(parsed.pack.content())
    return report.model_copy(update={"recordings": len(parsed.recordings)})


@router.post("/worlds/webdav/import", response_model=ImportReport)
async def import_webdav_pack(
    name: str = Query(max_length=webdav.MAX_NAME),
    dry_run: bool = Query(default=False),
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
    worker: speech.SpeechWorker = Depends(get_worker),
    folder: webdav.PackFolder | None = Depends(get_pack_folder),
) -> ImportReport:
    """Download one pack zip from the WebDAV folder and check or import it exactly like an
    upload to `/worlds/import` (§13.4, §13.5a): same limits, same report."""
    source = _folder(folder)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    body: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # noqa: SIM115
        max_size=SPOOL_IN_MEMORY, dir=str(settings.data_dir)
    )
    with body:
        try:
            await source.download(name, body, MAX_PACK_BYTES)
        except webdav.WebDavError as exc:
            raise ApiError(exc.status_code, exc.reason, **exc.params) from exc
        return _import_spooled(body, conn, settings, clock, worker, dry_run)


@router.post("/worlds/examples/{slug}", response_model=ImportReport)
def import_example(
    slug: str,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
    worker: speech.SpeechWorker = Depends(get_worker),
) -> ImportReport:
    """Import one of the example packs (a folder, §13.5), like an upload. Its recordings
    (`speech/`) are copied first, so only the texts without one are queued (§14.3)."""
    example = example_packs(settings).get(slug)
    if example is None:
        raise ApiError(404, "example_not_found", slug=slug)
    pack, pictures = load_folder_pack(example.folder)
    report = import_pack(conn, settings, clock.now(), pack, pictures, dry_run=False)
    speech.install_recorded(settings, example.folder, pack.content())
    worker.enqueue_world(pack.content())
    return report


@router.put("/worlds/{world_id}", response_model=WorldFull)
def put_world(
    world_id: int,
    body: Pack,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
    worker: speech.SpeechWorker = Depends(get_worker),
) -> WorldFull:
    """Save the editor: texts, chapters, age band and colour. The slug can't change; the cover
    and chapter pictures are set separately and stay by position (`cover`/`image` names in
    the body are ignored, and so are the output-only `audio` maps). Changed spoken texts are
    queued for rendering (§14.3)."""
    row = _world_row(conn, world_id)
    if body.slug != row.slug:
        raise ApiError(422, "slug_mismatch", slug=row.slug)
    content = body.content()
    pictures = chapter_pictures(row)
    kept = pictures[: len(content.chapters)]
    _delete_pictures(settings, {p.key for p in pictures[len(kept) :] if p is not None})
    conn.execute(
        update(worlds)
        .where(worlds.c.id == world_id)
        .values(
            age_band=body.age_band,
            color=body.color,
            content=stored_content(content, kept),
            updated_at=to_db(clock.now()),
        )
    )
    worker.enqueue_world(content)
    return world_full(_world_row(conn, world_id), settings)


@router.put("/worlds/{world_id}/cover", response_model=WorldFull)
async def upload_world_cover(
    world_id: int,
    request: Request,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> WorldFull:
    row = _world_row(conn, world_id)
    data = await read_body(request, thumbs.MAX_IMAGE_UPLOAD_BYTES, "image_too_large")
    try:
        img = thumbs.open_image(data)
    except thumbs.MediaError as exc:
        raise ApiError(422, exc.reason) from exc
    _write_cover(conn, settings, row, img)
    return world_full(_world_row(conn, world_id), settings)


@router.delete("/worlds/{world_id}/cover", response_model=WorldFull)
def delete_world_cover(
    world_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> WorldFull:
    _write_cover(conn, settings, _world_row(conn, world_id), None)
    return world_full(_world_row(conn, world_id), settings)


@router.put("/worlds/{world_id}/chapters/{position}/image", response_model=WorldFull)
async def upload_chapter_image(
    world_id: int,
    position: int,
    request: Request,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> WorldFull:
    """A chapter's picture (raw image body, like the cover). 404 for an unknown chapter."""
    row = _world_row(conn, world_id)
    _chapter_index(row, position)
    data = await read_body(request, thumbs.MAX_IMAGE_UPLOAD_BYTES, "image_too_large")
    try:
        img = thumbs.open_image(data)
    except thumbs.MediaError as exc:
        raise ApiError(422, exc.reason) from exc
    _write_chapter_image(conn, settings, row, position, img)
    return world_full(_world_row(conn, world_id), settings)


@router.delete("/worlds/{world_id}/chapters/{position}/image", response_model=WorldFull)
def delete_chapter_image(
    world_id: int,
    position: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> WorldFull:
    _write_chapter_image(conn, settings, _world_row(conn, world_id), position, None)
    return world_full(_world_row(conn, world_id), settings)


@router.delete("/worlds/{world_id}", status_code=204)
def delete_world(
    world_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> None:
    """Deletes the world, its done rows (FK cascade), cover and chapter pictures, and removes
    it from the queue."""
    row = _world_row(conn, world_id)
    now = local_now(clock, settings)
    sched = compute(conn, DEFAULT_PROFILE_ID, now)[0]
    if world_id in sched.queue:
        _set_queue(conn, now, [i for i in sched.queue if i != world_id])
    _delete_pictures(settings, picture_keys(row))
    conn.execute(delete(activity_done).where(activity_done.c.world_id == world_id))
    conn.execute(delete(worlds).where(worlds.c.id == world_id))


@router.get(
    "/worlds/{world_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
def export_world(
    world_id: int,
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """The world as a pack zip (§13.4): world.json plus cover.webp and chapter-<n>.webp for
    the pictures it has, so export -> import keeps them."""
    row = _world_row(conn, world_id)
    content, pictures = load_content(row.content)
    files: dict[str, bytes] = {}

    def add(key: str | None, name: str) -> str | None:
        if not key:
            return None
        path = settings.thumbs_dir / f"{key}-lg.webp"
        if not path.is_file():
            return None
        files[name] = path.read_bytes()
        return name

    cover = add(row.cover_key, "cover.webp")
    chapters = [
        chapter.model_copy(
            update={"image": add(picture.key if picture else None, f"chapter-{pos}.webp")}
        )
        for pos, (chapter, picture) in enumerate(
            zip(content.chapters, pictures, strict=True), start=1
        )
    ]
    pack = Pack(
        slug=row.slug,
        age_band=row.age_band,
        color=row.color,
        cover=cover,
        texts=content.texts,
        chapters=chapters,
    )
    world_json = json.dumps(pack.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("world.json", world_json)
        for name, data in files.items():
            zf.writestr(name, data, compress_type=zipfile.ZIP_STORED)  # WebP is compressed
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{row.slug}.zip"'},
    )


# ---- done activities and photos (§15.3, §17) -------------------------------------------------

DONE_PAGE = 30
DONE_PAGE_MAX = 100
PHOTO_CACHE = "private, max-age=31536000, immutable"  # the key holds a content hash


@dataclass(frozen=True)
class DoneStars:
    """The stars of a done task (§17.3)."""

    held: int  # what it holds: earn, chapter and bonus rows, minus what was taken back
    bonus: bool  # its bonus star was given
    chapter: bool  # it holds its chapter's stars
    chapter_holder: int | None  # the done id that holds the chapter's stars, if any
    chapter_stars: int  # what that holder still holds of them


def _done_stars(conn: Connection, profile_id: int, rows: list[Row[Any]]) -> dict[int, DoneStars]:
    """{done id: its stars} for these done rows, with their chapters' awards (§14.4)."""
    chapters: dict[tuple[int, int], list[int]] = {}
    for r in rows:
        key = (r.world_id, r.position)
        if key not in chapters:
            chapters[key] = list(chapter_done(conn, profile_id, r.world_id, r.position).values())
    ledger = star_rows(conn, profile_id, {i for ids in chapters.values() for i in ids})
    holder = {key: active_chapter_award(ledger, ids) for key, ids in chapters.items()}
    out: dict[int, DoneStars] = {}
    for r in rows:
        h = holder[(r.world_id, r.position)]
        chapter_rows = [(k, ref, d) for k, ref, d in ledger if k in (CHAPTER_KIND, REOPEN_KIND)]
        out[r.id] = DoneStars(
            held=held_stars(ledger, r.id),
            bonus=any(k == BONUS_KIND and ref == r.id for k, ref, _ in ledger),
            chapter=h == r.id,
            chapter_holder=h,
            chapter_stars=held_stars(chapter_rows, h) if h is not None else 0,
        )
    return out


def _photo_url(key: str, size: photos.PhotoSize) -> str:
    return f"/api/admin/photos/{key}.webp?size={size}"


def done_entries(
    conn: Connection, profile_id: int, rows: list[Row[Any]], default_lang: Lang
) -> list[DoneOut]:
    """The admin's view of done rows (§17.3): world name and task title in the story language
    (§14.7; a task the world no longer has keeps its photo's snapshot title), stars, bonus
    and photos."""
    ids = [r.id for r in rows]
    stars = _done_stars(conn, profile_id, rows)
    by_done: dict[int, list[Row[Any]]] = {}
    if ids:
        for ph in conn.execute(
            select(activity_photos)
            .where(activity_photos.c.profile_id == profile_id, activity_photos.c.done_id.in_(ids))
            .order_by(activity_photos.c.id)
        ).all():
            by_done.setdefault(ph.done_id, []).append(ph)
    world_ids = {r.world_id for r in rows}
    world_of = {
        w.id: w for w in conn.execute(select(worlds).where(worlds.c.id.in_(world_ids))).all()
    }
    chosen = langs_of(ensure_settings(conn, profile_id))
    out: list[DoneOut] = []
    for r in rows:
        shots = [ph for ph in by_done.get(r.id, []) if ph.key]  # key "" = upload under way
        content = content_of(world_of[r.world_id])
        lang = story_lang(chosen.get(r.world_id), content.texts, default_lang)
        title = shots[0].task_title if shots else ""
        if r.position <= len(content.chapters):
            task = next(
                (t for t in content.chapters[r.position - 1].tasks if t.kind == r.kind), None
            )
            if task is not None:
                title = pick_lang(task.texts, lang).title
        out.append(
            DoneOut(
                id=r.id,
                world_id=r.world_id,
                world_name=pick_lang(content.texts, lang).name,
                position=r.position,
                kind=r.kind,
                task_title=title,
                done_at=from_db(r.done_at),
                attempts=r.attempts,
                stars=stars[r.id].held,
                bonus=stars[r.id].bonus,
                chapter=stars[r.id].chapter,
                reopen_stars=stars[r.id].held
                + (stars[r.id].chapter_stars if not stars[r.id].chapter else 0),
                photos=[
                    DonePhotoOut(
                        id=ph.id,
                        url_sm=_photo_url(ph.key, "sm"),
                        url_lg=_photo_url(ph.key, "lg"),
                        taken_at=from_db(ph.taken_at),
                    )
                    for ph in shots
                ],
            )
        )
    return out


def _done_row(conn: Connection, done_id: int) -> Row[Any]:
    row = conn.execute(
        select(activity_done).where(
            activity_done.c.id == done_id,
            activity_done.c.profile_id == DEFAULT_PROFILE_ID,
        )
    ).first()
    if row is None:
        raise ApiError(404, "unknown_done", done_id=done_id)
    return row


@router.get("/activities/done", response_model=DoneListOut)
def list_done(
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=DONE_PAGE, ge=1, le=DONE_PAGE_MAX),
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
) -> DoneListOut:
    """The done tasks (§17.2, §17.3), newest first, a page at a time: `before_id` = the
    previous page's `next_before_id`."""
    query = select(activity_done).where(activity_done.c.profile_id == DEFAULT_PROFILE_ID)
    if before_id is not None:
        query = query.where(activity_done.c.id < before_id)
    rows = conn.execute(query.order_by(activity_done.c.id.desc()).limit(limit + 1)).all()
    page = list(rows[:limit])
    return DoneListOut(
        entries=done_entries(conn, DEFAULT_PROFILE_ID, page, settings.default_locale),
        next_before_id=page[-1].id if len(rows) > limit else None,
    )


@router.post("/activities/done/{done_id}/bonus", response_model=DoneOut)
def give_bonus(
    done_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> DoneOut:
    """One bonus star per done task, every kind (§17.3): a ledger row `bonus`, +1, `ref_id`
    = the done id; it counts as earned. Again -> 409 bonus_given; 404 unknown_done (checked
    and written as one atomic step)."""
    begin_immediate(conn)
    row = _done_row(conn, done_id)
    if _done_stars(conn, DEFAULT_PROFILE_ID, [row])[done_id].bonus:
        raise ApiError(409, "bonus_given", done_id=done_id)
    conn.execute(
        insert(reward_ledger).values(
            profile_id=DEFAULT_PROFILE_ID,
            delta=1,
            kind=BONUS_KIND,
            ref_id=done_id,
            created_at=to_db(clock.now()),
        )
    )
    return done_entries(conn, DEFAULT_PROFILE_ID, [row], settings.default_locale)[0]


@router.delete("/activities/done/{done_id}", status_code=204)
def reopen_done(
    done_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> None:
    """Open again (§17.3): in one transaction, deletes the done row and its photo rows and
    books a `reopen` row of -min(its stars, the balance), `ref_id` = the done id (none for 0),
    so the balance never goes below 0. When another task of the chapter holds the chapter's
    stars (§14.4), they go back too: a `reopen` row with that task's id (also for 0 stars,
    so the chapter is awarded again once it is finished again). The photo files go after the
    commit (best effort: a leftover file is harmless, a missing one for a kept row would not
    be). The tablet sees the task open on its next refresh; its sticker goes with the
    chapter's last done task. 404 unknown_done."""
    begin_immediate(conn)
    row = _done_row(conn, done_id)
    st = _done_stars(conn, DEFAULT_PROFILE_ID, [row])[done_id]
    now = to_db(clock.now())
    balance_now = stars_of(conn, DEFAULT_PROFILE_ID)
    delta = reopen_delta(st.held, balance_now)
    if st.chapter_holder is not None and st.chapter_holder != done_id:
        conn.execute(
            insert(reward_ledger).values(
                profile_id=DEFAULT_PROFILE_ID,
                delta=reopen_delta(st.chapter_stars, balance_now + delta),
                kind=REOPEN_KIND,
                ref_id=st.chapter_holder,
                created_at=now,
            )
        )
    keys = [
        k
        for k in conn.execute(
            select(activity_photos.c.key).where(activity_photos.c.done_id == done_id)
        ).scalars()
        if k
    ]
    conn.execute(delete(activity_photos).where(activity_photos.c.done_id == done_id))
    conn.execute(delete(activity_done).where(activity_done.c.id == done_id))
    if delta:
        conn.execute(
            insert(reward_ledger).values(
                profile_id=DEFAULT_PROFILE_ID,
                delta=delta,
                kind=REOPEN_KIND,
                ref_id=done_id,
                created_at=now,
            )
        )
    conn.commit()
    for key in keys:
        with contextlib.suppress(OSError):
            photos.delete_photo_files(settings.photos_dir, key)


@router.get(
    "/photos/{key}.webp",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
def serve_photo(
    key: str,
    size: photos.PhotoSize = Query(default="sm"),
    _: Admin = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> Response:
    """A photo's file, for the admin only (the device can't read photos back). Like media
    via X-Accel-Redirect from `/_media/_photos/`; with ACCEL_REDIRECT=false the app serves it.
    The key format is checked strictly, which also rules out paths."""
    if not photos.PHOTO_KEY_RE.match(key):
        raise ApiError(404, "photo_not_found")
    path = photos.photo_file(settings.photos_dir, key, size)
    if not path.is_file():
        raise ApiError(404, "photo_not_found")
    if settings.accel_redirect:
        return Response(
            headers={
                "X-Accel-Redirect": f"/_media/_photos/{path.name}",
                "Cache-Control": PHOTO_CACHE,
            },
            media_type="image/webp",
        )
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": PHOTO_CACHE})
