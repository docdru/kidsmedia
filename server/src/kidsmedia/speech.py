"""Read aloud on the server (SPEC §14.3): Piper voices, the spoken fields of a world, the
content-addressed speech files and the background worker that renders them.

Every spoken text becomes `data/media/_speech/<key>.m4a` with
`key = sha256(voice id + "\\n" + spoken text)[:20]`, so identical texts share a file and only
changed texts are rendered again. Files need no table; the worker keeps its state in memory.
"""

import hashlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import Connection, select

from kidsmedia.activities import (
    LANGS,
    ChapterTexts,
    Kind,
    Lang,
    Level,
    Problem,
    Question,
    TaskTexts,
    WorldContent,
    WorldTexts,
    load_content,
)
from kidsmedia.config import Settings
from kidsmedia.db import activity_settings, worlds

log = logging.getLogger("kidsmedia")

KEY_LENGTH = 20
KEY_RE = re.compile(r"^[0-9a-f]{20}$")
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
MANIFEST = Path(__file__).resolve().parent / "voices.json"
DEVICE_PREFIX = "/api/speech/"
ADMIN_PREFIX = "/api/admin/speech/"
RENDER_TIMEOUT = 300  # seconds per file, for Piper and for ffmpeg
TEMP_PREFIX = ".render-"  # temporary folders in data/media/_speech while a file is rendered
NICE = "10"  # Piper runs below the app's priority, so playback requests stay quick


# ---- keys and spoken fields (pure) --------------------------------------------------------


def speech_key(voice_id: str, text: str) -> str:
    """The file key of a spoken text in a voice: the first 20 hex characters of the sha256."""
    digest = hashlib.sha256(f"{voice_id}\n{text}".encode()).hexdigest()
    return digest[:KEY_LENGTH]


def spoken(shown: str, say: Mapping[Any, str] | None, field: str) -> str:
    """The spoken form of a field: its `say` override, else the shown text (§14.3)."""
    return ((say or {}).get(field) or shown).strip()


def _fields(block: Any, names: Iterable[str]) -> dict[str, str]:
    """The spoken form of each non-empty field of a text block, by field name."""
    say: Mapping[Any, str] | None = block.say
    out: dict[str, str] = {}
    for name in names:
        shown: str = getattr(block, name)
        text = spoken(shown, say, name) if shown else ""
        if text:
            out[name] = text
    return out


def world_fields(t: WorldTexts) -> dict[str, str]:
    """A world's spoken fields: its name (§14.3)."""
    return _fields(t, ("name",))


def chapter_fields(t: ChapterTexts) -> dict[str, str]:
    return _fields(t, ("title", "intro", "outro"))


def level_field(t: TaskTexts, level: Level) -> Literal["text", "easier", "harder"]:
    """The field the level's text comes from; a missing level text falls back to `text`."""
    if level == "easier" and t.easier:
        return "easier"
    if level == "harder" and t.harder:
        return "harder"
    return "text"


def task_fields(kind: Kind, t: TaskTexts, level: Level) -> dict[str, str]:
    """The tablet's view (§14.1): `title`, and `text` = the level's text. A read task's text is
    the reading text, which isn't spoken (§14.2)."""
    out = _fields(t, ("title",))
    if kind != "read":
        text = _fields(t, (level_field(t, level),))
        if text:
            out["text"] = next(iter(text.values()))
    return out


def task_editor_fields(kind: Kind, t: TaskTexts) -> dict[str, str]:
    """The editor's view: `title` and, except for read tasks, all three level texts."""
    names = ("title",) if kind == "read" else ("title", "text", "easier", "harder")
    return _fields(t, names)


def question_texts(q: Question) -> tuple[str, list[str]]:
    """A question and its answers as spoken (questions have no `say`)."""
    return q.question.strip(), [a.strip() for a in q.answers]


def problem_texts(p: Problem) -> tuple[str, str]:
    """A maths problem and its hint as spoken (§15.2; problems have no `say`)."""
    return p.text.strip(), p.hint.strip()


