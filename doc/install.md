# Install — always-on Linux setup

This is the native install path that turns Lhassa into a real, always-on Linux
tool: the dashboard + REST API + scheduler run continuously, the brain heartbeat
ticks on a timer, and everything comes back on boot. For the containerized story
(image design, the Compose roles, portability caveats) see
[`deployment.md`](deployment.md).

## Quickstart

```bash
cd ~/Code/lhassa
bash scripts/install.sh
```

That single command:

1. **Preflight** — checks `python3`, `uv`, and `docker`, prints versions, and
   fails early with a helpful message if anything is missing.
2. **Sync** — runs `uv sync` to resolve the `.venv`, and builds the dashboard
   (`npm ci && npm run build`) if `dashboard/dist` is missing.
3. **Docker build + smoke test** — `docker build -t lhassa:latest .`, then two
   smoke checks: `import lhassa, main` inside the image, and starting the API to
   confirm `GET /api/status` returns 200. Reports PASS/FAIL. Skip with
   `--no-docker`.
4. **Install services** — copies the systemd USER units into
   `~/.config/systemd/user/`, `daemon-reload`, and `enable --now` the app +
   heartbeat timer (and the Telegram poller only if a bot token is set in
   `.env`).
5. **Run on boot** — `loginctl enable-linger "$USER"` so the user services start
   at boot even before you log in. If that needs elevated rights it prints the
   exact `sudo loginctl enable-linger $USER` to run.
6. **Summary** — prints unit status, the dashboard URL, and how to read logs.

When it finishes the dashboard is live at **http://127.0.0.1:7766**.

### Flags

| Flag | Effect |
|------|--------|
| `--no-docker` | Skip the Docker build + smoke test (still installs services). |
| `--docker-only` | Only build + smoke-test the image; install nothing. |
| `--no-boot` | Install + enable services but do not enable boot lingering. |
| `--image NAME` | Image tag to build (default `lhassa:latest`). |
| `-h`, `--help` | Show help. |

The script is idempotent — re-running it re-syncs, re-copies the units, and
re-enables them safely.

## The systemd services

All units are **USER** units (under `systemctl --user`, never system-wide):

| Unit | Type | What it does |
|------|------|--------------|
| `lhassa-app.service` | simple (foreground) | Dashboard + REST API + scheduler on `127.0.0.1:7766`. Reuses `docker/run_api.py`, so it needs no audio devices and no Deepgram/Cartesia keys. `Restart=on-failure`. |
| `lhassa-heartbeat.timer` → `.service` | oneshot timer | One brain `step()` tick shortly after boot, then every 2 min. Routine beats are deterministic and cost $0. |
| `lhassa-telegram.service` | simple | Telegram inbound poller. Enabled only when `LHASSA_TELEGRAM_BOT_TOKEN` is set in `.env`; otherwise installed-but-disabled. |

The voice pipeline (`make run` / `main.py`) is **not** run as a service — it
needs host audio and the Deepgram/Cartesia keys. The always-on path is API-only
by design.

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

By default the uninstaller touches only the systemd units — your brain data in
`~/.lhassa` and the Docker image/volume are left intact. `--purge` removes the
image and, after an extra confirmation, the named data volume. `--no-linger`
also disables boot lingering.

## See also

- [`deployment.md`](deployment.md) — Docker image design, the Compose roles
  (`api` / `heartbeat` / `telegram`), secrets, and the portability caveats
  (voice needs host audio, the opencode heartbeat is a host CLI).
