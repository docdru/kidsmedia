"""Thumbnail pipeline (SPEC §6.4) against real ffmpeg-generated files."""

from pathlib import Path

import pytest
from PIL import Image

from kidsmedia import thumbs
from tests import media_fixtures as fx


@pytest.fixture(scope="module")
def files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("media")
    cover = fx.cover_png(d / "cover.png")
    return {
        "mp3_plain": fx.mp3(d / "plain.mp3", title="Episode 1: Setting Sail"),
        "mp3_cover": fx.mp3(d / "cover.mp3", cover=cover),
        "mp3_corrupt": fx.corrupt_embedded_picture(fx.mp3(d / "corrupt.mp3", cover=cover)),
        "m4a_cover": fx.m4a_with_cover(d / "cover.m4a", cover),
        "mp4_black": fx.mp4_black_intro(d / "black.mp4"),
        "mp4_moov_end": fx.mp4_black_intro(d / "moovend.mp4", faststart=False),
        "mp4_cover": fx.mp4_with_cover(d / "cover.mp4", cover),
        "mp4_mpeg4": fx.mp4_unsupported_codec(d / "mpeg4.mp4"),
    }


def test_classify_accepts_supported_formats(files: dict[str, Path]) -> None:
    assert thumbs.classify(thumbs.probe(files["mp3_plain"])) == thumbs.Kind(
        False, "audio/mpeg", "mp3"
    )
    assert thumbs.classify(thumbs.probe(files["m4a_cover"])) == thumbs.Kind(
        False, "audio/mp4", "m4a"
    )
    assert thumbs.classify(thumbs.probe(files["mp4_black"])) == thumbs.Kind(
        True, "video/mp4", "mp4"
    )
    # an mp4 whose only extra video stream is cover art is still a video
    assert thumbs.classify(thumbs.probe(files["mp4_cover"])).is_video


def test_classify_rejects_other_codecs_with_a_reason(files: dict[str, Path]) -> None:
    with pytest.raises(thumbs.MediaError) as err:
        thumbs.classify(thumbs.probe(files["mp4_mpeg4"]))
    assert err.value.reason == "unsupported_format"
    assert err.value.params["video_codec"] == "mpeg4"


def test_probe_reads_title_and_duration(files: dict[str, Path]) -> None:
    p = thumbs.probe(files["mp3_plain"])
    assert p.title == "Episode 1: Setting Sail"
    assert 4.5 < p.duration_s < 5.5


