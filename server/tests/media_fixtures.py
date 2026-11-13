"""Generate small media files with ffmpeg for tests (SPEC §6.4 'Tests (CI)')."""

import io
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True, timeout=120)


def cover_png(path: Path, color: str = "#4552C7", size: tuple[int, int] = (600, 600)) -> Path:
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    draw.ellipse((size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4), fill="#F7E7A6")
    img.save(path)
    return path


def mp3(path: Path, seconds: int = 5, cover: Path | None = None, title: str | None = None) -> Path:
    meta = ["-metadata", f"title={title}"] if title else []
    if cover is None:
        ff(
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:a",
            "libmp3lame",
            *meta,
            str(path),
        )
    else:
        raw = path.with_suffix(".raw.mp3")
        ff(
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:a",
            "libmp3lame",
            str(raw),
        )
        ff(
            "-i",
            str(raw),
            "-i",
            str(cover),
            "-map",
            "0",
            "-map",
            "1",
            "-c",
            "copy",
            "-id3v2_version",
            "3",
            "-metadata:s:v",
            "comment=Cover (front)",
            *meta,
            str(path),
        )
        raw.unlink()
    return path


def m4a_with_cover(path: Path, cover: Path, seconds: int = 5) -> Path:
    raw = path.with_suffix(".raw.m4a")
    ff("-f", "lavfi", "-i", f"sine=duration={seconds}", "-c:a", "aac", str(raw))
    ff(
        "-i",
        str(raw),
        "-i",
        str(cover),
        "-map",
        "0",
        "-map",
        "1",
        "-c",
        "copy",
        "-disposition:v:0",
        "attached_pic",
        str(path),
    )
    raw.unlink()
    return path


def mp4_black_intro(path: Path, faststart: bool = True) -> Path:
    """4 s black, then 16 s colour bars, with audio."""
    flags = ["-movflags", "+faststart"] if faststart else []
    ff(
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:d=4:r=25",
        "-f",
        "lavfi",
        "-i",
        "testsrc=s=320x240:d=16:r=25",
        "-f",
        "lavfi",
        "-i",
        "sine=duration=20",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        *flags,
        str(path),
    )
    return path


def mp4_with_cover(path: Path, cover: Path) -> Path:
    raw = path.with_suffix(".raw.mp4")
    ff(
        "-f",
        "lavfi",
        "-i",
        "testsrc=s=320x240:d=6:r=25",
        "-f",
        "lavfi",
        "-i",
        "sine=duration=6",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(raw),
    )
    ff(
        "-i",
        str(raw),
        "-i",
        str(cover),
        "-map",
        "0",
        "-map",
        "1",
        "-c",
        "copy",
        "-disposition:v:1",
        "attached_pic",
        "-movflags",
        "+faststart",
        str(path),
    )
    raw.unlink()
    return path


def mp4_unsupported_codec(path: Path) -> Path:
    ff("-f", "lavfi", "-i", "testsrc=s=160x120:d=3:r=25", "-c:v", "mpeg4", str(path))
    return path


def corrupt_embedded_picture(path: Path) -> Path:
    """Damage the PNG inside an mp3's APIC frame while keeping the file playable."""
    data = bytearray(path.read_bytes())
    start = data.find(b"\x89PNG\r\n\x1a\n")
    assert start >= 0
    for i in range(start + 16, min(start + 400, len(data))):
        data[i] = 0
    path.write_bytes(bytes(data))
    return path


def mp3_with_untyped_cover(
    path: Path, color: str = "#C74552", size: tuple[int, int] = (300, 300)
) -> Path:
    """An mp3 whose ID3v2.3 APIC frame has an empty MIME type, as fre:ac writes it.

    ffmpeg skips such a picture ("Unknown attached picture mimetype"), macOS shows it.
    The tag is written by hand so the frame is exactly that, byte for byte.
    """
    raw = path.with_suffix(".raw.mp3")
    ff(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=5",
        "-c:a",
        "libmp3lame",
        "-id3v2_version",
        "0",
        str(raw),
    )
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    # encoding latin-1, MIME "" + NUL, picture type 3 (front cover), description "" + NUL
    body = b"\x00" + b"\x00" + b"\x03" + b"\x00" + buf.getvalue()
    frame = b"APIC" + len(body).to_bytes(4, "big") + b"\x00\x00" + body
    n = len(frame)
    syncsafe = bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])
    path.write_bytes(b"ID3\x03\x00\x00" + syncsafe + frame + raw.read_bytes())
    raw.unlink()
    return path
