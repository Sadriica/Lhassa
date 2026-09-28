# Install; always-on Linux setup

This is the native install path that turns Lhassa into a real, always-on Linux
tool: the dashboard + REST API + scheduler run continuously, the brain heartbeat
ticks on a timer, and everything comes back on boot. For the containerized story
(image design, the Compose roles, portability caveats) see
[`deployment.md`](deployment.md).

## System dependencies

Install these before running the installer:

| Dependency | Why | Required? |
|------------|-----|-----------|
| [`uv`](https://docs.astral.sh/uv/) | Python toolchain; `uv sync` resolves the `.venv`. | Yes |
| `node` + `npm` | Build the React/Vite dashboard (`npm ci && npm run build`). | Yes (for the dashboard) |
| `docker` | The containerized path + the installer's build/smoke test. | Only without `--no-docker` |
| `tectonic` | LaTeX engine that compiles a tailored CV `.tex` into a PDF. Without it the CV bridge sends the raw `.tex`. | Optional |
| `ccusage` | Cross-harness LLM-spend CLI for the dashboard cost panels. Install user-level: `npm install -g --prefix ~/.local ccusage`. Without it those panels show "unavailable" (the read is run `--offline` + budget-capped so it never hangs). | Optional |

The subscription CLIs (`claude` / `codex` / `opencode`) are optional and need **no
API key**. The preflight checks `python3`/`uv`/`docker` and fails early if missing.

### Per-distro bootstrap (Arch / Debian / Ubuntu / Fedora / openSUSE)

On a fresh host, one command installs the system prerequisites for whichever
distro it finds (pacman / apt / dnf / zypper):

```bash
sudo bash scripts/bootstrap.sh   # git, curl, python3, nodejs+npm; idempotent
```

It then prints the user-level next steps (uv, `install.sh`, optional ollama and
privileged). The privileged installer is distro-aware too:
`scripts/install-privileged.sh` builds the sudoers line PER HOST; universal
admin tools + whichever package manager the distro ships + laptop/power tools
only when actually installed (a server simply gets a shorter, validated line);
and `lhassa/tools/privileged.py` mirrors the same host-filtered set at runtime.

## Quickstart

```bash
cd ~/Code/lhassa
bash scripts/install.sh
```

That single command:

1. **Preflight**: checks `python3`, `uv`, and `docker`, prints versions, and
   fails early with a helpful message if anything is missing.
2. **Sync**: runs `uv sync` to resolve the `.venv`, and builds the dashboard
   (`npm ci && npm run build`) if `dashboard/dist` is missing.
3. **Docker build + smoke test**: `docker build -t lhassa:latest .`, then two
   smoke checks: `import lhassa, main` inside the image, and starting the API to
   confirm `GET /api/status` returns 200. Reports PASS/FAIL. Skip with
   `--no-docker`.
4. **Install services**: copies the systemd USER units into
   `~/.config/systemd/user/`, `daemon-reload`, and `enable --now` the app +
   heartbeat timer + health timer + the always-on `$0` monitor watcher (and the
   Telegram poller only if a bot token is set in `.env`).
5. **Run on boot**: `loginctl enable-linger "$USER"` so the user services start
   at boot even before you log in. If that needs elevated rights it prints the
   exact `sudo loginctl enable-linger $USER` to run.
6. **Summary**: prints unit status, the dashboard URL, and how to read logs.

When it finishes the dashboard is live at **http://127.0.0.1:7766**.

### Flags

| Flag | Effect |
|------|--------|
| `--no-docker` | Skip the Docker build + smoke test (still installs services). |
| `--docker-only` | Only build + smoke-test the image; install nothing. |
| `--no-boot` | Install + enable services but do not enable boot lingering. |
| `--image NAME` | Image tag to build (default `lhassa:latest`). |
| `-h`, `--help` | Show help. |

The script is idempotent; re-running it re-syncs, re-copies the units, and
re-enables them safely.

## The systemd services

All units are **USER** units (under `systemctl --user`, never system-wide):

| Unit | Type | What it does |
|------|------|--------------|
| `lhassa-app.service` | simple (foreground) | Dashboard + REST API + scheduler on `127.0.0.1:7766`. Reuses `docker/run_api.py`, so it needs no audio devices and no Deepgram/Cartesia keys. `Restart=on-failure`. |
| `lhassa-heartbeat.timer` → `.service` | oneshot timer | One brain `step()` tick shortly after boot, then every 2 min. Routine beats are deterministic and cost $0. |
| `lhassa-health.timer` → `.service` | oneshot timer | LLM-free PC-state health checks shortly after boot, then every 10 min. No tokens. |
| `lhassa-monitor.service` | simple | The always-on `$0` "ear": a deterministic watch loop over episodes/health/flows/approvals that only alerts on notable events. Ships `LHASSA_MONITOR_ENABLED=1`; idle ticks cost zero tokens (a token is spent only under `LHASSA_MONITOR_THINK=1` + `LHASSA_ORG_LIVE=1`). |
| `lhassa-telegram.service` | simple | Telegram inbound poller. Enabled only when `LHASSA_TELEGRAM_BOT_TOKEN` is set in `.env`; otherwise installed-but-disabled. |

Individual units can also be installed via `make`: `make install-heartbeat`,
`make install-health`, `make install-telegram`, `make install-monitor`,
`make install-ollama`.

The voice pipeline (`make run` / `main.py`) is **not** run as a service; it
needs host audio and the Deepgram/Cartesia keys. The always-on path is API-only
by design.

### Ollama (optional, real semantic memory)

Layer 4 (semantic fact retrieval) and document perception both work $0 and
offline out of the box via the deterministic `HashingEmbedder`. Installing
Ollama upgrades them to REAL embeddings without changing anything else about
how memory is used:

```bash
# 1. Install Ollama itself (see https://ollama.com for your distro's method)

# 2. Install + enable the systemd user unit that runs the Ollama server
make install-ollama

# 3. Pull the embedding model
ollama pull nomic-embed-text

# 4. Re-embed everything already stored under the offline hashing embedder
lhassa memory reindex
```

`make install-ollama` copies `scripts/systemd/ollama.service` into
`~/.config/systemd/user/` and enables it; same USER-unit pattern as every
other Lhassa service. `LHASSA_EMBEDDER_BACKEND=auto` (the default) then
detects the running daemon automatically; no other config is needed unless
you want to force it (`LHASSA_EMBEDDER_BACKEND=ollama`) or pin a different
model (`LHASSA_EMBEDDER_MODEL`). If Ollama is ever stopped or unreachable,
every embedder call degrades silently back to the offline hashing embedder;
nothing breaks, it just stops being semantic.

### Operational seeds

Some behaviors are explicit seeds so a clean env can recreate the same
departments and work-flows:

```bash
lhassa seed list       # list the registered seeds
lhassa seed apply cv   # recreates the interns department + the CV work-flow
lhassa seed apply personal # creates the personal department + morning/evening flows
```

The `personal` seed creates the stable personal secretary department
(`personal`) plus two different execution flows:
`personal-morning-briefing` and `personal-evening-briefing`.

For the CV seed, drop your real CV at `~/.lhassa/cv/cv_largo.tex` (or point
`LHASSA_CV_DIR` elsewhere). Install the optional `tectonic` system dep to have
the bridge reply with a compiled PDF; without it, it sends the raw `.tex`.

### Boot persistence

User services normally stop when your session ends. `loginctl enable-linger`
keeps them running at boot before login. The installer attempts this; if polkit
denies it, run once as the operator:

```bash
sudo loginctl enable-linger $USER
```

## Check status / logs

```bash
systemctl --user status lhassa-app
systemctl --user status lhassa-heartbeat.timer
systemctl --user list-timers 'lhassa*'

journalctl --user -u lhassa-app -f          # live API/dashboard logs
journalctl --user -u lhassa-heartbeat -f    # heartbeat beats
```

Application logs also land in `~/.lhassa/lhassa.log`.

## Update

```bash
cd ~/Code/lhassa
git pull
bash scripts/install.sh
```

Re-running picks up new code, rebuilds the image and dashboard if needed, and
restarts the services (the copied units are refreshed and re-enabled).

## Uninstall

```bash
bash scripts/uninstall.sh                 # stop + disable + remove the units
bash scripts/uninstall.sh --purge         # also remove the lhassa:latest image
bash scripts/uninstall.sh --purge-data    # also delete ~/.lhassa (DESTRUCTIVE)
```

By default the uninstaller touches only the systemd units; your brain data in
`~/.lhassa` and the Docker image/volume are left intact. `--purge` removes the
image and, after an extra confirmation, the named data volume. `--no-linger`
also disables boot lingering.

## See also

- [`deployment.md`](deployment.md); Docker image design, the Compose roles
  (`api` / `heartbeat` / `telegram`), secrets, and the portability caveats
  (voice needs host audio, the opencode heartbeat is a host CLI).

## One-command install

For a fresh machine with no clone, no Docker and no prior Python setup, there
is a single-command bootstrap:

```bash
curl -fsSL https://lhassa.witara.site/get-lhassa.sh | bash
# or, until that host is live, straight from GitHub:
curl -fsSL https://raw.githubusercontent.com/Sadriica/Lhassa/main/scripts/get-lhassa.sh | bash
```

What it does: detects the platform (Linux; macOS best-effort; anything else
is a clear "not supported yet" instead of a guess), installs `uv` if missing
(leaving an existing install alone), clones the repo into
`~/.local/share/lhassa` (override with `LHASSA_DIR`, or the source with
`LHASSA_REPO_URL`) or updates it if already cloned, runs `uv sync` headless
(no `voice` extra; pass `--voice` to include it), and drops a `lhassa` shim
into `~/.local/bin` so the command is on `PATH`. It is idempotent: re-running
it updates the clone and re-syncs instead of duplicating anything.

What it does **not** do: it does not install Docker, does not set up systemd
units, does not enable boot lingering, and does not configure a model
backend. It only gets `lhassa` itself onto the machine and runnable. For the
always-on path (systemd + Docker + boot persistence), run
`bash scripts/install.sh` from the clone afterward, as described above. For
the two-minute talk-to-it path from an existing clone, see `make quickstart`
in the main [`README.md`](../README.md#quickstart).

After it finishes, `lhassa --help` works with no model backend configured;
commands that actually need to think (e.g. `lhassa "hello"`) need `.env` set
up first; copy `.env.example` to `.env` in the clone and set one API key, or
point it at Ollama or a subscription CLI as described in
[`getting-started.md`](getting-started.md).
