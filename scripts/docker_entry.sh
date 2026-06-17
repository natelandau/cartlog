#!/usr/bin/env bash
set -euo pipefail

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Adjust appuser GID if needed
if [[ "${PGID}" != "1000" ]]; then
    if getent group "${PGID}" > /dev/null 2>&1; then
        printf "WARNING: GID %s is already in use, skipping groupmod\n" "${PGID}" >&2
    else
        groupmod -o -g "${PGID}" appuser
    fi
fi

# Adjust appuser UID if needed
if [[ "${PUID}" != "1000" ]]; then
    if getent passwd "${PUID}" > /dev/null 2>&1; then
        printf "WARNING: UID %s is already in use, skipping usermod\n" "${PUID}" >&2
    else
        usermod -o -u "${PUID}" appuser
    fi
fi

# The app dir holds read-only code, so a non-recursive chown of the mount point is enough.
chown "${PUID}:${PGID}" /app

# The data volume holds the SQLite database and stored receipt images, all written by the
# app user. Chown recursively so ownership is correct even when the volume already holds
# files from a previous run made under a different PUID/PGID.
[[ -d /data ]] && chown -R "${PUID}:${PGID}" /data

# Drop privileges and start the app. `cartlog serve` runs migrations, the web server,
# and the in-process ingestion worker pool in one process. The CSS is compiled in the
# Docker build, so --skip-css-build keeps Node and node_modules out of the runtime image.
exec gosu appuser cartlog serve \
    --host "${CARTLOG_HOST:-0.0.0.0}" \
    --port "${CARTLOG_PORT:-8000}" \
    --workers "${CARTLOG_WORKERS:-1}" \
    --skip-css-build
