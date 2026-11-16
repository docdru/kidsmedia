"""Kids Media server."""

import os
import re

__version__ = "0.1.0"  # the API schema's version (openapi.json), not the release


def running_version() -> str:
    """The release this server runs (SPEC §10.2), from the image's `KIDSMEDIA_VERSION`:
    the tag it was built from without the "v" (`v0.9.0` -> `0.9.0`), `main-<short sha>` for
    images from main, and `dev` when unset (run from source)."""
    value = os.environ.get("KIDSMEDIA_VERSION", "").strip()
    if re.fullmatch(r"v\d+\.\d+\.\d+", value):
        return value[1:]
    if m := re.fullmatch(r"main-([0-9a-f]{7})[0-9a-f]*", value):
        return f"main-{m.group(1)}"
    return value or "dev"
