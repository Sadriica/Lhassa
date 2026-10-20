# Portability audit; laptop → VPS

This is the sweep behind the "runs unchanged on a headless VPS" goal: every
laptop-only coupling found in the codebase, its status, and the fixes applied
for the real gaps found; the monitor watcher had no container entry point,
and the voice dependencies (`pipecat`/`torch`) were unconditional top-level
deps that bloated every headless install. It complements `doc/deployment.md`
(the existing, detailed Docker/Compose reference); this doc is the audit
trail, that one is the how-to.

## Coupling table

| Coupling | File | Current behavior on a VPS (no DISPLAY, no audio, no desktop session) | Status | Notes |
|---|---|---|---|---|
| Desktop toast (`notify-send`) | `lhassa/notify/desktop.py` | `is_configured()` is `shutil.which("notify-send") is not None`; with the binary absent (any headless box) the channel is silently skipped by `active_channels()`: no error, no log spam. `send()` also re-checks the binary before calling `subprocess.run`, so even a direct caller gets a clean `False`. | ok-degrades | Already tested (`tests/test_notify_channels.py::test_desktop_is_configured_follows_which`, `test_desktop_send_noop_when_binary_missing`). No change needed. |
| Voice-pipeline desktop/TTS fallback | `lhassa/tools/notifier.py` (was `lhassa/voice/notifier.py`) | Only reachable from the voice pipeline, which the container never starts (`docker/run_api.py` boots API + scheduler only). `_notify_linux` catches `FileNotFoundError` and prints instead of raising. | ok-degrades / not-reached | Dead code path in the headless deploy; not touched (outside my edit scope and not broken). |
| Audio / voice dependencies (`pipecat-ai[cartesia,deepgram,groq,local,silero]`, `torch`, `pyaudio` via `portaudio19-dev`) | `pyproject.toml`, `Dockerfile` | **Fixed.** These now live behind a `voice` extra (`[project.optional-dependencies].voice` in `pyproject.toml`); the default `uv sync` / the Docker image never installs `torch`/`pipecat`/`pyaudio`. Every import site that touched them at module load was guarded: `lhassa/voice/notifier.py` (consumed by `lhassa/scheduler.py` and `lhassa/tools/monitor.py`, both on the headless API path) now imports `pipecat.frames.frames.TTSSpeakFrame` lazily inside `notify()`, degrading to desktop notify with a log line ("voice extra not installed") if it's missing; the ten `lhassa/tools/handlers/*.py` modules that imported `pipecat.services.llm_service.FunctionCallParams` only for a type hint now do it under `TYPE_CHECKING` (+ `from __future__ import annotations`), so they no longer require pipecat to import at all. `lhassa/voice/pipeline.py` and `lhassa/tools/schemas.py`/`registry.py` (which really do construct pipecat objects at runtime) still require the extra; but they're only ever reached from `main.py` (the voice entrypoint) or the built-in CLI tool-calling loop, never from the API/heartbeat/telegram/monitor roles. `docker build` no longer needs `build-essential`/`portaudio19-dev` (builder stage) or `libportaudio2`/`libgomp1`/`libsndfile1` (runtime stage). `make install`/`scripts/install.sh` (the laptop dev workflow, which runs `main.py`'s voice pipeline via `make run`/`make dev`) install with `--extra voice` (since 2026-10-20: the voice pack's `runtime` extra, `uv sync --all-packages --extra runtime`; the TTS branch is a speaker the pack registers on `lhassa/tools/notifier.py`); a throwaway venv with `uv pip install -e .` (no extra) can `import lhassa.api.server, lhassa.core.step, lhassa.notify.telegram_inbound, lhassa.org.watcher` cleanly. |
| Hardware health checks (battery/temp/sensors) | `lhassa/health/checks.py` | The current `CHECKS` registry is `disk`, `failed_units`, `git_status`, `mem_pressure` only; **no battery or thermal-sensor check exists in this codebase.** `check_disk` uses `shutil.disk_usage` (portable, no hardware assumption); `check_failed_units` guards `shutil.which("systemctl")`; `check_git_status` guards `shutil.which("git")`; `check_mem_pressure` reads `/proc/meminfo` inside a `try/except OSError` (present on any Linux, container included). | ok-degrades / not-applicable | Verified via `tests/test_health.py` (missing-binary and missing-file cases already covered). No hardware-only check exists to break, so no guard was added. |
| Privileged host commands (`sensors`, `tlp`, `power-mode`, `pacman`, `systemctl` via `sudo -n`) | `lhassa/tools/privileged.py` | Requires the sudoers drop-in from `scripts/install-privileged.sh`, never run in the container (non-root `lhassa` user, no `sudo` binary installed). `run_privileged()` catches `FileNotFoundError` ("I couldn't find `sudo`") and a failed `sudo -n` ("I don't have passwordless sudo... run install-privileged.sh"); both are clean, spoken-to-the-user degrades, never a crash. | ok-degrades | Arch-laptop-specific allowlist (`pacman`, `tlp`, `power-mode`) is meaningless on a VPS regardless; the tool self-reports rather than erroring. |
| CLI subscriptions (`claude`, `codex`, `opencode` in `~/.local/bin` / `~/.codex` / `~/.config/opencode`) | `harness/*`, `llm/router.py`, systemd `Environment=PATH=...` lines | Each CLI's auth is a local credential file (e.g. `~/.codex/auth.json`, an OAuth-style token; `opencode` similarly persists a token under its config dir) written by an **interactive, browser-based login** the CLI performs once. A container/VPS with no browser can't complete that login flow itself. | needs-work (research only) | Practical headless path: perform the interactive login **once** on a machine with a browser (the laptop, or `ssh -L` port-forwarding the CLI's local OAuth callback from the VPS to a local browser), then copy the resulting credential file onto the VPS/volume and bind-mount it (or copy into the `lhassa-data` volume and point `LHASSA_CLAUDE_BIN`/`LHASSA_OPENCODE_BIN`/config paths at it). This is exactly the "mount the binary + creds" caveat `doc/deployment.md` already documents for `opencode`; the same shape applies to `claude`/`codex`. The lower-friction alternative for a pure VPS deploy is to skip subscription CLIs entirely and set a cloud provider API key (`GROQ_API_KEY` etc.); the gateway path needs no interactive login. No code change; this is a documentation/ops note. |
| systemd **user**-session assumptions | `scripts/systemd/lhassa-*.service` | All four units are user units (`%h`, `WantedBy=default.target`/`timers.target`), three depend on `After=graphical-session.target` (a login-manager-started session; meaningless headless), and all four hardcode `PATH=%h/.local/bin:...` to find the subscription CLIs. None of this runs in the container; Compose replaces all four roles (`api`→`lhassa` service, `heartbeat`→`heartbeat` service, `telegram`→`telegram` profile, and now `monitor`→`monitor` profile). | fixed-here (via Compose) / not-applicable | The systemd units themselves are intentionally untouched (they're the laptop path, `doc/deployment.md` §"systemd timers are replaced" already states Compose supersedes them for containers). The gap this audit found: **`docker/entrypoint.sh` and `docker-compose.yml` had no `monitor` role**, so the always-on watcher (`org/watcher.py`, mirrored 1:1 by `lhassa-monitor.service` on the laptop) had no VPS equivalent. Fixed; see below. |
| Absolute paths | `lhassa/config.py`, `main.py` | Every default data path derives from `Path.home()` (`~/.lhassa/brain.db`, `chica.db`, backups, Ed25519 keys, `org.json`, `plugins/`, skills, flows). No hardcoded `/home/w` found anywhere under `lhassa/` (checked via grep). | ok-degrades | Already solved by `Dockerfile`'s `HOME=/data` + non-root `lhassa` user; one override relocates all state onto the named volume. Pre-existing, not part of this workstream's changes. |
| `~/.lhassa/settings.env` / `.env` layering | `lhassa/config.py`, `lhassa/settings.py` | Both are plain files under `HOME`; work identically in the container since `HOME=/data`. | ok-degrades | No coupling. |

## Fix applied; the monitor role had no container entry point

`docker/entrypoint.sh` dispatched `api` / `heartbeat` / `telegram` but not
`monitor`, and `docker-compose.yml` had no service for `org/watcher.py` (the
always-on $0 watcher that `lhassa-monitor.service` runs on the laptop). Added:

- `docker/entrypoint.sh`: a `monitor` case that `exec`s
  `python -m lhassa.org.watcher` (mirrors the `telegram` case's shape).
- `docker-compose.yml`: a `monitor` service, profile-gated
  (`docker compose --profile monitor up -d`) the same way `telegram` is;
  `LHASSA_MONITOR_ENABLED` is the real on/off switch (the module logs once and
  exits cleanly, same discipline as Telegram's credential gate), so the profile
  just controls whether the container exists at all.

No changes were needed in `lhassa/notify/channels.py` or `lhassa/health/checks.py`;
both already degrade cleanly headless, and both have existing test coverage for
exactly that (see table above). Nothing was added there per the
"only fix what's broken" discipline.

## Run it on a VPS today

```bash
cd /path/to/lhassa
cp .env.example .env      # fill in keys (see below)
docker build -t lhassa:dev .
docker compose up -d                          # api + heartbeat
docker compose --profile telegram up -d       # + Telegram inbound poller
docker compose --profile monitor up -d        # + the always-on monitor watcher
docker compose logs -f lhassa
```

Dashboard/API: `http://127.0.0.1:7766` (published loopback-only by default; the
dashboard is unauthenticated and can drive privileged actions; set
`LHASSA_API_TOKEN` and/or change the port publish line to expose it beyond
loopback, e.g. behind a reverse proxy with TLS).

Minimum env for a headless brain (from `lhassa/config.py`):

- **One LLM key** (auto-detected priority: `ANTHROPIC_API_KEY` →
  `OPENAI_API_KEY` → `GROQ_API_KEY` → `OPENROUTER_API_KEY` → `CEREBRAS_API_KEY`).
  `GROQ_API_KEY` has a free tier and needs no other setup.
- `LHASSA_TELEGRAM_BOT_TOKEN` + `LHASSA_TELEGRAM_CHAT_ID` + `LHASSA_TELEGRAM_POLL=1`
 ; only if running the `telegram` profile.
- `LHASSA_MONITOR_ENABLED=1`: only if running the `monitor` profile; optionally
  `LHASSA_MONITOR_NOTIFY`, `LHASSA_MONITOR_IDLE_INTERVAL` /
  `_ACTIVE_INTERVAL`, `LHASSA_MONITOR_THINK` + `LHASSA_ORG_LIVE` (only if you
  want the monitor to spend a token on a live reaction; off by default).
- `LHASSA_HEARTBEAT_INTERVAL`: in-container loop period in seconds (default 900;
  set `0` to run one beat and exit, driving the schedule from a host cron/systemd
  timer calling `docker compose run --rm heartbeat` instead).
- `LHASSA_API_PORT` / `LHASSA_API_HOST`: the container already sets
  `LHASSA_API_HOST=0.0.0.0` internally; only the Compose port-publish line
  controls host exposure.
- `LHASSA_INSTANCE_ID`: set a stable id per VPS if you ever run more than one
  brain instance.
- **Not needed headless:** `DEEPGRAM_API_KEY` / `CARTESIA_API_KEY` (voice-only;
  `Config.validate()` (which requires them) is only called by `main.py`, the
  voice entrypoint, never by `docker/run_api.py`). The voice pack runtime
  (`pipecat`/`torch`/`pyaudio`) isn't installed in the image either; see the
  coupling table above.
- **Subscription CLIs** (`claude`/`codex`/`opencode` as `LHASSA_HEARTBEAT_PROVIDER`
  or harness): not usable out of the box on a fresh VPS (see the CLI-auth row
  above); set a cloud provider key instead unless you've bind-mounted a
  pre-authenticated credential file.

See `doc/deployment.md` for the full image design, the voice/opencode caveats,
and the secrets/backup discipline.
