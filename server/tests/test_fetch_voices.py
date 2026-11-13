"""The voice download script of the image build (SPEC §14.3): what it insists on."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_voices.py"
MANIFEST = Path(__file__).resolve().parents[1] / "src" / "kidsmedia" / "voices.json"
PINNED = "https://huggingface.co/rhasspy/piper-voices/resolve/" + "a" * 40 + "/x/"


def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_voices", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_model_needs_its_sha256() -> None:
    with pytest.raises(SystemExit, match="needs a sha256"):
        script().check_pinned("v.onnx", PINNED + "v.onnx", None)
    script().check_pinned("v.onnx", PINNED + "v.onnx", "0" * 64)


def test_a_config_is_pinned_by_the_commit() -> None:
    script().check_pinned("v.onnx.json", PINNED + "v.onnx.json", None)
    with pytest.raises(SystemExit, match="pinned"):
        script().check_pinned("v.onnx.json", "https://example.org/main/v.onnx.json", None)


def test_the_bundled_voices_pass() -> None:
    check = script().check_pinned
    for voice in json.loads(MANIFEST.read_text(encoding="utf-8"))["voices"]:
        if voice.get("bundled"):
            for file in voice["files"]:
                if not file.get("vendored"):
                    check(file["name"], file["url"], file.get("sha256"))


def test_no_voice_is_vendored() -> None:
    """The public image ships no voice files from the repository; JOY isn't bundled (§14.3)."""
    for voice in json.loads(MANIFEST.read_text(encoding="utf-8"))["voices"]:
        if voice.get("bundled"):
            assert not any(f.get("vendored") for f in voice["files"]), voice["id"]
        if voice["id"] == "el_GR-joy-medium":
            assert voice["bundled"] is False


def test_a_vendored_file_must_match_and_exist(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "v.onnx").write_bytes(b"model")
    copy = script().copy_vendored
    with pytest.raises(SystemExit, match="sha256"):
        copy(source, tmp_path / "v.onnx", "0" * 64)
    assert not (tmp_path / "v.onnx").exists()
    with pytest.raises(SystemExit, match="needs a sha256"):
        copy(source, tmp_path / "v.onnx", None)
    with pytest.raises(SystemExit, match="missing"):
        copy(source, tmp_path / "w.onnx", "0" * 64)