def spoken_texts(content: WorldContent) -> list[tuple[Lang, str]]:
    """Everything spoken in a world, every level, in reading order, without duplicates:
    world name; chapter title, intro, outro; task title and texts; for read tasks the title
    and the questions with their answers, not the reading text (§14.3); for calc tasks also
    the problems and their hints (§15.2)."""
    out: list[tuple[Lang, str]] = []
    seen: set[tuple[Lang, str]] = set()

    def add(lang: Lang, texts: Iterable[str]) -> None:
        for text in texts:
            if text and (lang, text) not in seen:
                seen.add((lang, text))
                out.append((lang, text))

    for lang, wt in content.texts.items():
        add(lang, world_fields(wt).values())
    for chapter in content.chapters:
        for lang, ct in chapter.texts.items():
            add(lang, chapter_fields(ct).values())
        for task in chapter.tasks:
            for lang, tt in task.texts.items():
                add(lang, task_editor_fields(task.kind, tt).values())
                if task.kind == "read":
                    for questions in (tt.questions or {}).values():
                        for q in questions:
                            question, answers = question_texts(q)
                            add(lang, [question, *answers])
                if task.kind == "calc":
                    for problems in (tt.problems or {}).values():
                        for p in problems:
                            add(lang, problem_texts(p))
    return out


# ---- voices -------------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceInfo:
    """A voice's entry in voices.json: licence and attribution for the admin."""

    id: str
    lang: Lang
    bundled: bool = False
    license: str | None = None
    attribution: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class Voice:
    """A voice that is present: its model file and where it was found."""

    id: str
    lang: Lang
    model: Path  # <id>.onnx, with <id>.onnx.json next to it
    source: Literal["data", "bundled"]
    license: str | None = None
    attribution: str | None = None


def _read_manifest(path: Path) -> list[VoiceInfo]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        log.warning("voices: can't read %s", path, exc_info=True)
        return []
    out: list[VoiceInfo] = []
    for entry in raw.get("voices") or []:
        voice_id = str(entry.get("id", ""))
        lang = entry.get("lang")
        if not VOICE_ID_RE.match(voice_id) or lang not in LANGS:
            continue
        out.append(
            VoiceInfo(
                id=voice_id,
                lang=lang,
                bundled=bool(entry.get("bundled")),
                license=entry.get("license"),
                attribution=entry.get("attribution"),
                url=entry.get("url"),
            )
        )
    return out


def manifest(settings: Settings) -> list[VoiceInfo]:
    """The bundled voices.json, then `data/voices/voices.json` (a parent's own entries win)."""
    by_id: dict[str, VoiceInfo] = {}
    for info in _read_manifest(MANIFEST) + _read_manifest(settings.voices_dir / "voices.json"):
        by_id.pop(info.id, None)
        by_id[info.id] = info
    return list(by_id.values())


def find_model(settings: Settings, voice_id: str) -> tuple[Path, Literal["data", "bundled"]] | None:
    """`<id>.onnx` + `<id>.onnx.json` in data/voices/ first, then in the bundled voices."""
    if not VOICE_ID_RE.match(voice_id) or ".." in voice_id:
        return None
    places: list[tuple[Path | None, Literal["data", "bundled"]]] = [
        (settings.voices_dir, "data"),
        (settings.bundled_voices_dir, "bundled"),
    ]
    for folder, source in places:
        if folder is None:
            continue
        model = folder / f"{voice_id}.onnx"
        if model.is_file() and model.with_name(f"{voice_id}.onnx.json").is_file():
            return model, source
    return None


def chosen_voices(settings: Settings) -> dict[Lang, Voice | None]:
    """One voice per language: VOICE_DE/EN/EL if that voice is present, else the first voice
    of the language in voices.json that is present (JOY before the bundled Greek voice)."""
    infos = manifest(settings)
    by_id = {i.id: i for i in infos}
    wanted: dict[Lang, str] = {
        "de": settings.voice_de,
        "en": settings.voice_en,
        "el": settings.voice_el,
    }
    out: dict[Lang, Voice | None] = {}
    for lang in LANGS:
        candidates = [wanted[lang].strip()] if wanted[lang].strip() else []
        candidates += [i.id for i in infos if i.lang == lang]
        out[lang] = None
        for voice_id in candidates:
            found = find_model(settings, voice_id)
            if found is None:
                continue
            info = by_id.get(voice_id)
            out[lang] = Voice(
                id=voice_id,
                lang=lang,
                model=found[0],
                source=found[1],
                license=info.license if info else None,
                attribution=info.attribution if info else None,
            )
            break
    return out


# ---- files and lookups --------------------------------------------------------------------


def speech_file(settings: Settings, key: str) -> Path:
    return settings.speech_dir / f"{key}.m4a"


