# AGENTS.md

Guidance for AI agents working on or reviewing this repository. The source of truth for behaviour is [docs/SPEC.md](docs/SPEC.md). When code and spec disagree, flag it.

## Layout and commands

| Part | Path | Check locally |
|---|---|---|
| Server (FastAPI, SQLite) | `server/` | `uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` |
| Admin (React, Vite) | `admin/` | `pnpm install && pnpm lint && pnpm typecheck && pnpm test && pnpm build` |
| Tablet app (Kotlin, Compose, Media3) | `android/` | `./gradlew ktlintCheck lintDebug testDebugUnitTest assembleDebug` |
| Image | `Dockerfile` | `docker build -t kidsmedia .` |
| Deployment | `deploy/` | installs to `/opt/kidsmedia` behind the host nginx |
| AI review (shared action `docdru/ai-review`) | `.github/ai-review.toml` | read from `main`; try a change with the action's `--dry-run --config` (see its README) |
| End-to-end (real nginx + TLS) | `server/e2e/` | needs a deployment: runs in `e2e.yml` nightly; label a PR `e2e` to run it there |

`openapi.json`, `server/uv.lock` and `admin/pnpm-lock.yaml` are generated. After API changes, run `cd server && uv run python -m kidsmedia.openapi > ../openapi.json`. The *sync generated files* workflow also commits them on feature branches.

Commits follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:` …).

## Review guidelines

Review the diff against the spec. Report only real problems, ranked: **P0** breaks a rule, security or data; **P1** is a likely bug or a spec mismatch; **P2** is maintainability; **P3** is a minor, optional improvement. Skip style nits that linters already cover.

Always check:

1. **Rules engine (SPEC §3).**
   - Every worked example in §3.4 must keep holding, and new behaviour needs a new §3.4 row plus a test.
   - Watch for off-by-one errors at window edges.
   - Lockouts are evening-anchored (§3.3b). `block` uses the current weekday. Item and minute limits use the current weekday for a new start, and the session's day for a file that is already playing: a session belongs to the day it started (§3.3).
   - Grace only lets the playing file finish and never starts a file (§3.3a).
   - Item limits count distinct files per day, and replays are free.
   - The app has a port of the engine for offline mode (`android/.../rules/RulesEngine.kt`). A change to `server/src/kidsmedia/rules.py` needs the same change there, with a test on both sides.
2. **Counting (§3.3).**
   - Heartbeats carry a cumulative `played_seconds_total`. The server stores `max(old, new)`, caps each delta at the elapsed wall time + 5 s, and only advances `last_heartbeat_at` when the total grows.
   - An unknown session returns 404 `unknown_session`.
3. **Enforcement points.** `/play/start`, `/play/heartbeat` and `/media/{id}/stream` must apply the same evaluation. Stream skips the item limit and honours an active grace, but never bypasses `block` or a revoked device.
4. **Security (§7).**
   - Every media and thumbnail byte goes through an authenticated endpoint and then `X-Accel-Redirect`. No public static paths.
   - Device tokens are stored hashed.
   - Upload filenames never reach filesystem paths.
   - Image decoding has size limits.
   - Admin endpoints require a session, and mutations require CSRF.
5. **Server returns codes, not sentences.** Child-facing reasons are a reason code plus params. Text lives in the clients.
6. **i18n.** Every user-visible string exists in English **and** German: `admin/src/i18n/{en,de}.json` and `android/.../values{,-de}/strings.xml`. Placeholders must match.
7. **Data model (§6.1).** Usage tables carry `profile_id`. Migrations go through Alembic and must not lose `plays` history. FK delete behaviour must match §6.1.
8. **API contract.** `openapi.json` is updated with API changes, and the Android and admin clients stay compatible.
9. **Tests.** New behaviour comes with tests. Rules-engine logic is tested with a frozen clock, never with the real time.
10. **Story packs (§13.5).** Every pack passes `python -m kidsmedia.packcheck` (cover and a picture per chapter, rendered from `art/*.svg`, problem sets, materials, write texts). The cover always shows the world's icon (its one recognisable motif), readable at tile size. Texts exist in every language with matching meaning.
11. **Deployment.** The container listens only on `127.0.0.1` via compose, data lives under `/opt/kidsmedia/data`, and nothing in the repo contains secrets. This repository is public: no personal data, no JOY files and no story packs other than `lighthouse-island`. Those belong to the private family image (§18).
