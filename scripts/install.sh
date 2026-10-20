#!/usr/bin/env bash
# Lhassa. Linux install / replication script.
#
# One command makes Lhassa an always-on Linux tool:
#   - resolves the Python venv and (if needed) builds the dashboard,
#   - builds the Docker image and smoke-tests it,
#   - installs the systemd USER units (app + heartbeat + health + monitor + telegram),
#   - enables them now AND on boot (via user lingering).
#
# It is idempotent: re-running it is safe (re-sync, re-copy units, re-enable).
# Linux-first; the systemd/linger steps assume a systemd user session.
#
# Usage:
#   bash scripts/install.sh [flags]
#
# Flags:
#   --no-docker     Skip the Docker build + smoke test.
#   --docker-only   Only build + smoke-test the Docker image; install nothing.
#   --no-boot       Install + enable services, but do NOT enable boot linger.
#   --image NAME    Docker image tag to build (default: lhassa:latest).
#   -h, --help      Show this help and exit.
#
# See doc/install.md for the walkthrough and doc/deployment.md for Docker details.
set -euo pipefail

# ── Resolve paths from this script's location (repo root = scripts/..) ────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_SRC="$SCRIPT_DIR/systemd"
SYSTEMD_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
IMAGE="lhassa:latest"
DASHBOARD_URL="http://127.0.0.1:7766"

# The USER units this installer manages (long-running + scheduled brain).
APP_UNIT="lhassa-app.service"
HEARTBEAT_SERVICE="lhassa-heartbeat.service"
HEARTBEAT_TIMER="lhassa-heartbeat.timer"
TELEGRAM_UNIT="lhassa-telegram.service"
HEALTH_SERVICE="lhassa-health.service"
HEALTH_TIMER="lhassa-health.timer"
MONITOR_UNIT="lhassa-monitor.service"
OLLAMA_UNIT="ollama.service"

# ── Flags ─────────────────────────────────────────────────────────────────────
DO_DOCKER=1
DO_INSTALL=1
DO_BOOT=1

# ── Logging helpers ─────────────────────────────────────────────────────────
_c() { if [ -t 1 ]; then printf '\033[%sm' "$1"; fi; }
log()  { printf '%s==>%s %s\n' "$(_c '1;34')" "$(_c 0)" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$(_c '1;32')" "$(_c 0)" "$*"; }
warn() { printf '%swarn%s %s\n' "$(_c '1;33')" "$(_c 0)" "$*" >&2; }
err()  { printf '%s err%s %s\n' "$(_c '1;31')" "$(_c 0)" "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
    sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-docker)   DO_DOCKER=0 ;;
        --docker-only) DO_INSTALL=0; DO_BOOT=0 ;;
        --no-boot)     DO_BOOT=0 ;;
        --image)       shift; [ "$#" -gt 0 ] || die "--image needs a value"; IMAGE="$1" ;;
        -h|--help)     usage ;;
        *)             die "unknown flag: $1 (try --help)" ;;
    esac
    shift
done

# ── Preflight ─────────────────────────────────────────────────────────────────
preflight() {
    log "Preflight; checking tools and the repo"
    [ -f "$REPO_ROOT/main.py" ] || die "repo not found at $REPO_ROOT (run from the lhassa checkout)"
    ok "repo: $REPO_ROOT"

    local missing=0
    if command -v python3 >/dev/null 2>&1; then ok "python3: $(python3 --version 2>&1)"; else err "python3 not found"; missing=1; fi
    if command -v uv >/dev/null 2>&1; then ok "uv: $(uv --version 2>&1)"; else err "uv not found; https://docs.astral.sh/uv/"; missing=1; fi
    if [ "$DO_DOCKER" -eq 1 ]; then
        if command -v docker >/dev/null 2>&1; then ok "docker: $(docker --version 2>&1)"; else err "docker not found (or use --no-docker)"; missing=1; fi
    fi
    [ "$missing" -eq 0 ] || die "missing prerequisites; run 'sudo bash scripts/bootstrap.sh' (per-distro: apt/pacman/dnf/zypper) and re-run"
}