class QuestionAudio(BaseModel):
    """The audio of one question (§14.3): None where a text isn't rendered yet."""

    question: str | None
    answers: list[str | None]


class ProblemAudio(BaseModel):
    """The audio of one maths problem (§15.2): None where a text isn't rendered yet."""

    text: str | None
    hint: str | None


class AudioLookup:
    """Finds the rendered files of spoken texts, for one response. `prefix` is the URL the
    files are served at: the device's or the admin's."""

    def __init__(self, settings: Settings, prefix: str = DEVICE_PREFIX) -> None:
        self.voices = chosen_voices(settings)
        self.folder = settings.speech_dir
        self.prefix = prefix

    def url(self, lang: Lang, text: str) -> str | None:
        voice = self.voices.get(lang)
        if voice is None or not text:
            return None
        key = speech_key(voice.id, text)
        if not (self.folder / f"{key}.m4a").is_file():
            return None
        return f"{self.prefix}{key}.m4a"

    def fields(self, lang: Lang, fields: Mapping[str, str]) -> dict[str, str]:
        """{field: url} for the fields whose file exists."""
        out: dict[str, str] = {}
        for name, text in fields.items():
            url = self.url(lang, text)
            if url is not None:
                out[name] = url
        return out

    def question(self, lang: Lang, q: Question) -> QuestionAudio | None:
        """None when nothing of the question is rendered."""
        question, answers = question_texts(q)
        out = QuestionAudio(
            question=self.url(lang, question), answers=[self.url(lang, a) for a in answers]
        )
        if out.question is None and not any(out.answers):
            return None
        return out

    def problem(self, lang: Lang, p: Problem) -> ProblemAudio:
        """Always an object; a field that isn't rendered is None (§15.2)."""
        text, hint = problem_texts(p)
        return ProblemAudio(text=self.url(lang, text), hint=self.url(lang, hint))


# ---- jobs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    key: str
    voice: Voice
    text: str


def world_jobs(content: WorldContent, voices: Mapping[Lang, Voice | None]) -> list[Job]:
    """One job per spoken text of a world whose language has a voice."""
    out: list[Job] = []
    for lang, text in spoken_texts(content):
        voice = voices.get(lang)
        if voice is not None:
            out.append(Job(speech_key(voice.id, text), voice, text))
    return out


def ordered_contents(conn: Connection) -> tuple[list[WorldContent], bool]:
    """All worlds: those in a queue first (in queue order), then the others by id. A world
    that can't be loaded is logged and skipped; the flag says whether all of them loaded."""
    rows = {r.id: r for r in conn.execute(select(worlds.c.id, worlds.c.content)).all()}
    order: list[int] = []
    queues = conn.execute(
        select(activity_settings.c.queue).order_by(activity_settings.c.profile_id)
    ).scalars()
    for world_id in [int(i) for q in queues for i in q or []] + sorted(rows):
        if world_id in rows and world_id not in order:
            order.append(world_id)
    out: list[WorldContent] = []
    complete = True
    for world_id in order:
        try:
            out.append(load_content(rows[world_id].content)[0])
        except Exception:
            log.warning("speech: can't load world %s", world_id, exc_info=True)
            complete = False
    return out, complete


def all_jobs_checked(conn: Connection, settings: Settings) -> tuple[list[Job], bool]:
    """Every speech file the worlds need, queued worlds first, without duplicates; and
    whether every world loaded (only then is the list complete)."""
    voices = chosen_voices(settings)
    seen: set[str] = set()
    out: list[Job] = []
    contents, complete = ordered_contents(conn)
    for content in contents:
        for job in world_jobs(content, voices):
            if job.key not in seen:
                seen.add(job.key)
                out.append(job)
    return out, complete


def all_jobs(conn: Connection, settings: Settings) -> list[Job]:
    return all_jobs_checked(conn, settings)[0]


# ---- rendering ----------------------------------------------------------------------------


class Renderer(Protocol):
    def __call__(self, voice: Voice, text: str, out: Path) -> None:
        """Write the spoken text as AAC (.m4a, mono, 64 kbit/s) to `out`."""


def piper_command(voice: Voice, wav: Path, text: str) -> list[str]:
    """Piper's CLI (`python -m piper`) in its own process, below the app's priority. Running it
    as a separate program also keeps the GPL-licensed Piper code out of the app's process."""
    return [
        "nice",
        "-n",
        NICE,
        sys.executable,
        "-m",
        "piper",
        "--model",
        str(voice.model),  # finds <id>.onnx.json next to it
        "--output-file",
        str(wav),
        "--",
        " ".join(text.split()),  # one line: Piper reads the text as given, no shell involved
    ]


