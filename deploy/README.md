# Deployment notes

Installation and nginx: see `install.sh`, `docker-compose.yml`, `nginx/kidsmedia.conf` and SPEC §4.1. Settings: `.env.example`.

## Read-aloud voices (SPEC §14.3)

The server reads the activity texts aloud with [Piper](https://github.com/OHF-Voice/piper1-gpl) and stores the result as `data/media/_speech/<key>.m4a`. nginx serves these files through the existing `/_media/` location, so its configuration doesn't change. A background worker renders what is missing at start-up and after an import, an example add or a world save, worlds in the queue first. Texts without a file yet use the tablet's own TextToSpeech. Admin → Activities shows the voice of each language, its licence and how many files are rendered, pending or failed. Failed texts are tried again after the next restart (`docker compose restart kidsmedia`).

**Bundled voices** (in the image under `/app/voices/`, listed with licence and attribution in `server/src/kidsmedia/voices.json`):

| Language | Voice | Licence |
|---|---|---|
| de | `de_DE-thorsten-medium` | model MIT, dataset Thorsten-Voice CC0 |
| en | `en_GB-cori-medium` | model MIT, dataset LibriVox public domain |
| el | `el_GR-rapunzelina-low` | model MIT, dataset CC0 |

They add about 190 MB to the image, and Piper with ONNX Runtime about another 100 MB. While the worker renders it uses about one CPU core and a few hundred MB of RAM; it frees the voices when it is idle.

**JOY for Greek.** JOY is a better Greek voice under CC BY-NC 4.0 (non-commercial use with attribution: "JOY Greek voice — Gemma4GR project, CC BY-NC 4.0"). It isn't in the public image: Hugging Face only hands it out after sign-in, and its licence doesn't allow passing it on freely. `voices.json` knows it, so once its two files are there it becomes the default Greek voice, and the admin shows its attribution: download `el_GR-joy-medium.onnx` and `el_GR-joy-medium.onnx.json` from https://huggingface.co/Efso/joy-greek-tts, put them into `data/voices/` and restart. A derived image can copy them into `/app/voices/` instead (see "Family image"). The expected sha256 of both files is in `voices.json`.

To use another voice, put its `.onnx` and `.onnx.json` into `data/voices/` and choose it with `VOICE_DE`, `VOICE_EN` or `VOICE_EL` in `.env`. A voice in `data/voices/` wins over a bundled one of the same name. For a voice that isn't in `voices.json`, you can record its licence and attribution in `data/voices/voices.json` (same format: `{"voices": [{"id": "…", "lang": "el", "license": "…", "attribution": "…"}]}`) so the admin shows them.

## Family image

The public image has the three free voices, one example story pack and no APK. To add your own content, build a small image on top of it in a private repository:

```dockerfile
FROM ghcr.io/docdru/kidsmedia:0.5.0
COPY voices/el_GR-joy-medium.onnx voices/el_GR-joy-medium.onnx.json /app/voices/
COPY packs/ /app/packs/
COPY apk-bundle/ /app/apk-bundle/
ENV EXTRA_PACKS_DIR=/app/packs
```

- `packs/<slug>/` are story packs in the pack format (SPEC §13.5). Check them before you build: `docker run --rm -v "$PWD/packs:/packs:ro" ghcr.io/docdru/kidsmedia:0.5.0 python -m kidsmedia.packcheck /packs`. Leave the `art/` sources out of the image (`.dockerignore`).
- `apk-bundle/` holds your signed APK of the same version and its `release.json` (`{"version_name":"0.5.0","version_code":500,"file":"kidsmedia-0.5.0.apk"}`; version code = major × 10000 + minor × 100 + patch). Then tablets update themselves (SPEC §4.3). Build the APK from the tag `v0.5.0` with `./gradlew assembleRelease -PversionName=0.5.0 -PversionCode=500` and the keystore in `KM_KEYSTORE_FILE`, `KM_KEYSTORE_PASSWORD`, `KM_KEY_ALIAS`, `KM_KEY_PASSWORD`. Keep the keystore safe: updates must be signed with the same key.
- Dependabot (`package-ecosystem: docker`) turns a new public version into a pull request that bumps the `FROM` line.
- On the server, set `KIDSMEDIA_IMAGE` (and `KIDSMEDIA_TAG`) in `.env` to your image, and `docker login ghcr.io` if it's private.
