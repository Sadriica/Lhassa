# Deployment — Docker / portability

This is how to run Lhassa anywhere via Docker: a multi-stage image, a Compose
topology for the long-running roles, and the portability caveats stated honestly.

The goal is replication, not magic: the **dashboard + REST API + scheduler** and
the **brain heartbeat** run cleanly in a container with nothing but a `.env`. The
**voice pipeline** and the **opencode heartbeat provider** depend on the host and
are documented as opt-in below.

## Image design

Multi-stage `Dockerfile`:

1. **`dashboard-build`** (`node:22-slim`) — `npm ci` + `npm run build` produces
   the static React/Vite bundle in `dashboard/dist`.
2. **`runtime`** (`python:3.13-slim`) — installs `uv` (pinned to the lockfile's
   version), `uv sync --frozen --no-dev` (no dev/test deps in the runtime image),
   copies the source + the built `dashboard/dist`, runs as a **non-root** user
   (`lhassa`, uid 10001), and serves the API on port **7766**.

Key facts:

- **Data dir:** `HOME=/data`. Every default data path in `lhassa/config.py` and
  `main.py` derives from `Path.home()` (`~/.lhassa/brain.db`, `chica.db`,
  backups, Ed25519 keys, `org.json`, `plugins/`, logs, plus `~/.config/lhassa`).
  Pointing `HOME` at the mounted volume relocates **all** persistent state into
  `/data` — one clean override, no per-path env vars.
- **Port:** `EXPOSE 7766`; `LHASSA_API_HOST=0.0.0.0` inside the container so the
  published port is reachable. Compose publishes it to **`127.0.0.1` only** by
  default (the dashboard is unauthenticated and can drive privileged actions).
- **Healthcheck:** `GET /api/status` via stdlib `urllib` (no extra packages).
- **Lean:** `.dockerignore` excludes `.venv`, `.git`, `dashboard/node_modules`,
  the prior `dashboard/dist`, `__pycache__`, `.env`, `*.db`, logs and test caches.
- **No voice deps:** the image installs `uv sync --frozen --no-dev` — no
  `--extra voice` — so `pipecat`/`torch`/`pyaudio` are never resolved or
  installed. Every import site that touches them (`lhassa/voice/notifier.py`,
  the `tools/handlers/*.py` type hints, `lhassa/voice/pipeline.py`) is guarded
  (lazy import or `TYPE_CHECKING`), so `lhassa.api.server`, `lhassa.core.step`,
  `lhassa.notify.telegram_inbound` and `lhassa.org.watcher` — everything the
  container roles need — import cleanly with no voice extra installed. This is
  the fix for the size/cost blocker previously logged in `doc/portability.md`;
  the image no longer needs `libportaudio2`/`libgomp1`/`libsndfile1` either.

## Build & run

```bash
cd /path/to/lhassa
cp .env.example .env          # then fill in your keys (see "Secrets" below)

# Build the image
docker build -t lhassa:dev .

# Run the whole topology (api + heartbeat)
docker compose up -d
docker compose logs -f lhassa

# Dashboard:  http://127.0.0.1:7766
```

### The roles (one image, `LHASSA_RUN` selects)

| `LHASSA_RUN` | What it runs | Compose service |
|--------------|--------------|-----------------|
| `api` (default) | dashboard + REST API + scheduler (no voice) | `lhassa` |
| `heartbeat` | one brain beat, then loop every `LHASSA_HEARTBEAT_INTERVAL`s | `heartbeat` |
| `telegram` | Telegram inbound long-poller | `telegram` (profile) |

The entrypoint also runs any explicit command verbatim, e.g.:

```bash
docker run --rm lhassa:dev python -c "import lhassa, main; print('ok')"
```

### Heartbeat scheduling

Compose has no cron. Two honest options:

- **In-container loop (default):** the `heartbeat` service runs `scripts/lhassa-heartbeat
  --loop $LHASSA_HEARTBEAT_INTERVAL` (default 900 = 15 min). This is a resident
  process (`lhassa.core.heartbeat_loop`): it imports the brain once, then beats,
  sleeps, repeats in-process, instead of relaunching the interpreter every tick
  (the old shell loop paid the full ~400ms import cost on every wake for no
  reason). SIGTERM/SIGINT stop it cleanly after the tick in flight.
- **Host scheduler:** set `LHASSA_HEARTBEAT_INTERVAL=0` (run one beat and exit)
  and drive it from the host — cron or a systemd timer calling
  `docker compose run --rm heartbeat`. This replaces the repo's
  `scripts/systemd/lhassa-heartbeat.timer` when running in containers.

