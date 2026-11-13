"""Photos of drawings and stories (SPEC §15.3): decoding, stripping and the WebP files.

No database access here. A photo is decoded like other images (§6.4: decompression-bomb
limit), EXIF-rotated, stripped of all metadata (no location, no device data) and written as
WebP at `data/media/_photos/<key>-<size>.webp`, `key = p<id>-<sha256[:8]>`.
"""

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from kidsmedia import thumbs

PhotoSize = Literal["sm", "lg"]
PHOTO_SIZES: dict[PhotoSize, int] = {"lg": 1600, "sm": 320}  # long edge, never upscaled
MAX_PHOTO_BYTES = 15 * 1024 * 1024
PHOTO_KEY_RE = re.compile(r"^p[0-9]{1,12}-[0-9a-f]{8}$")


@dataclass(frozen=True)
class StoredPhoto:
    key: str
    width: int  # of the lg file
    height: int


def decode(data: bytes) -> Image.Image:
    """Untrusted bytes -> an upright RGB image (thumbs.open_image: size limits, EXIF
    rotation). Raises thumbs.MediaError("bad_image") for a body that doesn't decode."""
    return thumbs.open_image(data)


def _webp(img: Image.Image, edge: int) -> tuple[bytes, int, int]:
    copy = img.copy()
    copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    copy.info = {}  # nothing of the original's metadata travels on
    buf = io.BytesIO()
    copy.save(buf, "WEBP", quality=thumbs.WEBP_QUALITY, method=4)  # no exif/icc/xmp given
    return buf.getvalue(), copy.width, copy.height


def photo_file(folder: Path, key: str, size: PhotoSize) -> Path:
    return folder / f"{key}-{size}.webp"


def write_photo(folder: Path, photo_id: int, img: Image.Image) -> StoredPhoto:
    """Write the lg and sm files of a photo (temporary file, then rename)."""
    folder.mkdir(parents=True, exist_ok=True)
    blobs: dict[PhotoSize, bytes] = {}
    width = height = 0
    for size, edge in PHOTO_SIZES.items():
        blob, w, h = _webp(img, edge)
        blobs[size] = blob
        if size == "lg":
            width, height = w, h
    key = f"p{photo_id}-{hashlib.sha256(blobs['lg']).hexdigest()[:8]}"
    for size, blob in blobs.items():
        dest = photo_file(folder, key, size)
        tmp = dest.with_name(f".{dest.name}.tmp")
        tmp.write_bytes(blob)
        tmp.replace(dest)
    return StoredPhoto(key, width, height)


def delete_photo_files(folder: Path, key: str) -> None:
    for size in PHOTO_SIZES:
        photo_file(folder, key, size).unlink(missing_ok=True)
