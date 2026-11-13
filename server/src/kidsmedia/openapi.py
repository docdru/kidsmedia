"""Export the OpenAPI schema.

    uv run python -m kidsmedia.openapi > ../openapi.json

CI fails when the committed openapi.json differs from the generated one
(tests/test_openapi.py).
"""

import json
import tempfile
from pathlib import Path

from kidsmedia.config import Settings
from kidsmedia.main import create_app


def render() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        schema = create_app(Settings(data_dir=Path(tmp), admin_dist=None)).openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    print(render(), end="")
