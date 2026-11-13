# kidsmedia

A self-hosted media server for a child's Android tablet. A parent uploads music, audiobooks and videos to a Docker web app, sorts them into groups and sets usage rules: for example *one audiobook file per day from two series, weekends only*, *90 minutes of music*, or *bedtime from 19:30*. A dedicated Android app streams the media and enforces the rules.

**Status:** working and in use; the milestones are in SPEC §11.

Uploading kids-media-parent-guide-narrated.mp4…

## Documents

- [docs/SPEC.md](docs/SPEC.md): product and technical specification (rules model, API, data model, thumbnails, Android app, admin, deployment, CI, milestones)
- Clickable prototypes (claude.ai, private to the owner):
  - Tablet app: https://claude.ai/artifact/1rgbdLGQr5ZmPDgHd7gzDJ
  - Parent admin: https://claude.ai/artifact/2GctEMxoouBG8cpfnRKNrz
- [docs/prototype-assets/](docs/prototype-assets/): the sample thumbnails used in the prototypes, plus the script that generates them

## Development

| Part | Path | Quick start |
|---|---|---|
| Server | `server/` | `uv sync && uv run kidsmedia` (serves on :8080) |
| Admin | `admin/` | `pnpm install && pnpm dev` (proxies `/api` to :8080) |
| Tablet app | `android/` | open in Android Studio, or `./gradlew assembleDebug` |
| Image | `Dockerfile` | `docker build -t kidsmedia .` |

Every part has its own CI workflow in `.github/workflows/`. Review rules for agents and humans are in [AGENTS.md](AGENTS.md).

## Layout

```
server/    FastAPI + SQLite API, rules engine, streaming, thumbnails
admin/     React + Vite parent admin (en/de)
android/   Kotlin / Compose / Media3 tablet app (en/de)
deploy/    docker-compose.yml, nginx snippet, install to /opt/kidsmedia
docs/      spec, decisions, screenshots
```

## Deployment target

Docker on a Linux server behind the existing host nginx (Let's Encrypt). App and data live in `/opt/kidsmedia`. See SPEC §4.1.

## Setting up the tablet

1. Install the APK and open the app. Enter your server address and the 6-digit code from the admin page (Tablets → Pair new tablet).
2. Choose a 4-digit parent PIN. Hold the small dot in the top-right corner of the home screen for 2 seconds to reach the settings (server, tablet name, change PIN, pair again). Your child picks the app's language with the flags on the home screen.
3. Keep your child inside the app with one of these (SPEC §4.3):
   - **Screen pinning** (Settings → Security → App pinning, wording varies by maker): open Kids Media, open the app overview and pin it. Unpinning asks for the device PIN.
   - **Google Family Link**: a supervised profile where only Kids Media and the apps you allow are available.
4. Offline mode follows the server's clock from the last sync, so changing the tablet's time doesn't unlock anything. Only after a restart without a connection does the app trust the tablet's own clock, until it's online again. Pinning or Family Link also keeps the child out of the clock settings.

## Releases

Pushing a tag like `v1.0.0` publishes the Docker image `ghcr.io/docdru/kidsmedia:1.0.0` and a GitHub Release. This public image carries no APK, so it doesn't update tablets; install the app by hand (`./gradlew assembleRelease` with your own keystore, see `android/`).

Self-updating tablets need a signed APK inside the image (SPEC §4.3). The maintainer's own server runs a private image built on top of this one, which adds the signed APK of the same tag, the Greek voice JOY and more story packs (SPEC §18, `deploy/README.md` → "Family image"). You can do the same: a Dockerfile `FROM ghcr.io/docdru/kidsmedia:<version>` that copies `release.json` and your APK into `/app/apk-bundle/`. Keep your keystore safe: updates to an installed app must be signed with the same key.

## License

[MIT](LICENSE). Provided as is, without warranty or liability. The bundled voices have their own licences (`deploy/README.md` → read aloud).