def piper_env() -> dict[str, str]:
    """The environment for Piper: onnxruntime (and OpenMP) limited to one thread, so a render
    can't take every core of the server."""
    return os.environ | {"OMP_NUM_THREADS": "1", "OMP_WAIT_POLICY": "PASSIVE"}


def ffmpeg_command(wav: Path, out: Path) -> list[str]:
    return [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(wav),
        "-ac",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(out),
    ]


class PiperRenderer:
    """Piper (piper-tts CLI, a separate process) to WAV, then ffmpeg to AAC."""

    def __call__(self, voice: Voice, text: str, out: Path) -> None:
        with tempfile.TemporaryDirectory(dir=out.parent, prefix=TEMP_PREFIX) as tmp:
            wav_path = Path(tmp) / "speech.wav"
            subprocess.run(
                piper_command(voice, wav_path, text),
                capture_output=True,
                check=True,
                timeout=RENDER_TIMEOUT,
                env=piper_env(),
                stdin=subprocess.DEVNULL,
            )
            if not wav_path.is_file() or wav_path.stat().st_size <= 44:  # a bare WAV header
                raise RuntimeError("piper wrote no audio")
            subprocess.run(
                ffmpeg_command(wav_path, out),
                capture_output=True,
                check=True,
                timeout=RENDER_TIMEOUT,
            )


