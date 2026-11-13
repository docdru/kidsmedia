from pathlib import Path

from kidsmedia.openapi import render

OPENAPI_FILE = Path(__file__).resolve().parents[2] / "openapi.json"


def test_openapi_json_is_up_to_date() -> None:
    """The committed openapi.json must match the code.

    Regenerate with:  cd server && uv run python -m kidsmedia.openapi > ../openapi.json
    """
    generated = render()
    committed = OPENAPI_FILE.read_text(encoding="utf-8") if OPENAPI_FILE.exists() else ""
    assert committed == generated, (
        "openapi.json is out of date. Regenerate it with:\n"
        "  cd server && uv run python -m kidsmedia.openapi > ../openapi.json"
    )
