# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — build the React/Vite dashboard into dashboard/dist
# ─────────────────────────────────────────────────────────────────────────────
FROM node:22-slim AS dashboard-build
WORKDIR /dashboard

# Install dependencies first (cached unless the lockfile changes).
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci

# Build the static bundle (Vite default outDir = dist).
COPY dashboard/ ./
RUN npm run build


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — builder: resolve + install the Python venv (needs compilers)
# The image is built WITHOUT the `voice` extra (no pipecat, no torch, no
# pyaudio) — the container only ever runs the API/heartbeat/telegram/monitor
# roles, none of which need the voice stack (see doc/portability.md). This is
# the whole point of the extra: a lean, fast-building headless image. The
# toolchain stays out of the runtime image — only the finished /app/.venv is
# copied forward.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# uv — pinned to the version that produced uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Resolve and install the runtime dependencies first (no dev deps, no voice
# extra), from the lockfile, WITHOUT the project itself — this layer is cached
# across code edits.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application source and the pre-built dashboard, then install the
# project package into the venv (still no dev deps, no voice extra).
COPY . .
COPY --from=dashboard-build /dashboard/dist ./dashboard/dist
RUN uv sync --frozen --no-dev


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — runtime: lean image with just the runtime shared libraries
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# ca-certificates — TLS for outbound provider calls. No audio/ML shared libs
# (libportaudio2/libgomp1/libsndfile1) needed: the voice extra isn't installed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    HOME=/data \
    LHASSA_API_HOST=0.0.0.0 \
    LHASSA_API_PORT=7766

WORKDIR /app

# Bring over the fully built app + venv from the builder stage.
COPY --from=builder /app /app

# ── Non-root user; /data holds ~/.lhassa (DBs, keys, backups, logs, plugins) ──
# HOME=/data is the single, clean override: every default data path in
# lhassa/config.py and main.py resolves from Path.home() (~/.lhassa, ~/.config),
# so pointing HOME at the mounted volume relocates ALL persistent state there.
RUN useradd --create-home --home-dir /data --uid 10001 lhassa \
    && mkdir -p /data/.lhassa \
    && chown -R lhassa:lhassa /data \
    && chmod +x /app/docker/entrypoint.sh /app/scripts/lhassa-heartbeat

USER lhassa

EXPOSE 7766

# Volume for persistent data (DBs, keys, backups). Compose mounts a named volume.
VOLUME ["/data"]

# Liveness — hit the dashboard API status endpoint (no extra packages needed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('LHASSA_API_PORT','7766')+'/api/status', timeout=3).status==200 else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