# ── Sync: venv + dashboard ────────────────────────────────────────────────────
sync_project() {
    log "Sync; resolving the Python venv (uv sync --all-packages --extra runtime)"
    # This script sets up the laptop dev/voice workflow (main.py, the voice
    # pipeline), so it installs every pack plus the voice pack's `runtime`
    # extra (pipecat/torch). Headless deploys (Docker/VPS) intentionally skip
    # it; see doc/portability.md.
    ( cd "$REPO_ROOT" && uv sync --all-packages --extra runtime )
    ok "venv ready at $REPO_ROOT/.venv"

    if [ -d "$REPO_ROOT/dashboard/dist" ] && [ -n "$(ls -A "$REPO_ROOT/dashboard/dist" 2>/dev/null)" ]; then
        ok "dashboard already built (dashboard/dist present)"
    else
        log "Sync; building the dashboard (npm ci && npm run build)"
        command -v npm >/dev/null 2>&1 || die "npm not found; needed to build dashboard/dist"
        ( cd "$REPO_ROOT/dashboard" && npm ci && npm run build )
        ok "dashboard built"
    fi
}

# ── Docker build + smoke test ─────────────────────────────────────────────────
docker_build_test() {
    log "Docker; building image $IMAGE"
    ( cd "$REPO_ROOT" && docker build -t "$IMAGE" . )
    ok "image built: $IMAGE"

    log "Docker; smoke test: import lhassa + main"
    if docker run --rm "$IMAGE" python -c "import lhassa, main; print('import ok')"; then
        ok "smoke: import PASS"
    else
        die "smoke: import FAIL"
    fi

    log "Docker; smoke test: start the API and curl the healthcheck"
    local cid
    cid="$(docker run -d --rm -p 127.0.0.1:7766:7766 "$IMAGE")" \
        || die "smoke: could not start API container"
    local passed=0 i
    for i in $(seq 1 30); do
        if docker exec "$cid" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7766/api/status',timeout=2).status==200 else 1)" >/dev/null 2>&1; then
            passed=1; break
        fi
        sleep 1
    done
    docker stop "$cid" >/dev/null 2>&1 || true
    if [ "$passed" -eq 1 ]; then
        ok "smoke: healthcheck PASS (/api/status 200)"
    else
        die "smoke: healthcheck FAIL (API did not become healthy)"
    fi
}

# ── Install systemd USER services ─────────────────────────────────────────────
telegram_configured() {
    # True when a non-empty bot token is set in the repo .env.
    local env_file="$REPO_ROOT/.env"
    [ -f "$env_file" ] || return 1
    grep -Eq '^[[:space:]]*LHASSA_TELEGRAM_BOT_TOKEN[[:space:]]*=[[:space:]]*[^[:space:]]+' "$env_file"
}

