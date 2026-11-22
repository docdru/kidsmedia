# Deployment notes

Installation and nginx: see `install.sh`, `docker-compose.yml`, `nginx/kidsmedia.conf` and SPEC §4.1. Settings: `.env.example`.

## Check the nginx routing

If `KM_PORT` isn't 8080 (another service already uses it), change `$km_backend` in the snippet. A hand-written config from an older snippet may still have `proxy_pass http://127.0.0.1:8080;` in the nested upload locations: then uploads go to whatever runs on 8080, and a tablet's photos of drawings and stories never arrive. After every nginx change, check that the upload paths reach Kids Media. Each must answer **JSON** (`{"reason": …}`, usually 401 without a login), never an HTML page:

```bash
for p in "POST /api/activities/photo?world_id=1&position=1&kind=draw" \
         "PUT /api/admin/groups/1/upload" "PUT /api/admin/app/apk" \
         "PUT /api/admin/worlds/import" "PUT /api/admin/worlds/1/cover"; do
  set -- $p
  printf '%-40s ' "$2"
  curl -s -o /dev/null -w '%{http_code} %{content_type}\n' -X "$1" "https://YOUR-DOMAIN$2"
done
```

## Single sign-on with Authelia (SPEC §7.1)

The admin can sign in through Authelia's OpenID Connect provider. Once it is on, the password login is off; tablets are not affected.

1. Make a client secret and its hash (on the Authelia host):

   ```bash
   docker run --rm authelia/authelia:latest authelia crypto hash generate pbkdf2 --variant sha512 --random --random.length 72 --random.charset rfc3986
   ```

   It prints the random password (goes to Kids Media) and its digest (goes to Authelia).

2. Add the client to Authelia's `configuration.yml` (under `identity_providers.oidc`, which needs its `hmac_secret` and `jwks` set up already) and restart Authelia:

   ```yaml
   identity_providers:
     oidc:
       clients:
         - client_id: 'kidsmedia'
           client_name: 'Kids Media'
           client_secret: '$pbkdf2-sha512$...'      # the digest from step 1
           public: false
           authorization_policy: 'one_factor'      # or two_factor
           require_pkce: true
           pkce_challenge_method: 'S256'
           redirect_uris:
             - 'https://YOUR-DOMAIN/api/admin/oidc/callback'
           scopes: ['openid', 'profile', 'groups']
           grant_types: ['authorization_code']
           response_types: ['code']
           token_endpoint_auth_method: 'client_secret_basic'
           userinfo_signed_response_alg: 'none'
   ```

3. In `/opt/kidsmedia/.env`:

   ```bash
   OIDC_ISSUER=https://auth.example.com
   OIDC_CLIENT_SECRET=<the random password from step 1>
   OIDC_ALLOWED_USERS=mama,papa          # Authelia user names …
   OIDC_ALLOWED_GROUPS=                  # … and/or groups, e.g. kidsmedia-admins
   ```

   Then `cd /opt/kidsmedia && sudo docker compose up -d`. The log says `Single sign-on with … is on`, or names what is missing.

To give someone access, add their account (or add them to an allowed group in Authelia) and restart the container. To take it away, remove them and restart: their open sessions end at once. To go back to the password, empty `OIDC_ISSUER` and restart; the first admin account from `ADMIN_PASSWORD` still exists.

nginx needs no change: the sign-in routes are under `/api/`. The server must reach Authelia over HTTPS (`OIDC_ISSUER`); if both run on the same host behind the host nginx, check that the container can resolve and reach that address.

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
- Big packs don't have to be in the image: put their zips (with `speech/`) in a WebDAV folder, e.g. a Seafile library through SeafDAV, set `PACKS_WEBDAV_URL`, `PACKS_WEBDAV_USER` and `PACKS_WEBDAV_PASSWORD` in `.env`, restart, and import them in the admin under Ideas → *Story packs on WebDAV* (SPEC §13.5a). With Seafile, SeafDAV must be enabled, and if the server asks for a separate WebDAV password (set in your Seafile profile), use that one.
- On the server, set `KIDSMEDIA_IMAGE` (and `KIDSMEDIA_TAG`) in `.env` to your image, and `docker login ghcr.io` if it's private.