class SpeechWorker:
    """Renders missing speech files in a background thread (§14.3). Jobs are queued after an
    import, an example add, a world save and at start-up. A failed text is logged and kept
    in memory; it is tried again after the next start."""

    def __init__(
        self, settings: Settings, render: Renderer | None = None, enabled: bool | None = None
    ) -> None:
        self.settings = settings
        self.render: Renderer = render if render is not None else PiperRenderer()
        # SPEECH_WORKER=false: nothing is queued (tests turn it on and render with drain()).
        self.enabled = settings.speech_worker if enabled is None else enabled
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._queued: set[str] = set()
        self._failed: dict[str, str] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # -- state

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def queued(self) -> list[str]:
        """Keys waiting to be rendered."""
        with self._lock:
            return sorted(self._queued)

    def failed(self) -> dict[str, str]:
        """Failed keys and their error, since the start."""
        with self._lock:
            return dict(self._failed)

    # -- queueing

    def enqueue(self, jobs: Iterable[Job]) -> int:
        """Queue the jobs whose file is missing and that aren't queued or failed. Returns how
        many were added; none when the worker is turned off."""
        if not self.enabled:
            return 0
        added = 0
        with self._lock:
            for job in jobs:
                if job.key in self._queued or job.key in self._failed:
                    continue
                if speech_file(self.settings, job.key).is_file():
                    continue
                self._queued.add(job.key)
                self._queue.put(job)
                added += 1
        return added

    def enqueue_world(self, content: WorldContent) -> int:
        return self.enqueue(world_jobs(content, chosen_voices(self.settings)))

    def enqueue_all(self, conn: Connection) -> int:
        return self.enqueue(all_jobs(conn, self.settings))

    def prune(self, conn: Connection) -> int:
        """At start-up, before the worker runs: delete leftover temporary files and folders of
        an interrupted render, and the `.m4a` files no world speaks any more (only when every
        world loaded, so a broken world never costs its files). Returns how many entries
        were deleted."""
        folder = self.settings.speech_dir
        if not folder.is_dir():
            return 0
        jobs, complete = all_jobs_checked(conn, self.settings)
        wanted = {f"{job.key}.m4a" for job in jobs}
        deleted = 0
        for entry in folder.iterdir():
            try:
                if _leftover(entry, wanted if complete else None):
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                    deleted += 1
            except OSError:
                log.warning("speech: can't delete %s", entry, exc_info=True)
        if deleted:
            log.info("speech: deleted %d unused or temporary files", deleted)
        return deleted

    # -- rendering

    def run_one(self, job: Job) -> bool:
        """Render one job to a temporary file and move it into place. False on failure."""
        dest = speech_file(self.settings, job.key)
        tmp = dest.with_name(f".{job.key}.tmp")
        try:
            if not dest.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                self.render(job.voice, job.text, tmp)
                tmp.replace(dest)
            return True
        except Exception as exc:
            reason = _describe(exc)
            log.warning("speech %s (%s) failed: %s", job.key, job.voice.id, reason)
            with self._lock:
                self._failed[job.key] = reason
            return False
        finally:
            tmp.unlink(missing_ok=True)
            with self._lock:
                self._queued.discard(job.key)

    def _jobs(self, block: bool) -> Iterator[Job]:
        while not self._stopping.is_set():
            try:
                job = self._queue.get(block=block)
            except queue.Empty:
                return
            try:
                if job is None:
                    return
                yield job
            finally:
                self._queue.task_done()

    def drain(self) -> None:
        """Render everything queued, in this thread (tests, tools)."""
        for job in self._jobs(block=False):
            self.run_one(job)

    def join(self) -> None:
        """Wait until the background thread has rendered everything queued so far."""
        self._queue.join()

    def _loop(self) -> None:
        for job in self._jobs(block=True):
            self.run_one(job)

    def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._loop, name="speech", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop after the file being rendered; what is still queued is done at the next start."""
        if self._thread is None:
            return
        self._stopping.set()
        self._queue.put(None)  # wakes the thread if it waits for work
        self._thread.join(timeout)
        self._thread = None


def _leftover(entry: Path, wanted: set[str] | None) -> bool:
    """A temporary folder or file of an interrupted render, or (with `wanted`, the complete
    list of needed file names) a speech file no world speaks any more."""
    name = entry.name
    if entry.is_dir():
        return name.startswith(TEMP_PREFIX) or name.startswith("tmp")
    if name.startswith(".") and name.endswith(".tmp"):
        return True
    return wanted is not None and name.endswith(".m4a") and name not in wanted


def _program(cmd: Any) -> str:
    args = [str(a) for a in cmd] if isinstance(cmd, list | tuple) else [str(cmd)]
    return "piper" if "piper" in args else Path(args[0]).name if args else "?"


def _describe(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else ""
        return f"{_program(exc.cmd)} exit {exc.returncode}: {stderr[:200]}"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"{_program(exc.cmd)} timed out after {exc.timeout:.0f} s"
    return f"{type(exc).__name__}: {exc}"[:300]


# ---- status (admin) -----------------------------------------------------------------------


class SpeechVoiceOut(BaseModel):
    lang: Lang
    voice: str | None  # None = no voice for this language: the tablet's TextToSpeech is used
    source: Literal["data", "bundled"] | None
    license: str | None
    attribution: str | None


class SpeechStatus(BaseModel):
    voices: list[SpeechVoiceOut]
    rendered: int
    pending: int
    failed: int
    worker: bool  # the background worker runs


def status(conn: Connection, settings: Settings, worker: SpeechWorker) -> SpeechStatus:
    """Voices and file counts over every spoken text of every world."""
    voices = chosen_voices(settings)
    failed = worker.failed()
    rendered = pending = failures = 0
    for job in all_jobs(conn, settings):
        if speech_file(settings, job.key).is_file():
            rendered += 1
        elif job.key in failed:
            failures += 1
        elif worker.enabled:  # turned off: nothing is waiting to be rendered
            pending += 1
    return SpeechStatus(
        voices=[voice_out(lang, voices[lang]) for lang in LANGS],
        rendered=rendered,
        pending=pending,
        failed=failures,
        worker=worker.running,
    )


def voice_out(lang: Lang, voice: Voice | None) -> SpeechVoiceOut:
    if voice is None:
        return SpeechVoiceOut(lang=lang, voice=None, source=None, license=None, attribution=None)
    return SpeechVoiceOut(
        lang=lang,
        voice=voice.id,
        source=voice.source,
        license=voice.license,
        attribution=voice.attribution,
    )


def log_voices(settings: Settings) -> None:
    """At start-up: which voice each language uses, and wanted voices that are missing."""
    chosen = chosen_voices(settings)
    for lang in LANGS:
        voice = chosen[lang]
        wanted = getattr(settings, f"voice_{lang}").strip()
        if wanted and (voice is None or voice.id != wanted):
            log.warning("speech: voice %s for %s is not in data/voices or the image", wanted, lang)
        if voice is None:
            log.info("speech: no voice for %s; the tablet's TextToSpeech is used", lang)
        else:
            log.info("speech: %s uses %s (%s)", lang, voice.id, voice.source)