install_services() {
    log "Services; installing systemd USER units into $SYSTEMD_DST"
    command -v systemctl >/dev/null 2>&1 || die "systemctl not found; this step needs a systemd user session"
    mkdir -p "$SYSTEMD_DST"

    local units=("$APP_UNIT" "$HEARTBEAT_SERVICE" "$HEARTBEAT_TIMER" "$TELEGRAM_UNIT" \
                 "$HEALTH_SERVICE" "$HEALTH_TIMER" "$MONITOR_UNIT" "$OLLAMA_UNIT")
    local u
    for u in "${units[@]}"; do
        if [ -f "$SYSTEMD_SRC/$u" ]; then
            # The repo units assume the reference checkout path (%h/Code/lhassa);
            # template in THIS clone's real location so any checkout path works.
            sed "s|%h/Code/lhassa|$REPO_ROOT|g" "$SYSTEMD_SRC/$u" > "$SYSTEMD_DST/$u"
            ok "installed $u (repo path → $REPO_ROOT)"
        else
            warn "unit not found in repo, skipping: $u"
        fi
    done

    systemctl --user daemon-reload
    ok "daemon-reload"

    log "Services; enabling the always-on app + heartbeat timer + health timer + monitor"
    systemctl --user enable --now "$APP_UNIT"
    ok "enabled --now $APP_UNIT"
    systemctl --user enable --now "$HEARTBEAT_TIMER"
    ok "enabled --now $HEARTBEAT_TIMER"
    systemctl --user enable --now "$HEALTH_TIMER"
    ok "enabled --now $HEALTH_TIMER"
    # The monitor unit ships LHASSA_MONITOR_ENABLED=1, so enabling it turns the
    # always-on $0 "ear" on. Idle ticks cost zero tokens.
    systemctl --user enable --now "$MONITOR_UNIT"
    ok "enabled --now $MONITOR_UNIT"

    # Real semantic embeddings (optional): only when the ollama binary exists.
    # Best-effort; a host without Ollama still installs fine (memory degrades
    # to the offline hashing embedder by design).
    if command -v ollama >/dev/null 2>&1; then
        log "Services; ollama binary detected, enabling the local model server"
        systemctl --user enable --now "$OLLAMA_UNIT" && ok "enabled --now $OLLAMA_UNIT" \
            || warn "could not enable $OLLAMA_UNIT (continuing)"
        echo "  → pull the embedding model once: ollama pull nomic-embed-text"
        echo "  → then re-embed stored memory:   uv run lhassa memory reindex"
    else
        warn "ollama not installed; semantic memory will use the offline hashing embedder"
    fi

    if telegram_configured; then
        log "Services. Telegram token detected, enabling the poller"
        systemctl --user enable --now "$TELEGRAM_UNIT"
        ok "enabled --now $TELEGRAM_UNIT"
    else
        warn "no LHASSA_TELEGRAM_BOT_TOKEN in .env; leaving $TELEGRAM_UNIT installed but disabled"
        warn "  enable later with: systemctl --user enable --now $TELEGRAM_UNIT"
    fi
}

# ── Run on boot (user lingering) ──────────────────────────────────────────────
enable_boot() {
    log "Boot; enabling user lingering so services run at boot before login"
    if loginctl enable-linger "$USER" >/dev/null 2>&1; then
        ok "lingering enabled for $USER"
    else
        warn "could not enable lingering automatically (needs sudo/polkit)"
        warn "  run this once as the operator:"
        warn "    sudo loginctl enable-linger $USER"
    fi
}

# ── Summary ───────────────────────────────────────────────────────────────────
summary() {
    log "Summary; what is running"
    local u
    for u in "$APP_UNIT" "$HEARTBEAT_TIMER" "$HEALTH_TIMER" "$MONITOR_UNIT" "$TELEGRAM_UNIT"; do
        if systemctl --user list-unit-files "$u" >/dev/null 2>&1 && \
           systemctl --user cat "$u" >/dev/null 2>&1; then
            printf '\n'
            systemctl --user --no-pager --lines=0 status "$u" 2>/dev/null \
                | sed -n '1,4p' || true
        fi
    done
    printf '\n'
    ok "Dashboard:  $DASHBOARD_URL"
    ok "Logs:       journalctl --user -u lhassa-app -f"
    ok "            journalctl --user -u lhassa-heartbeat -f"
    ok "            journalctl --user -u lhassa-monitor -f"
    ok "Status:     systemctl --user status lhassa-app"
    ok "Uninstall:  bash scripts/uninstall.sh"
    printf '\n'
    warn "Optional system dep: 'tectonic' (LaTeX engine); install it so the CV"
    warn "  bridge compiles the tailored CV to PDF; without it, it sends raw .tex."
    warn "  Bootstrap the CV pipeline on a fresh env with: lhassa seed apply cv"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    log "Lhassa installer; repo: $REPO_ROOT"
    preflight

    if [ "$DO_INSTALL" -eq 1 ]; then
        sync_project
    fi

    if [ "$DO_DOCKER" -eq 1 ]; then
        docker_build_test
    else
        warn "skipping Docker build + smoke test (--no-docker)"
    fi

    if [ "$DO_INSTALL" -eq 0 ]; then
        ok "docker-only: done (no services installed)"
        exit 0
    fi

    install_services

    if [ "$DO_BOOT" -eq 1 ]; then
        enable_boot
    else
        warn "skipping boot linger (--no-boot); services stop when the user session ends"
    fi

    summary
    log "Done."
}

main
