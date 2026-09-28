#!/usr/bin/env bash
# Lhassa; uninstall the always-on systemd USER services.
#
# Stops + disables the units, removes the unit files, and reloads the user
# manager. By default it touches ONLY the systemd units; your brain data in
# ~/.lhassa and the Docker image/volume are left intact.
#
# Usage:
#   bash scripts/uninstall.sh [flags]
#
# Flags:
#   --purge         Also remove the lhassa:latest Docker image. With an extra
#                   interactive confirm, also remove the named data volume.
#   --purge-data    Also delete the local brain data dir (~/.lhassa). DESTRUCTIVE.
#   --image NAME    Image tag to remove with --purge (default: lhassa:latest).
#   --no-linger     Also disable user lingering (loginctl disable-linger).
#   -h, --help      Show this help and exit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
IMAGE="lhassa:latest"
DATA_DIR="$HOME/.lhassa"
DATA_VOLUME="lhassa-data"

UNITS=(
    "lhassa-app.service"
    "lhassa-heartbeat.timer"
    "lhassa-heartbeat.service"
    "lhassa-telegram.service"
)

DO_PURGE=0
DO_PURGE_DATA=0
DO_LINGER_OFF=0

_c() { if [ -t 1 ]; then printf '\033[%sm' "$1"; fi; }
log()  { printf '%s==>%s %s\n' "$(_c '1;34')" "$(_c 0)" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$(_c '1;32')" "$(_c 0)" "$*"; }
warn() { printf '%swarn%s %s\n' "$(_c '1;33')" "$(_c 0)" "$*" >&2; }
err()  { printf '%s err%s %s\n' "$(_c '1;31')" "$(_c 0)" "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
    sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --purge)      DO_PURGE=1 ;;
        --purge-data) DO_PURGE_DATA=1 ;;
        --no-linger)  DO_LINGER_OFF=1 ;;
        --image)      shift; [ "$#" -gt 0 ] || die "--image needs a value"; IMAGE="$1" ;;
        -h|--help)    usage ;;
        *)            die "unknown flag: $1 (try --help)" ;;
    esac
    shift
done

# ── Stop + disable + remove unit files ────────────────────────────────────────
remove_units() {
    log "Removing systemd USER units"
    command -v systemctl >/dev/null 2>&1 || die "systemctl not found; nothing to remove"
    local u
    for u in "${UNITS[@]}"; do
        # disable --now also stops the unit; ignore failures (not installed).
        systemctl --user disable --now "$u" >/dev/null 2>&1 || true
        if [ -f "$SYSTEMD_DST/$u" ]; then
            rm -f "$SYSTEMD_DST/$u"
            ok "removed $u"
        else
            warn "not installed: $u"
        fi
    done
    systemctl --user daemon-reload
    systemctl --user reset-failed >/dev/null 2>&1 || true
    ok "daemon-reload"
}

# ── Optional: disable lingering ───────────────────────────────────────────────
disable_linger() {
    [ "$DO_LINGER_OFF" -eq 1 ] || return 0
    log "Disabling user lingering"
    if loginctl disable-linger "$USER" >/dev/null 2>&1; then
        ok "lingering disabled for $USER"
    else
        warn "could not disable lingering (needs sudo/polkit): sudo loginctl disable-linger $USER"
    fi
}

# ── Optional: purge image + volume ────────────────────────────────────────────
purge_docker() {
    [ "$DO_PURGE" -eq 1 ] || return 0
    if ! command -v docker >/dev/null 2>&1; then
        warn "docker not found; skipping image/volume purge"
        return 0
    fi
    log "Purge; removing Docker image $IMAGE"
    if docker image inspect "$IMAGE" >/dev/null 2>&1; then
        docker image rm "$IMAGE" >/dev/null 2>&1 && ok "removed image $IMAGE" \
            || warn "could not remove image $IMAGE (in use?)"
    else
        warn "image not present: $IMAGE"
    fi

    # The data volume holds the brain; extra explicit confirmation required.
    if docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1; then
        printf 'Also DELETE the Docker data volume "%s" (wipes the brain)? [y/N] ' "$DATA_VOLUME"
        local reply=""
        read -r reply || true
        if [ "$reply" = "y" ] || [ "$reply" = "Y" ]; then
            docker volume rm "$DATA_VOLUME" >/dev/null 2>&1 && ok "removed volume $DATA_VOLUME" \
                || warn "could not remove volume $DATA_VOLUME (still in use?)"
        else
            ok "kept data volume $DATA_VOLUME"
        fi
    fi
}

# ── Optional: purge local data dir ────────────────────────────────────────────
purge_data() {
    [ "$DO_PURGE_DATA" -eq 1 ] || return 0
    log "Purge; deleting local brain data $DATA_DIR"
    if [ -d "$DATA_DIR" ]; then
        printf 'PERMANENTLY delete %s (DBs, keys, backups)? [y/N] ' "$DATA_DIR"
        local reply=""
        read -r reply || true
        if [ "$reply" = "y" ] || [ "$reply" = "Y" ]; then
            rm -rf "$DATA_DIR"
            ok "deleted $DATA_DIR"
        else
            ok "kept $DATA_DIR"
        fi
    else
        warn "no data dir at $DATA_DIR"
    fi
}

main() {
    log "Lhassa uninstaller"
    remove_units
    disable_linger
    purge_docker
    purge_data
    ok "Done. Brain data in $DATA_DIR is kept unless --purge-data was given."
}

main
