"""Media probing and thumbnails (SPEC §6.3, §6.4). No database access here."""

import base64
import hashlib
import io
import json
import logging
import shutil
import subprocess
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import mutagen
from mutagen.flac import Picture
from PIL import Image, ImageOps, ImageStat

Image.MAX_IMAGE_PIXELS = 50_000_000  # SPEC §6.4: decompression-bomb limit

SIZES: dict[str, int] = {"sm": 160, "md": 480, "lg": 1024}
WEBP_QUALITY = 82
MAX_IMAGE_UPLOAD_BYTES = 20 * 1024 * 1024

AUDIO_CODECS = {"mp3", "aac", "opus", "vorbis"}
VIDEO_CODECS = {"h264"}
VIDEO_AUDIO_CODECS = {"aac", "mp3"}


class MediaError(Exception):
    """A file that can't be used. `reason` is an API reason code (SPEC §3.2 style)."""

    def __init__(self, reason: str, **params: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.params = params


@dataclass
class Probe:
    format_name: str
    duration_s: float
    audio_codec: str | None = None
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    attached_pic_index: int | None = None
    title: str | None = None
    streams: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Kind:
    is_video: bool
    mime: str
    ext: str


def probe(path: Path) -> Probe:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
        data = json.loads(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise MediaError("unreadable_file") from exc
    fmt = data.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    p = Probe(
        format_name=fmt.get("format_name", ""),
        duration_s=float(fmt.get("duration") or 0.0),
        title=(tags.get("title") or "").strip() or None,
        streams=data.get("streams", []),
    )
    for s in p.streams:
        kind = s.get("codec_type")
        if kind == "video" and (s.get("disposition") or {}).get("attached_pic") == 1:
            if p.attached_pic_index is None:
                p.attached_pic_index = int(s["index"])
        elif kind == "video" and p.video_codec is None:
            p.video_codec = s.get("codec_name")
            p.width, p.height = s.get("width"), s.get("height")
        elif kind == "audio" and p.audio_codec is None:
            p.audio_codec = s.get("codec_name")
    return p


def classify(p: Probe) -> Kind:
    """Accept only what Android plays without transcoding (SPEC §6.3)."""
    fmt = set(p.format_name.split(","))
    if p.duration_s <= 0:
        raise MediaError("unreadable_file")
    if p.video_codec is not None:
        if not fmt & {"mp4", "mov", "m4a"}:
            raise MediaError("unsupported_format", container=p.format_name)
        if p.video_codec not in VIDEO_CODECS:
            raise MediaError("unsupported_format", video_codec=p.video_codec, convert=CONVERT_VIDEO)
        if p.audio_codec is not None and p.audio_codec not in VIDEO_AUDIO_CODECS:
            raise MediaError("unsupported_format", audio_codec=p.audio_codec, convert=CONVERT_VIDEO)
        return Kind(True, "video/mp4", "mp4")
    if p.audio_codec is None:
        raise MediaError("unsupported_format", container=p.format_name)
    if p.audio_codec not in AUDIO_CODECS:
        raise MediaError("unsupported_format", audio_codec=p.audio_codec, convert=CONVERT_AUDIO)
    if "mp3" in fmt and p.audio_codec == "mp3":
        return Kind(False, "audio/mpeg", "mp3")
    if fmt & {"mp4", "mov", "m4a"} and p.audio_codec == "aac":
        return Kind(False, "audio/mp4", "m4a")
    if "ogg" in fmt and p.audio_codec in {"opus", "vorbis"}:
        return Kind(False, "audio/ogg", "ogg")
    raise MediaError("unsupported_format", container=p.format_name, audio_codec=p.audio_codec)


CONVERT_VIDEO = "ffmpeg -i IN -c:v libx264 -c:a aac -movflags +faststart OUT.mp4"
CONVERT_AUDIO = "ffmpeg -i IN -c:a libmp3lame -q:a 2 OUT.mp3"


def is_faststart(path: Path) -> bool:
    """True when the mp4 'moov' atom comes before 'mdat' (progressive streaming)."""
    with path.open("rb") as fh:
        while True:
            header = fh.read(8)
            if len(header) < 8:
                return False
            size = int.from_bytes(header[:4], "big")
            kind = header[4:8]
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            if size == 1:
                size = int.from_bytes(fh.read(8), "big")
                fh.seek(size - 16, io.SEEK_CUR)
            elif size == 0:
                return False
            else:
                fh.seek(size - 8, io.SEEK_CUR)


def make_faststart(path: Path) -> None:
    """Move the moov atom to the front without re-encoding (SPEC §6.3 'optimize')."""
    tmp = path.with_suffix(".faststart" + path.suffix)
    _ffmpeg(["-i", str(path), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(tmp)])
    tmp.replace(path)


def _ffmpeg(args: list[str], timeout: int = 120) -> bytes:
    try:
        return subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-y", *args],
            capture_output=True,
            check=True,
            timeout=timeout,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise MediaError("ffmpeg_failed") from exc


def open_image(data: bytes) -> Image.Image:
    """Decode untrusted image bytes with size limits, EXIF rotation, RGB."""
    if len(data) > MAX_IMAGE_UPLOAD_BYTES:
        raise MediaError("image_too_large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img: Image.Image = Image.open(io.BytesIO(data))
            img.load()
    except (
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise MediaError("bad_image") from exc
    img = ImageOps.exif_transpose(img) or img
    return img.convert("RGB")


FRONT_COVER = 3  # ID3 / FLAC picture type


def tag_pictures(path: Path) -> list[bytes]:
    """Pictures read straight from the file's tags with mutagen, front cover first.

    The fallback for covers ffmpeg skips: ffmpeg drops an ID3 APIC frame whose MIME type
    is empty or unknown ("Unknown attached picture mimetype"), which some taggers write
    (e.g. fre:ac) and macOS shows anyway. Pillow then recognises the image by its bytes.
    Covers ID3 (mp3), MP4 "covr" (m4a/mp4) and METADATA_BLOCK_PICTURE (ogg/opus).
    """
    found: list[tuple[int, bytes]] = []  # (0 = front cover, 1 = other, data)
    try:
        f: Any = mutagen.File(path)
        tags: Any = getattr(f, "tags", None)
        if tags is None:
            return []
        if hasattr(tags, "getall"):  # ID3 (v2.2 PIC frames are upgraded to APIC)
            for frame in tags.getall("APIC"):
                found.append((0 if frame.type == FRONT_COVER else 1, bytes(frame.data)))
        else:  # MP4 and Vorbis comments are dict-like
            for cover in tags.get("covr") or []:
                found.append((0, bytes(cover)))
            for value in tags.get("metadata_block_picture") or []:
                pic: Any = cast(Any, Picture)(base64.b64decode(value))  # mutagen is untyped
                found.append((0 if pic.type == FRONT_COVER else 1, bytes(pic.data)))
    except Exception:  # any tag problem only means "no (more) pictures"; the upload goes on
        logging.getLogger(__name__).debug("tag pictures unreadable: %s", path, exc_info=True)
    return [data for _, data in sorted(found, key=lambda x: x[0]) if data]


def has_embedded_picture(path: Path, p: Probe) -> bool:
    """For the admin's "From file" option: a cover ffmpeg sees, or one in the tags."""
    return p.attached_pic_index is not None or bool(tag_pictures(path))


def embedded_picture(path: Path, p: Probe) -> Image.Image | None:
    """The file's own cover: ffmpeg's attached picture, else one read from the tags."""
    if p.attached_pic_index is not None:
        try:
            data = _ffmpeg(
                [
                    "-i",
                    str(path),
                    "-map",
                    f"0:{p.attached_pic_index}",
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ]
            )
            return open_image(data)
        except MediaError:
            pass  # try the tags; a corrupt picture falls back to a frame or none (§6.4)
    for data in tag_pictures(path):
        try:
            return open_image(data)
        except MediaError:
            continue
    return None


def frame_at(
    path: Path, t_s: float, pick_representative: bool = False, width: int = 1024
) -> Image.Image:
    vf = f"thumbnail=120,scale={width}:-2" if pick_representative else f"scale={width}:-2"
    data = _ffmpeg(
        [
            "-ss",
            f"{max(t_s, 0):.3f}",
            "-i",
            str(path),
            "-vf",
            vf,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
    )
    if not data:
        raise MediaError("ffmpeg_failed")
    return open_image(data)


def is_dull(img: Image.Image) -> bool:
    """Almost black or a single colour (SPEC §6.4: mean luma < 16 or very low variance)."""
    stat = ImageStat.Stat(img.convert("L"))
    return stat.mean[0] < 16 or stat.stddev[0] < 4


def representative_frame(path: Path, duration_s: float) -> tuple[Image.Image, int] | None:
    for fraction in (0.10, 0.25, 0.50):
        t = max(duration_s * fraction, min(3.0, duration_s / 2))
        try:
            img = frame_at(path, t, pick_representative=True)
        except MediaError:
            continue
        if not is_dull(img):
            return img, int(t * 1000)
    return None


Source = Literal["embedded", "frame", "none"]


def automatic_thumbnail(
    path: Path, p: Probe, is_video: bool
) -> tuple[Source, Image.Image | None, int | None]:
    """SPEC §6.4 order: embedded picture, then (video) a representative frame, else none."""
    img = embedded_picture(path, p)
    if img is not None:
        return "embedded", img, None
    if is_video:
        found = representative_frame(path, p.duration_s)
        if found is not None:
            return "frame", found[0], found[1]
    return "none", None, None


def write_thumb(img: Image.Image, thumbs_dir: Path, prefix: str) -> tuple[str, float]:
    """Write sm/md/lg WebP files. Returns (key, aspect). The key contains a content hash."""
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    blobs: dict[str, bytes] = {}
    for size, edge in SIZES.items():
        copy = img.copy()
        copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)  # never upscales
        buf = io.BytesIO()
        copy.save(buf, "WEBP", quality=WEBP_QUALITY, method=4)  # no exif/icc: metadata stripped
        blobs[size] = buf.getvalue()
    key = f"{prefix}-{hashlib.sha256(blobs['lg']).hexdigest()[:6]}"
    for size, blob in blobs.items():
        tmp = thumbs_dir / f".{key}-{size}.webp.tmp"
        tmp.write_bytes(blob)
        tmp.replace(thumbs_dir / f"{key}-{size}.webp")
    return key, round(img.width / img.height, 4)


def delete_thumb_files(thumbs_dir: Path, key: str) -> None:
    for size in SIZES:
        (thumbs_dir / f"{key}-{size}.webp").unlink(missing_ok=True)


def copy_thumb(thumbs_dir: Path, key: str, prefix: str) -> str:
    """Copy an existing thumbnail under a new owner prefix (same content hash)."""
    new_key = f"{prefix}-{key.rsplit('-', 1)[1]}"
    for size in SIZES:
        src = thumbs_dir / f"{key}-{size}.webp"
        if src.exists():
            shutil.copyfile(src, thumbs_dir / f"{new_key}-{size}.webp")
    return new_key


def jpeg_bytes(img: Image.Image, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def temp_dir(base: Path) -> tempfile.TemporaryDirectory[str]:
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)
