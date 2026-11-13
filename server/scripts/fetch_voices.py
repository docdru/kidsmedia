"""Download the bundled Piper voices named in voices.json and check them (SPEC §14.3).

Used by the Dockerfile:

    python fetch_voices.py voices.json /voices [VENDORED_DIR]

Standard library only. Only voices with `"bundled": true` are fetched. Every `.onnx` model
must have a `sha256` in the manifest and match it; the small `.onnx.json` configs are pinned
by the commit in their URL (`…/resolve/<commit>/…`) instead. A file marked `"vendored": true`
has no URL: it is copied from VENDORED_DIR and must match its `sha256` (no voice uses this
at the moment). The digest of every file is printed, so new entries can be pinned.
"""

import hashlib
import json
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ATTEMPTS = 3
CHUNK = 1 << 20
PINNED_URL = re.compile(r"/resolve/[0-9a-f]{40}/")


def check_pinned(name: str, url: str, sha256: str | None) -> None:
    """A model needs its sha256; any other file at least a URL pinned to a commit."""
    if name.endswith(".onnx") and not sha256:
        raise SystemExit(f"{name}: a bundled model needs a sha256 in the manifest")
    if not sha256 and not PINNED_URL.search(url):
        raise SystemExit(f"{name}: no sha256 and the URL isn't pinned to a commit ({url})")


def copy_vendored(source_dir: Path | None, dest: Path, sha256: str | None) -> None:
    """A file kept in the repository: copied from source_dir and checked against its sha256."""
    if not sha256:
        raise SystemExit(f"{dest.name}: a vendored file needs a sha256 in the manifest")
    if source_dir is None:
        raise SystemExit(f"{dest.name}: vendored, but no VENDORED_DIR was given")
    source = source_dir / dest.name
    if not source.is_file():
        raise SystemExit(f"{dest.name}: vendored, but missing from {source_dir}")
    digest = hashlib.sha256()
    with source.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != sha256:
        raise SystemExit(f"{dest.name}: sha256 {actual}, expected {sha256}")
    shutil.copyfile(source, dest)
    print(f"{actual}  {dest.name}")


def fetch(url: str, dest: Path, sha256: str | None) -> None:
    if not url.startswith("https://"):
        raise SystemExit(f"{dest.name}: only https URLs ({url})")
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, ATTEMPTS + 1):
        digest = hashlib.sha256()
        try:
            # https only (checked above); the URLs come from the repository's manifest.
            with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:  # noqa: S310
                while chunk := r.read(CHUNK):
                    digest.update(chunk)
                    f.write(chunk)
            break
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            if attempt == ATTEMPTS:
                raise SystemExit(f"{dest.name}: download failed: {exc}") from exc
            time.sleep(5 * attempt)
    actual = digest.hexdigest()
    if sha256 and actual != sha256:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"{dest.name}: sha256 {actual}, expected {sha256}")
    tmp.replace(dest)
    print(f"{actual}  {dest.name}")


def main(manifest: Path, target: Path, vendored: Path | None = None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    voices = json.loads(manifest.read_text(encoding="utf-8"))["voices"]
    for voice in voices:
        if not voice.get("bundled"):
            continue
        for file in voice["files"]:
            name = Path(file["name"]).name  # a plain file name, never a path
            if file.get("vendored"):
                copy_vendored(vendored, target / name, file.get("sha256"))
                continue
            check_pinned(name, file["url"], file.get("sha256"))
            fetch(file["url"], target / name, file.get("sha256"))


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit("usage: fetch_voices.py voices.json TARGET_DIR [VENDORED_DIR]")
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) == 4 else None)
