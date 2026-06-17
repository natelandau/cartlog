# syntax=docker/dockerfile:1.7

# ============================================================
# Stage 1: CSS builder - compile Tailwind + daisyUI to app.css
# ============================================================
FROM node:lts-alpine AS css-builder

WORKDIR /build

# Install npm deps first so this layer caches unless package.json changes
COPY package.json package-lock.json ./
RUN npm ci

# Tailwind v4 scans the templates referenced from assets/app.css, so the whole
# source tree must be visible to the scanner before the build runs.
COPY src/ ./src/

# `build:css` reads assets/app.css and writes the minified static/app.css
RUN npm run build:css

# ============================================================
# Stage 2: Python builder - install dependencies and project
# ============================================================
FROM ghcr.io/astral-sh/uv:0.11.21-python3.14-trixie-slim AS python-builder

# Build-time system deps for any native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libc6-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

# Cache dependency install separately from project install. README/LICENSE are
# referenced by pyproject metadata, so the build needs them present.
COPY uv.lock pyproject.toml README.md LICENSE ./
RUN uv sync --locked --no-dev --no-cache --no-install-project

# Install the project itself (editable: the venv .pth points back at /app/src)
COPY src/ ./src/
RUN uv sync --locked --no-dev --no-cache

# ============================================================
# Stage 3: Runtime - lean production image
# ============================================================
FROM python:3.14-slim-trixie

# Runtime-only system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates tini tzdata gosu \
    && rm -rf /var/lib/apt/lists/*

# Default timezone. Overridable at runtime via the TZ env var, which the entrypoint
# re-applies to /etc/localtime before dropping privileges (e.g. TZ=America/New_York).
ENV TZ=Etc/UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ >/etc/timezone

# Create default app user
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# PUID/PGID defaults (overridden at runtime via env vars)
ENV PUID=1000
ENV PGID=1000

WORKDIR /app

# Copy the built venv from the python builder
COPY --from=python-builder /app/.venv .venv

# Application source (the editable install resolves to this path) plus the
# Alembic config and migrations, which `cartlog serve` runs at startup.
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini

# Compiled stylesheet from the css builder, overwriting the (gitignored) source
# placeholder so the served CSS matches the templates without a runtime rebuild.
COPY --from=css-builder /build/src/cartlog/web/static/app.css ./src/cartlog/web/static/app.css

# Entrypoint
COPY scripts/docker_entry.sh ./scripts/docker_entry.sh
RUN chmod +x scripts/docker_entry.sh

ENV PATH="/app/.venv/bin:$PATH"

# Default data location for the SQLite database and stored receipt images. Mount
# a volume at /data to persist both across container restarts. The database path is
# bare; cartlog adds the sqlite:/// prefix and checks the directory exists at startup.
ENV CARTLOG_DATABASE_URL=/data/cartlog.db
ENV CARTLOG_IMAGE_STORAGE_DIR=/data/receipt_images

EXPOSE 8000

# OCI labels (placed after stable layers to avoid cache busting)
LABEL org.opencontainers.image.source=https://github.com/natelandau/cartlog
LABEL org.opencontainers.image.description="Scan and parse grocery receipts to track prices and spending over time."
LABEL org.opencontainers.image.url=https://github.com/natelandau/cartlog
LABEL org.opencontainers.image.title="cartlog"

ENTRYPOINT ["tini", "--"]
CMD ["scripts/docker_entry.sh"]
