"""Read aloud on the server (SPEC §14.3): keys, spoken fields, voices, the worker and the
speech endpoints. Piper and ffmpeg are faked: nothing is synthesised here."""

import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from kidsmedia import speech
from kidsmedia.activities import ChapterTexts, Pack, TaskTexts
from kidsmedia.config import Settings
from kidsmedia.db import make_engine, worlds
from kidsmedia.speech import Voice, speech_key
from tests.conftest import ADMIN_PASSWORD
from tests.test_activities_api import import_json, pack, queued_world
from tests.test_play import bearer

BUNDLED = {"de": "de_DE-thorsten-medium", "en": "en_GB-cori-medium", "el": "el_GR-rapunzelina-low"}
JOY = "el_GR-joy-medium"


def fake_voice(folder: Path, voice_id: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{voice_id}.onnx").write_bytes(b"not a model")
    (folder / f"{voice_id}.onnx.json").write_text("{}")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """As in conftest, plus the three bundled voices (fake files)."""
    bundled = tmp_path / "bundled-voices"
    for voice_id in BUNDLED.values():
        fake_voice(bundled, voice_id)
    return Settings(
        data_dir=tmp_path / "data",
        tz="Europe/Berlin",
        accel_redirect=False,
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        admin_dist=None,
        bundled_voices_dir=bundled,
        speech_worker=False,
    )


def fake_render(voice: Voice, text: str, out: Path) -> None:
    out.write_bytes(f"m4a {voice.id} {text}".encode())


def render(settings: Settings, lang: str, text: str) -> str:
    """Put a 'rendered' file for a text in the language's bundled voice; returns its key."""
    key = speech_key(BUNDLED[lang], text)
    settings.speech_dir.mkdir(parents=True, exist_ok=True)
    (settings.speech_dir / f"{key}.m4a").write_bytes(b"m4a")
    return key


def worker_of(app: FastAPI) -> speech.SpeechWorker:
    """The app's worker, turned on (but without its thread: tests render with drain())."""
    worker: speech.SpeechWorker = app.state.speech
    worker.render = fake_render
    worker.enabled = True
    return worker


def all_keys(body: dict[str, Any], voices: dict[str, str] = BUNDLED) -> set[str]:
    """The keys of everything a pack speaks, in these voices."""
    content = Pack.model_validate(body).content()
    return {speech_key(voices[lang], text) for lang, text in speech.spoken_texts(content)}


# ---- keys and spoken fields ---------------------------------------------------------------


def test_the_key_is_the_start_of_the_sha256_of_voice_and_text() -> None:
    key = speech_key("de_DE-thorsten-medium", "Hallo")
    assert key == hashlib.sha256(b"de_DE-thorsten-medium\nHallo").hexdigest()[:20]
    assert speech.KEY_RE.match(key)
    assert speech_key("en_GB-cori-medium", "Hallo") != key  # another voice, another file


def test_the_spoken_form_wins_for_its_field_only() -> None:
    t = TaskTexts(
        title="Zeichne",
        text="Male 3 Äpfel",
        easier="Male 1 Apfel",
        harder="",
        say={"text": "Male drei Äpfel"},
    )
    assert speech.task_fields("draw", t, "normal") == {
        "title": "Zeichne",
        "text": "Male drei Äpfel",
    }
    assert speech.task_fields("draw", t, "easier")["text"] == "Male 1 Apfel"
    # No harder text: falls back to `text`, and so does its spoken form (§14.1).
    assert speech.task_fields("draw", t, "harder")["text"] == "Male drei Äpfel"
    assert speech.task_editor_fields("draw", t) == {
        "title": "Zeichne",
        "text": "Male drei Äpfel",
        "easier": "Male 1 Apfel",
    }
    chapter = ChapterTexts(title="Kapitel", intro="Mira geht", say={"intro": "Miera geht"})
    assert speech.chapter_fields(chapter) == {"title": "Kapitel", "intro": "Miera geht"}


def test_a_read_tasks_reading_text_is_not_spoken() -> None:
    t = TaskTexts(title="Lies", text="Der Text", say={"text": "Der gesprochene Text"})
    assert speech.task_fields("read", t, "normal") == {"title": "Lies"}
    assert speech.task_editor_fields("read", t) == {"title": "Lies"}


def test_what_a_pack_speaks() -> None:
    content = Pack.model_validate(pack(chapters=1)).content()
    texts = speech.spoken_texts(content)
    english = [text for lang, text in texts if lang == "en"]
    # World name; chapter title; task titles and all level texts; calc: the problems of every
    # level with their hints (§15.2); read: the title and the questions of every level with
    # their answers. Shared texts once.
    assert english == [
        "World",
        "One",
        "Do it",
        "easier",
        "harder",
        "T",
        "sum?",
        "count",
        "left?",
        "easy sum?",
        "hard sum?",
        "R",
        "normal?",
        "a",
        "b",
        "c",
        "easier?",
        "harder?",
    ]
    german = [text for lang, text in texts if lang == "de"]
    assert german[:3] == ["Welt", "One", "i"]  # the intro too; the guide isn't spoken
    spoken = {text for _, text in texts}
    for reading in ("en normal", "en easy", "en hard", "en spoken", "mit Mira"):
        assert reading not in spoken


def test_jobs_only_for_languages_with_a_voice(settings: Settings) -> None:
    content = Pack.model_validate(pack(chapters=1)).content()
    voices = speech.chosen_voices(settings)
    voices["el"] = None
    jobs = speech.world_jobs(content, voices)
    assert jobs
    assert {job.voice.lang for job in jobs} == {"de", "en"}
    assert all(job.key == speech_key(job.voice.id, job.text) for job in jobs)


# ---- voices -------------------------------------------------------------------------------


def test_the_manifest_records_licence_and_attribution() -> None:
    infos = {i.id: i for i in speech.manifest(Settings(data_dir=Path("/nonexistent")))}
    for voice_id in (*BUNDLED.values(), JOY):
        assert infos[voice_id].license, voice_id
        assert infos[voice_id].attribution, voice_id
    assert infos[JOY].attribution == "JOY Greek voice — Gemma4GR project, CC BY-NC 4.0"
    assert not infos[JOY].bundled  # known, used when its files are there (§14.3)
    raw = json.loads(speech.MANIFEST.read_text())
    for entry in raw["voices"]:
        if entry["bundled"]:
            model = next(f for f in entry["files"] if f["name"].endswith(".onnx"))
            assert len(model["sha256"]) == 64
            assert model["name"] == f"{entry['id']}.onnx"
            assert model.get("vendored") or entry["id"] in model["url"]


def test_bundled_voices_by_default(settings: Settings) -> None:
    voices = speech.chosen_voices(settings)
    assert {lang: v.id for lang, v in voices.items() if v} == BUNDLED
    assert all(v is not None and v.source == "bundled" for v in voices.values())


def test_joy_in_data_wins_for_greek(settings: Settings) -> None:
    fake_voice(settings.voices_dir, JOY)
    el = speech.chosen_voices(settings)["el"]
    assert el is not None
    assert (el.id, el.source) == (JOY, "data")
    assert el.attribution == "JOY Greek voice — Gemma4GR project, CC BY-NC 4.0"
    # VOICE_EL chooses by name.
    chosen = speech.chosen_voices(settings.model_copy(update={"voice_el": BUNDLED["el"]}))["el"]
    assert chosen is not None and chosen.id == BUNDLED["el"]


def test_joy_added_to_the_image_wins_for_greek(settings: Settings) -> None:
    """The family image copies JOY into /app/voices/ (§14.3): known from voices.json, so it
    comes before the bundled Greek voice without any setting."""
    assert settings.bundled_voices_dir is not None
    fake_voice(settings.bundled_voices_dir, JOY)
    el = speech.chosen_voices(settings)["el"]
    assert el is not None
    assert (el.id, el.source) == (JOY, "bundled")
    assert el.license == "CC BY-NC 4.0"


def test_a_voice_in_data_wins_over_the_bundled_one(settings: Settings) -> None:
    fake_voice(settings.voices_dir, BUNDLED["de"])
    de = speech.chosen_voices(settings)["de"]
    assert de is not None
    assert de.source == "data"
    assert de.model == settings.voices_dir / f"{BUNDLED['de']}.onnx"


def test_a_missing_or_odd_voice_name_falls_back(settings: Settings) -> None:
    for name in ("de_DE-nobody-medium", "../de_DE-thorsten-medium", "a/b"):
        de = speech.chosen_voices(settings.model_copy(update={"voice_de": name}))["de"]
        assert de is not None and de.id == BUNDLED["de"], name
    assert speech.find_model(settings, "../bundled-voices/de_DE-thorsten-medium") is None


def test_a_model_without_its_json_is_not_a_voice(settings: Settings) -> None:
    assert settings.bundled_voices_dir is not None
    (settings.bundled_voices_dir / f"{BUNDLED['en']}.onnx.json").unlink()
    assert speech.chosen_voices(settings)["en"] is None


# ---- worker -------------------------------------------------------------------------------


def test_the_worker_renders_what_is_missing(settings: Settings) -> None:
    content = Pack.model_validate(pack(chapters=1)).content()
    jobs = speech.world_jobs(content, speech.chosen_voices(settings))
    existing = render(settings, "en", "World")
    worker = speech.SpeechWorker(settings, render=fake_render, enabled=True)
    assert worker.enqueue(jobs) == len(jobs) - 1  # "World" (en) is there already
    assert existing not in worker.queued()
    assert worker.enqueue(jobs) == 0  # already queued
    worker.drain()
    assert worker.queued() == []
    names = {p.name for p in settings.speech_dir.iterdir()}
    assert names == {f"{job.key}.m4a" for job in jobs}  # no temporary files left
    job = next(j for j in jobs if j.text == "Do it")
    assert (settings.speech_dir / f"{job.key}.m4a").read_bytes() == b"m4a en_GB-cori-medium Do it"


def test_failures_are_kept_and_not_retried_until_restart(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    content = Pack.model_validate(pack(chapters=1)).content()
    jobs = speech.world_jobs(content, speech.chosen_voices(settings))[:2]

    def broken(voice: Voice, text: str, out: Path) -> None:
        out.write_bytes(b"half")
        raise RuntimeError("piper fell over")

    worker = speech.SpeechWorker(settings, render=broken, enabled=True)
    worker.enqueue(jobs)
    worker.drain()
    assert set(worker.failed()) == {j.key for j in jobs}
    assert "piper fell over" in caplog.text
    assert not any(settings.speech_dir.iterdir())  # the half file is gone
    assert worker.enqueue(jobs) == 0
    # A new worker (the next start) tries again.
    again = speech.SpeechWorker(settings, render=fake_render, enabled=True)
    assert again.enqueue(jobs) == 2


def test_the_worker_thread(settings: Settings) -> None:
    content = Pack.model_validate(pack(chapters=1)).content()
    jobs = speech.world_jobs(content, speech.chosen_voices(settings))[:3]
    worker = speech.SpeechWorker(settings, render=fake_render, enabled=True)
    worker.start()
    assert worker.running
    worker.enqueue(jobs)
    worker.join()
    assert all(speech.speech_file(settings, j.key).is_file() for j in jobs)
    worker.stop()
    assert not worker.running


def test_piper_then_ffmpeg_to_mono_aac(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Piper runs as its own process (`python -m piper`, niced, one thread), then ffmpeg."""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(args: list[str], **kw: Any) -> None:
        calls.append((args, kw))
        if "piper" in args:
            wav = Path(args[args.index("--output-file") + 1])
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(b"\0\0" * 100)
        else:
            wav = Path(args[args.index("-i") + 1])
            assert wav.stat().st_size > 44  # Piper wrote the WAV before ffmpeg runs
            Path(args[-1]).write_bytes(b"m4a")

    monkeypatch.setattr("kidsmedia.speech.subprocess.run", fake_run)
    voice = speech.chosen_voices(settings)["de"]
    assert voice is not None
    out = tmp_path / "out.m4a"
    speech.PiperRenderer()(voice, "Hallo\n  Welt", out)
    assert out.read_bytes() == b"m4a"
    (piper, piper_kw), (ffmpeg, _) = calls
    assert piper[:6] == ["nice", "-n", "10", sys.executable, "-m", "piper"]
    assert piper[piper.index("--model") + 1] == str(voice.model)
    assert piper[-2:] == ["--", "Hallo Welt"]  # one line, after "--": never read as a flag
    assert piper_kw["env"]["OMP_NUM_THREADS"] == "1"
    assert piper_kw["check"] is True
    assert ffmpeg[0] == "ffmpeg"
    for flag, value in (("-ac", "1"), ("-c:a", "aac"), ("-b:a", "64k"), ("-f", "mp4")):
        assert ffmpeg[ffmpeg.index(flag) + 1] == value
    # The WAV's temporary folder is gone (bundled-voices is the fixture's).
    assert [p for p in tmp_path.iterdir() if p.name != "bundled-voices"] == [out]


def test_a_failing_piper_is_reported(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(args: list[str], **_: Any) -> None:
        raise subprocess.CalledProcessError(2, args, stderr=b"no such model")

    monkeypatch.setattr("kidsmedia.speech.subprocess.run", fail)
    content = Pack.model_validate(pack(chapters=1)).content()
    job = speech.world_jobs(content, speech.chosen_voices(settings))[0]
    worker = speech.SpeechWorker(settings, enabled=True)
    assert worker.run_one(job) is False
    assert worker.failed() == {job.key: "piper exit 2: no such model"}
    assert list(settings.speech_dir.iterdir()) == []  # no temporary folder left


def test_a_turned_off_worker_queues_nothing(app: FastAPI, admin: TestClient) -> None:
    worker: speech.SpeechWorker = app.state.speech
    assert worker.enabled is False  # SPEECH_WORKER=false in the test settings
    import_json(admin, pack())
    assert worker.queued() == []
    body = admin.get("/api/admin/speech").json()
    assert (body["rendered"], body["pending"], body["failed"], body["worker"]) == (0, 0, 0, False)


def test_start_up_deletes_unused_and_temporary_files(
    admin: TestClient, app: FastAPI, settings: Settings
) -> None:
    worker = worker_of(app)
    world_id = import_json(admin, pack()).json()["world_id"]
    worker.drain()
    folder = settings.speech_dir
    used = {p.name for p in folder.iterdir()}
    (folder / "0123456789abcdef0123.m4a").write_bytes(b"old")  # a text no world speaks
    (folder / ".0123456789abcdef0123.tmp").write_bytes(b"half")
    (folder / ".render-x1y2").mkdir()
    (folder / ".render-x1y2" / "speech.wav").write_bytes(b"wav")
    (folder / "tmpabc").mkdir()  # an older renderer's temporary folder
    with make_engine(settings.db_path).connect() as conn:
        assert worker.prune(conn) == 4
    assert {p.name for p in folder.iterdir()} == used

    # A world that can't be loaded: temporary files go, speech files all stay.
    (folder / "0123456789abcdef0123.m4a").write_bytes(b"old")
    (folder / ".render-z").mkdir()
    engine = make_engine(settings.db_path)
    with engine.begin() as conn:
        conn.execute(update(worlds).where(worlds.c.id == world_id).values(content={"x": 1}))
    with engine.connect() as conn:
        assert worker.prune(conn) == 1
    assert {p.name for p in folder.iterdir()} == used | {"0123456789abcdef0123.m4a"}


def test_queued_worlds_come_first(admin: TestClient, settings: Settings) -> None:
    first = import_json(admin, pack("first-world")).json()["world_id"]
    second = import_json(admin, pack("second-world")).json()["world_id"]
    body = pack("second-world")
    body["texts"]["en"]["name"] = "Second"
    admin.put(f"/api/admin/worlds/{second}", json=body)
    admin.put("/api/admin/activities/queue", json={"world_ids": [second]})
    assert first < second
    with make_engine(settings.db_path).connect() as conn:
        jobs = speech.all_jobs(conn, settings)
    assert [j.text for j in jobs[:3]] == ["Welt", "Second", "Κόσμος"]
    assert len({j.key for j in jobs}) == len(jobs)


# ---- device -------------------------------------------------------------------------------


def test_audio_only_for_rendered_fields(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    queued_world(admin)
    name = render(settings, "en", "World")
    title = render(settings, "en", "One")
    question = render(settings, "en", "normal?")
    answer = render(settings, "en", "b")
    render(settings, "en", "en normal")  # a reading text never gets audio, even if a file exists
    render(settings, "en", "en spoken")
    world = admin.get("/api/activities", headers=bearer(device_token)).json()["world"]

    def url(key: str) -> str:
        return f"/api/speech/{key}.m4a"

    assert world["texts"]["en"]["audio"] == {"name": url(name)}
    assert world["texts"]["de"]["audio"] == {}
    chapter = world["chapters"][0]
    assert chapter["texts"]["en"]["audio"] == {"title": url(title)}
    tasks = {t["kind"]: t["texts"] for t in chapter["tasks"]}
    assert tasks["draw"]["en"]["audio"] == {"title": url(title)}  # same text, same file
    assert tasks["calc"]["en"]["audio"] == {}
    read = tasks["read"]["en"]
    assert read["audio"] == {}
    assert read["questions"][0]["audio"] == {
        "question": url(question),
        "answers": [None, url(answer), None],
    }
    assert tasks["read"]["de"]["questions"][0]["audio"] is None


def test_problems_carry_their_audio(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    """§15.2: each problem has `audio: {text, hint}`, None for what isn't rendered."""
    queued_world(admin)
    text = render(settings, "en", "sum?")
    hint = render(settings, "en", "count")
    render(settings, "de", "count")
    world = admin.get("/api/activities", headers=bearer(device_token)).json()["world"]
    calc = {t["kind"]: t["texts"] for t in world["chapters"][0]["tasks"]}["calc"]
    assert [p["audio"] for p in calc["en"]["problems"]] == [
        {"text": f"/api/speech/{text}.m4a", "hint": f"/api/speech/{hint}.m4a"},
        {"text": None, "hint": f"/api/speech/{hint}.m4a"},  # "left?" isn't rendered
    ]
    assert calc["de"]["problems"][0]["audio"]["text"] is None
    assert calc["de"]["problems"][0]["audio"]["hint"].startswith("/api/speech/")
    assert calc["el"]["problems"][0]["audio"] == {"text": None, "hint": None}
    assert calc["en"]["problems"][0]["answer"] == 5


def test_the_tablet_gets_the_audio_of_its_level(
    admin: TestClient, device_token: str, settings: Settings
) -> None:
    queued_world(admin)
    easier = render(settings, "en", "easier")
    render(settings, "en", "Do it")
    admin.put(
        "/api/admin/activities/settings",
        json={"days": [0, 1, 2, 3, 4, 5], "unlock_time": "07:00", "level": "easier"},
    )
    world = admin.get("/api/activities", headers=bearer(device_token)).json()["world"]
    draw = world["chapters"][0]["tasks"][0]["texts"]["en"]
    assert draw["text"] == "easier"
    assert draw["audio"] == {"text": f"/api/speech/{easier}.m4a"}


def test_speech_files_need_a_device(app: FastAPI, admin: TestClient, settings: Settings) -> None:
    key = render(settings, "en", "World")
    assert TestClient(app).get(f"/api/speech/{key}.m4a").status_code == 401
    # The admin's session isn't enough for the device endpoint.
    assert admin.get(f"/api/speech/{key}.m4a").status_code == 401


def test_speech_file_served(admin: TestClient, device_token: str, settings: Settings) -> None:
    key = render(settings, "en", "World")
    r = admin.get(f"/api/speech/{key}.m4a", headers=bearer(device_token))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/mp4"
    assert r.content == b"m4a"
    settings.accel_redirect = True
    r = admin.get(f"/api/speech/{key}.m4a", headers=bearer(device_token))
    assert r.status_code == 200
    assert r.headers["x-accel-redirect"] == f"/_media/_speech/{key}.m4a"
    assert r.content == b""
    assert "immutable" in r.headers["cache-control"]


@pytest.mark.parametrize(
    "key",
    [
        "0123456789abcdef012",  # 19
        "0123456789abcdef01234",  # 21
        "0123456789ABCDEF0123",  # upper case
        "0123456789abcdef012g",
        "..0123456789abcdef01",
    ],
)
def test_speech_keys_are_checked(
    admin: TestClient, device_token: str, settings: Settings, key: str
) -> None:
    # A file under a malformed name is still refused.
    settings.speech_dir.mkdir(parents=True, exist_ok=True)
    (settings.speech_dir / f"{key}.m4a").write_bytes(b"x")
    render(settings, "en", "World")
    for path, headers in (
        (f"/api/speech/{key}.m4a", bearer(device_token)),
        (f"/api/admin/speech/{key}.m4a", {}),
    ):
        r = admin.get(path, headers=headers)
        assert r.status_code == 404, (path, r.text)
        assert r.json()["reason"] == "speech_not_found"


def test_a_speech_file_that_isnt_rendered(admin: TestClient, device_token: str) -> None:
    r = admin.get("/api/speech/0123456789abcdef0123.m4a", headers=bearer(device_token))
    assert r.status_code == 404
    assert r.json()["reason"] == "speech_not_found"


# ---- admin --------------------------------------------------------------------------------


def test_admin_speech_file(app: FastAPI, admin: TestClient, settings: Settings) -> None:
    key = render(settings, "de", "Welt")
    r = admin.get(f"/api/admin/speech/{key}.m4a")
    assert r.status_code == 200
    assert r.content == b"m4a"
    assert TestClient(app).get(f"/api/admin/speech/{key}.m4a").status_code == 401


def test_admin_speech_status(app: FastAPI, admin: TestClient, settings: Settings) -> None:
    fake_voice(settings.voices_dir, JOY)
    worker = worker_of(app)
    body = admin.get("/api/admin/speech").json()
    assert body["voices"] == [
        {
            "lang": "de",
            "voice": BUNDLED["de"],
            "source": "bundled",
            "license": "MIT (model); CC0 (dataset)",
            "attribution": body["voices"][0]["attribution"],
        },
        {
            "lang": "en",
            "voice": BUNDLED["en"],
            "source": "bundled",
            "license": "MIT (model); public domain (dataset)",
            "attribution": body["voices"][1]["attribution"],
        },
        {
            "lang": "el",
            "voice": JOY,
            "source": "data",
            "license": "CC BY-NC 4.0",
            "attribution": "JOY Greek voice — Gemma4GR project, CC BY-NC 4.0",
        },
    ]
    assert "Thorsten" in body["voices"][0]["attribution"]
    assert (body["rendered"], body["pending"], body["failed"]) == (0, 0, 0)
    assert body["worker"] is False

    import_json(admin, pack())
    total = len(all_keys(pack(), BUNDLED | {"el": JOY}))
    body = admin.get("/api/admin/speech").json()
    assert (body["rendered"], body["pending"], body["failed"]) == (0, total, 0)

    worker.drain()
    body = admin.get("/api/admin/speech").json()
    assert (body["rendered"], body["pending"], body["failed"]) == (total, 0, 0)

    def broken(voice: Voice, text: str, out: Path) -> None:
        raise RuntimeError("no")

    worker.render = broken
    one = next(settings.speech_dir.glob("*.m4a"))
    one.unlink()
    worker.enqueue_world(Pack.model_validate(pack()).content())
    worker.drain()
    body = admin.get("/api/admin/speech").json()
    assert (body["rendered"], body["pending"], body["failed"]) == (total - 1, 0, 1)


def test_admin_speech_status_needs_a_session(app: FastAPI) -> None:
    assert TestClient(app).get("/api/admin/speech").status_code == 401


def test_import_example_and_save_queue_their_texts(app: FastAPI, admin: TestClient) -> None:
    worker = worker_of(app)
    r = import_json(admin, pack(), dry_run=True)
    assert r.status_code == 200
    assert worker.queued() == []  # a dry run queues nothing
    world_id = import_json(admin, pack()).json()["world_id"]
    assert set(worker.queued()) == all_keys(pack())
    worker.drain()
    edited = pack(title="Edited")
    assert admin.put(f"/api/admin/worlds/{world_id}", json=edited).status_code == 200
    # Only the changed texts are new.
    assert set(worker.queued()) == all_keys(edited) - all_keys(pack())
    assert speech_key(BUNDLED["en"], "Edited") in worker.queued()
    worker.drain()
    slug = admin.get("/api/admin/activities").json()["examples"][0]["slug"]
    assert admin.post(f"/api/admin/worlds/examples/{slug}").status_code == 200
    assert worker.queued()


def test_the_editor_gets_audio_and_may_send_it_back(app: FastAPI, admin: TestClient) -> None:
    worker = worker_of(app)
    world_id = import_json(admin, pack()).json()["world_id"]
    worker.drain()
    full = admin.get(f"/api/admin/worlds/{world_id}").json()
    assert full["texts"]["en"]["audio"]["name"].startswith("/api/admin/speech/")
    chapter = full["chapters"][0]
    assert set(chapter["texts"]["de"]["audio"]) == {"title", "intro"}
    draw = chapter["tasks"][0]["texts"]["en"]
    assert set(draw["audio"]) == {"title", "text", "easier", "harder"}
    assert draw["question_audio"] == {}
    assert draw["problem_audio"] == {}
    read = chapter["tasks"][3]["texts"]["en"]
    assert set(read["audio"]) == {"title"}  # never the reading text
    assert set(read["question_audio"]) == {"normal", "easier", "harder"}
    assert all(read["question_audio"]["normal"][0]["answers"])
    assert read["problem_audio"] == {}
    calc = chapter["tasks"][1]["texts"]["en"]
    assert calc["question_audio"] == {}
    assert set(calc["problem_audio"]) == {"normal", "easier", "harder"}
    assert len(calc["problem_audio"]["normal"]) == 2  # aligned with `problems`
    for audio in calc["problem_audio"]["normal"]:
        assert audio["text"].startswith("/api/admin/speech/")
        assert audio["hint"].startswith("/api/admin/speech/")
    r = admin.get(full["texts"]["en"]["audio"]["name"])
    assert r.status_code == 200

    # The editor may send the blocks back with their output-only audio maps.
    body = pack(title="Edited")
    body["texts"]["en"]["audio"] = full["texts"]["en"]["audio"]
    body["chapters"][0]["texts"]["de"]["audio"] = chapter["texts"]["de"]["audio"]
    body["chapters"][0]["tasks"][3]["texts"]["en"] |= {
        "audio": read["audio"],
        "question_audio": read["question_audio"],
    }
    body["chapters"][0]["tasks"][1]["texts"]["en"] |= {
        "audio": calc["audio"],
        "problem_audio": calc["problem_audio"],
    }
    r = admin.put(f"/api/admin/worlds/{world_id}", json=body)
    assert r.status_code == 200, r.text
    r = admin.get(f"/api/admin/worlds/{world_id}/export")
    assert r.status_code == 200
