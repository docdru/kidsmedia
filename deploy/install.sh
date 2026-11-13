#!/usr/bin/env bash
# Install or update Kids Media in /opt/kidsmedia (SPEC §4.1).
#
#   sudo ./deploy/install.sh            # from a checkout of the repository
#
# Idempotent: an existing .env and data/ are never overwritten.
set -euo pipefail

PREFIX="${PREFIX:-/opt/kidsmedia}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Please run as root (sudo)." >&2
    exit 1
fi
command -v docker >/dev/null || { echo "docker is not installed." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose plugin is missing." >&2; exit 1; }

install -d -m 0755 "$PREFIX"
install -m 0644 "$HERE/docker-compose.yml" "$PREFIX/docker-compose.yml"

if [[ ! -f "$PREFIX/.env" ]]; then
    install -m 0600 "$HERE/.env.example" "$PREFIX/.env"
    password="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)"
    sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=${password}/" "$PREFIX/.env"
    echo "Created $PREFIX/.env with a random admin password:"
    echo "    ADMIN_PASSWORD=${password}"
    echo "Edit PUBLIC_URL and TZ there before the first start."
fi

# shellcheck disable=SC1091
set -a; source "$PREFIX/.env"; set +a
uid="${KM_UID:-10001}"
gid="${KM_GID:-10001}"

install -d -m 0750 -o "$uid" -g "$gid" "$PREFIX/data"
for sub in media thumbs apk; do
    install -d -m 0750 -o "$uid" -g "$gid" "$PREFIX/data/$sub"
done

if [[ ! -f /etc/nginx/snippets/kidsmedia.conf ]] && [[ -d /etc/nginx/snippets ]]; then
    install -m 0644 "$HERE/nginx/kidsmedia.conf" /etc/nginx/snippets/kidsmedia.conf
    echo "Installed /etc/nginx/snippets/kidsmedia.conf. Include it in your TLS server block, then: nginx -t && systemctl reload nginx"
fi

cat <<EOF

nginx serves media files directly, so it must be able to read $PREFIX/data.
If nginx runs as www-data, one option is:
    usermod -aG $gid www-data && systemctl restart nginx
(or set KM_GID in .env to an existing group that www-data belongs to).

EOF

cd "$PREFIX"
# KM_SKIP_PULL=1 uses a locally built image (the nightly e2e test does this).
if [[ "${KM_SKIP_PULL:-0}" != 1 ]]; then
    docker compose pull
fi
docker compose up -d
echo "Kids Media is starting. Check: curl -fsS http://127.0.0.1:${KM_PORT:-8080}/healthz"