### Telegram poller (optional)

```bash
docker compose --profile telegram up -d telegram
```

Requires `LHASSA_TELEGRAM_BOT_TOKEN` + `LHASSA_TELEGRAM_CHAT_ID` in `.env`. With
no token it never touches the network (credential-gated, by design).

## Secrets / env

Everything is env-driven (`.env`, loaded by `load_dotenv()`); **no secret is ever
baked into the image** — they are injected at runtime via Compose `env_file`.
Copy `.env.example` to `.env` and set what you use:

- **LLM provider key** (one is enough; auto-detected): `GROQ_API_KEY` /
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` /
  `CEREBRAS_API_KEY`.
- **Voice (only if you enable it):** `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`.
  Note `Config.validate()` requires these two — it is called by `main.py` (the
  voice entrypoint), **not** by the container's `api`/`heartbeat` roles, so the
  API and heartbeat run without them. The container image doesn't even install
  the `voice` extra (`pipecat`/`torch`), so `main.py`/the voice pipeline simply
  isn't runnable inside this image — voice is a laptop-only surface, run via
  `make run`/`make dev` after `make install` (which does install the extra).
- **Telegram:** `LHASSA_TELEGRAM_BOT_TOKEN`, `LHASSA_TELEGRAM_CHAT_ID`.
- **Heartbeat via opencode:** `LHASSA_HEARTBEAT_PROVIDER=opencode`,
  `LHASSA_OPENCODE_BIN`, `LHASSA_OPENCODE_MODEL` (see the caveat below).
- **Tuning:** `LHASSA_API_PORT`, `LHASSA_INSTANCE_ID`, budgets, etc.

`.env` is gitignored and `.dockerignore`d — keep it that way.

## Portability caveats (honest)

1. **Voice needs host audio, and the extra isn't in this image.** The voice
   pipeline opens local audio devices via `pyaudio`/PipeWire/PulseAudio and
   needs the Deepgram (STT) and Cartesia (TTS) keys. It is **off in the
   container by default** (the `api` role never starts it) — and, since the
   image is built without `uv sync --extra voice`, `pipecat`/`torch`/`pyaudio`
   aren't even installed. To run voice in a container you'd need a separate
   image built with `RUN uv sync --frozen --no-dev --extra voice`, pass the
   host audio through (e.g. `--device /dev/snd` plus the PulseAudio/PipeWire
   socket — `-v /run/user/1000/pulse:/run/user/1000/pulse -e
   PULSE_SERVER=...`), or use `--network host`. This is host-specific and not
   wired into the default Compose file or Dockerfile. `[SUPUESTO]` the exact
   device/socket mapping varies per host; nobody has needed containerized voice
   yet, so this stays a documented path rather than a built image variant.

2. **The opencode heartbeat is a host CLI.** `LHASSA_HEARTBEAT_PROVIDER=opencode`
   shells out to the `opencode` binary using the **owner's local credentials** —
   neither is in the image. Two honest paths in-container:
   - **Mount the binary + creds:** bind-mount the `opencode` binary and its
     config/credentials into the container and set `LHASSA_OPENCODE_BIN` to the
     mounted path. Brittle (host-coupled), but keeps the free tier.
   - **Use a cloud provider via API key (recommended for portability):** set a
     normal provider key (e.g. `GROQ_API_KEY`) and leave the heartbeat on the
     gateway path. No host CLI needed; costs follow the provider. The deterministic
     beat itself is $0 (no LLM); only the *think* path spends.

3. **Secrets via env, never in the image.** Provided through Compose `env_file`
   at runtime. The image contains no `.env` (excluded by `.dockerignore`). Rotate
   keys by editing `.env` and `docker compose up -d` (recreates the containers).

4. **Data persistence via the volume.** All state lives under `/data` (the named
   `lhassa-data` volume) because `HOME=/data`. Back it up by snapshotting the
   volume, or use Lhassa's own backup (`~/.lhassa/backups`, also on the volume).
   Removing the volume wipes the brain; `docker compose down` (without `-v`)
   keeps it.

5. **systemd timers are replaced.** The host's `lhassa-heartbeat.timer` /
   `lhassa-telegram.service` user units are **not** used in containers — the
   Compose `heartbeat` and `telegram` services (or a host scheduler driving
   `docker compose run`) take their place.
