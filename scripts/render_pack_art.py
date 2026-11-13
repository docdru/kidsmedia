#!/usr/bin/env python3
"""Render the SVG sources of story packs to the WebP pictures the server reads.

    python3 scripts/render_pack_art.py                      # all bundled packs
    python3 scripts/render_pack_art.py lighthouse-island
    PACKS_DIR=../kidsmedia-family/packs python3 scripts/render_pack_art.py   # other packs

For each ``<packs>/<slug>/art/<name>.svg`` it writes ``<packs>/<slug>/<name>.webp`` at
1024 x 1024 (SPEC §13.5). ``<packs>`` is ``server/src/kidsmedia/packs`` unless PACKS_DIR
is set.
Needs ``rsvg-convert`` (librsvg) and Pillow; it is a developer tool, not used at runtime.

Every cover shows the world's icon, its one recognisable motif, large enough to read on a
small tile (SPEC §13.5). Check a new cover at about 100 px before committing it.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent.parent / "server" / "src" / "kidsmedia" / "packs"
PACKS = Path(os.environ["PACKS_DIR"]) if os.environ.get("PACKS_DIR") else BUNDLED
SIZE = 1024


def render(svg: Path, out: Path) -> None:
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "out.png"
        subprocess.run(
            ["rsvg-convert", "-w", str(SIZE), "-h", str(SIZE), "-o", str(png), str(svg)],
            check=True,
        )
        with Image.open(png) as img:
            img.convert("RGB").save(out, "WEBP", quality=86, method=6)


def main(argv: list[str]) -> int:
    slugs = argv or sorted(p.name for p in PACKS.iterdir() if (p / "world.json").is_file())
    count = 0
    for slug in slugs:
        for svg in sorted((PACKS / slug / "art").glob("*.svg")):
            out = PACKS / slug / f"{svg.stem}.webp"
            render(svg, out)
            print(f"{out.relative_to(PACKS)}  {out.stat().st_size // 1024} KB")
            count += 1
    print(f"{count} pictures")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