def test_unreadable_file(tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not media at all" * 100)
    with pytest.raises(thumbs.MediaError) as err:
        thumbs.classify(thumbs.probe(junk))
    assert err.value.reason in {"unreadable_file", "unsupported_format"}


def test_faststart_detection_and_fix(files: dict[str, Path], tmp_path: Path) -> None:
    assert thumbs.is_faststart(files["mp4_black"])
    copy = tmp_path / "copy.mp4"
    copy.write_bytes(files["mp4_moov_end"].read_bytes())
    assert not thumbs.is_faststart(copy)
    thumbs.make_faststart(copy)
    assert thumbs.is_faststart(copy)
    assert thumbs.probe(copy).duration_s > 19


@pytest.mark.parametrize("name", ["mp3_cover", "m4a_cover"])
def test_embedded_art_wins_for_audio(files: dict[str, Path], name: str) -> None:
    p = thumbs.probe(files[name])
    source, img, frame_ms = thumbs.automatic_thumbnail(files[name], p, is_video=False)
    assert source == "embedded" and img is not None and frame_ms is None
    assert img.size == (600, 600)


def test_embedded_cover_also_wins_for_video(files: dict[str, Path]) -> None:
    p = thumbs.probe(files["mp4_cover"])
    source, img, _ = thumbs.automatic_thumbnail(files["mp4_cover"], p, is_video=True)
    assert source == "embedded" and img is not None and img.size == (600, 600)


def test_audio_without_art_has_no_thumbnail(files: dict[str, Path]) -> None:
    p = thumbs.probe(files["mp3_plain"])
    assert thumbs.automatic_thumbnail(files["mp3_plain"], p, is_video=False) == ("none", None, None)


def test_corrupt_embedded_picture_falls_back_without_failing(files: dict[str, Path]) -> None:
    p = thumbs.probe(files["mp3_corrupt"])
    assert thumbs.classify(p).ext == "mp3"  # the upload itself stays valid
    source, img, _ = thumbs.automatic_thumbnail(files["mp3_corrupt"], p, is_video=False)
    assert source == "none" and img is None


def test_video_frame_skips_black_intro(files: dict[str, Path]) -> None:
    p = thumbs.probe(files["mp4_black"])
    source, img, frame_ms = thumbs.automatic_thumbnail(files["mp4_black"], p, is_video=True)
    assert source == "frame" and img is not None and frame_ms is not None
    assert not thumbs.is_dull(img)
    assert img.width / img.height == pytest.approx(4 / 3, rel=0.02)


def test_is_dull() -> None:
    assert thumbs.is_dull(Image.new("RGB", (64, 64), "black"))
    assert thumbs.is_dull(Image.new("RGB", (64, 64), "#4552C7"))  # single colour
    striped = Image.new("RGB", (64, 64), "white")
    striped.paste(Image.new("RGB", (32, 64), "black"), (0, 0))
    assert not thumbs.is_dull(striped)


def test_write_thumb_sizes_and_key(tmp_path: Path) -> None:
    img = Image.new("RGB", (2000, 1000), "#0F7A70")
    key, aspect = thumbs.write_thumb(img, tmp_path, "m7")
    assert key.startswith("m7-") and len(key) == len("m7-") + 6
    assert aspect == 2.0
    for size, edge in thumbs.SIZES.items():
        with Image.open(tmp_path / f"{key}-{size}.webp") as out:
            assert out.format == "WEBP"
            assert max(out.size) == edge
            assert not out.info.get("exif")
    # same content -> same key (content hash); delete removes all sizes
    key2, _ = thumbs.write_thumb(img, tmp_path, "m7")
    assert key2 == key
    thumbs.delete_thumb_files(tmp_path, key)
    assert not list(tmp_path.glob(f"{key}-*"))


def test_small_images_are_not_upscaled(tmp_path: Path) -> None:
    key, _ = thumbs.write_thumb(Image.new("RGB", (100, 100), "red"), tmp_path, "g1")
    with Image.open(tmp_path / f"{key}-lg.webp") as out:
        assert out.size == (100, 100)


def test_open_image_limits() -> None:
    with pytest.raises(thumbs.MediaError) as err:
        thumbs.open_image(b"\x89PNG not really")
    assert err.value.reason == "bad_image"
    with pytest.raises(thumbs.MediaError) as err:
        thumbs.open_image(b"0" * (thumbs.MAX_IMAGE_UPLOAD_BYTES + 1))
    assert err.value.reason == "image_too_large"


def test_decompression_bomb_is_rejected(tmp_path: Path) -> None:
    big = Image.new("1", (10_000, 6_000))  # 60 MP, tiny file
    path = tmp_path / "bomb.png"
    big.save(path)
    with pytest.raises(thumbs.MediaError) as err:
        thumbs.open_image(path.read_bytes())
    assert err.value.reason == "bad_image"


def test_a_cover_with_an_empty_mime_type_is_read_from_the_tags(tmp_path: Path) -> None:
    """fre:ac-style APIC frame (no MIME type): ffmpeg skips it, the tag fallback finds it."""
    f = fx.mp3_with_untyped_cover(tmp_path / "untyped.mp3")
    p = thumbs.probe(f)
    assert p.attached_pic_index is None  # ffmpeg really doesn't see it
    assert thumbs.has_embedded_picture(f, p)
    source, img, _ = thumbs.automatic_thumbnail(f, p, is_video=False)
    assert source == "embedded" and img is not None
    assert img.size == (300, 300)
    px = img.getpixel((150, 150))
    assert isinstance(px, tuple)
    r, g, b = px[:3]
    assert r > 150 and g < 120 and b < 120  # the red test cover, decoded by its bytes


def test_files_without_any_picture_still_have_none(files: dict[str, Path]) -> None:
    p = thumbs.probe(files["mp3_plain"])
    assert thumbs.tag_pictures(files["mp3_plain"]) == []
    assert not thumbs.has_embedded_picture(files["mp3_plain"], p)
