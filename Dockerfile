# syntax=docker/dockerfile:1

# ---- Admin SPA -------------------------------------------------------------
FROM node:26-alpine AS admin
WORKDIR /admin
# Node 25+ no longer bundles Corepack: install it from npm. It still picks
# the pnpm version from "packageManager" in admin/package.json.
RUN npm install -g corepack@latest && corepack enable
COPY admin/package.json admin/pnpm-lock.yaml* ./
RUN if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else pnpm install --no-frozen-lockfile; fi
COPY admin/ ./
# API types are generated from the schema at build time (SPEC §4.2).
COPY openapi.json /openapi.json
RUN pnpm build

# ---- Python dependencies ---------------------------------------------------
FROM python:3.15-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY server/pyproject.toml server/uv.lock* server/README.md ./
COPY server/src ./src
RUN if [ -f uv.lock ]; then uv sync --frozen --no-dev; else uv sync --no-dev; fi

# ---- Piper voices (SPEC §14.3) ---------------------------------------------
# The read-aloud voices listed as bundled in voices.json, pinned to a commit of
# rhasspy/piper-voices and checked against their sha256. Plain downloads, the same
# for every platform, so they are fetched once on the build host (no emulation).
# JOY (Greek) is not bundled: its licence is non-commercial. The family image adds it
# to /app/voices/, or a parent puts it into data/voices/.
FROM --platform=$BUILDPLATFORM python:3.15-slim AS voices
WORKDIR /build
COPY server/src/kidsmedia/voices.json server/scripts/fetch_voices.py ./
RUN python fetch_voices.py voices.json /voices

# ---- Runtime ---------------------------------------------------------------
FROM python:3.15-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

# The UID/GID only matter for files written to the data volume; compose can
# override them with `user:` so nginx on the host can read /opt/kidsmedia/data.
RUN groupadd --system --gid 10001 kidsmedia \
    && useradd --system --uid 10001 --gid kidsmedia --home /app kidsmedia \
    && mkdir -p /data && chown kidsmedia:kidsmedia /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=admin /admin/dist /app/admin-dist
# About 190 MB: one voice per language. Parents may add voices to data/voices/.
COPY --from=voices /voices /app/voices
# Release images: the signed APK of the same tag, put here by release.yml (SPEC §4.3).
COPY apk-bundle/ /app/apk-bundle/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    ADMIN_DIST=/app/admin-dist \
    BUNDLED_APK_DIR=/app/apk-bundle \
    BUNDLED_VOICES_DIR=/app/voices \
    PORT=8080

VOLUME ["/data"]
EXPOSE 8080
USER kidsmedia

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

CMD ["kidsmedia"]
