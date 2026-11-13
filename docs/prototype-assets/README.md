# Prototype assets

Sample thumbnails used by the clickable prototypes (see the repository README). All artwork is generated, so there are no third-party images.

| Files | What |
|---|---|
| `m1`–`m5.webp` | Music covers (square) |
| `p1`–`p5.webp`, `g_moon.webp` | "The Moon Pirates" episodes and series cover |
| `f1`–`f4.webp`, `g_fox.webp` | "Detective Fox" cases and series cover |
| `c1`–`c4.webp` | "Bubble Garden" cartoon stills (16:9, like video frames) |

## Regenerating

`generate.py` draws the images as SVG and converts them to WebP.

Requirements:
- Python 3 with Pillow (`pip install pillow`)
- `rsvg-convert` from librsvg (`apt install librsvg2-bin`, `brew install librsvg`)
- The DejaVu Sans font, used for the episode badges (installed by default on most Linux systems)

```sh
python3 docs/prototype-assets/generate.py   # writes to docs/prototype-assets/out/
```

These images can later be embedded into generated test media (e.g. as an ID3 `APIC` picture) for the thumbnail pipeline tests in SPEC §6.4.
