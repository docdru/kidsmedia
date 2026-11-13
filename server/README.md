# kidsmedia server

FastAPI + SQLite backend (see `docs/SPEC.md` §4.1).

```sh
cd server
uv sync                                  # install deps incl. dev group
uv run kidsmedia                         # serve on :8080
uv run pytest                            # tests (incl. openapi.json drift check)
uv run ruff check && uv run ruff format --check && uv run mypy
uv run python -m kidsmedia.openapi > ../openapi.json   # after API changes
```

Configuration is read from environment variables, see `deploy/.env.example`.
