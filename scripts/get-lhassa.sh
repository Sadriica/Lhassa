#!/usr/bin/env bash
# Lhassa; one-command bootstrap ("get-lhassa").
#
# The Hermes-style on-ramp: no Docker, no prior Python knowledge, no manual
# cloning. Run this on a fresh Linux (or macOS) machine and it:
#   1. detects the platform (Linux; macOS best-effort; anything else: a clear
#      "not supported yet" message, no guessing),
#   2. installs `uv` if it is missing (the official astral.sh installer),
#      leaving an already-installed uv untouched,
#   3. clones the Lhassa repo (or updates it if it is already there),
#   4. resolves the Python venv with `uv sync` (headless by default, no
#      voice runtime; pass --voice to include it),
#   5. puts a `lhassa` command on PATH via a small shim in ~/.local/bin,
#   6. prints the real next steps (.env, a model backend, `lhassa --help`,
#      where the docs live); nothing this script cannot actually deliver.
#
# This is NOT the always-on installer; that's scripts/install.sh (systemd
# user units + Docker + boot lingering). get-lhassa.sh only gets `lhassa`
# itself onto the machine and runnable from the CLI. See doc/install.md for
# the always-on path and doc/getting-started.md for the full walkthrough.
#
# Idempotent: safe to re-run. Re-running updates the clone (git pull, or a
# fast-forward fetch) and re-syncs the venv instead of duplicating anything.
#
# Usage:
#   curl -fsSL https://lhassa.witara.site/get-lhassa.sh | bash
#   # or, without piping to a shell:
#   bash scripts/get-lhassa.sh [flags]
#
# Flags:
#   --voice         Also install every pack plus the voice pack `runtime` extra (pipecat/torch).
#                    Needed only for the local mic/speaker pipeline (main.py);
#                    the CLI, dashboard and API work fine without it.
#   -h, --help      Show this help and exit.
#
# Environment overrides:
#   LHASSA_DIR       Where to clone/update the repo (default: ~/.local/share/lhassa).
#   LHASSA_REPO_URL  Git URL to clone from (default: the public GitHub repo).
set -euo pipefail

# ── Defaults (overridable via environment) ────────────────────────────────────
LHASSA_DIR="${LHASSA_DIR:-$HOME/.local/share/lhassa}"
LHASSA_REPO_URL="${LHASSA_REPO_URL:-https://github.com/Sadriica/Lhassa.git}"
BIN_DIR="$HOME/.local/bin"
INCLUDE_VOICE=0

# ── Logging helpers (matches scripts/install.sh's voice) ──────────────────────
_c() { if [ -t 1 ]; then printf '\033[%sm' "$1"; fi; }
log()  { printf '%s==>%s %s\n' "$(_c '1;34')" "$(_c 0)" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$(_c '1;32')" "$(_c 0)" "$*"; }
warn() { printf '%swarn%s %s\n' "$(_c '1;33')" "$(_c 0)" "$*" >&2; }
err()  { printf '%s err%s %s\n' "$(_c '1;31')" "$(_c 0)" "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
    sed -n '2,34p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --voice)   INCLUDE_VOICE=1 ;;
        -h|--help) usage ;;
        *)         die "unknown flag: $1 (try --help)" ;;
    esac
    shift
done

# ── 1. Platform detection ─────────────────────────────────────────────────────
detect_platform() {
    log "Detecting platform"
    local os
    os="$(uname -s)"
    case "$os" in
        Linux)
            ok "platform: Linux"
            ;;
        Darwin)
            warn "platform: macOS; best-effort support (uv + git + the venv all"
            warn "  work the same way there); the always-on systemd path in"
            warn "  scripts/install.sh is Linux-only and will not apply"
            ;;
        *)
            die "unsupported platform: $os (Lhassa's one-command install only" \
                "targets Linux today, with best-effort macOS; see doc/install.md)"
            ;;
    esac
    if [ "$(id -u)" -eq 0 ]; then
        warn "running as root; this script installs everything under your user"
        warn "  home (uv, the clone, the shim); running as an unprivileged user"
        warn "  is recommended but not required"
    fi
}

# ── 2. uv ──────────────────────────────────────────────────────────────────────
install_uv() {
    log "Checking uv"
    if command -v uv >/dev/null 2>&1; then
        ok "uv already installed: $(uv --version 2>&1)"
        return
    fi
    log "uv not found; installing via the official astral.sh installer"
    command -v curl >/dev/null 2>&1 || die "curl not found; needed to install uv (install curl and re-run)"
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        die "uv installer failed; see https://docs.astral.sh/uv/ for manual install steps"
    fi
    # The official installer places uv in ~/.local/bin (or ~/.cargo/bin on
    # older versions) and updates shell rc files, but this process's PATH
    # will not see that until a new shell starts. Make it visible now too.
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command -v uv >/dev/null 2>&1; then
        ok "uv installed: $(uv --version 2>&1)"
    else
        die "uv installed but not found on PATH; open a new shell and re-run this script"
    fi
}

# ── 3. Clone or update the repo ────────────────────────────────────────────────
clone_or_update_repo() {
    log "Getting the Lhassa repo into $LHASSA_DIR"
    command -v git >/dev/null 2>&1 || die "git not found; install git and re-run (Lhassa is distributed as a git repo, no git means no clone)"

    if [ -d "$LHASSA_DIR/.git" ]; then
        ok "existing checkout found; updating"
        if ! git -C "$LHASSA_DIR" pull --ff-only; then
            warn "git pull --ff-only failed (local changes or diverged history?)"
            warn "  leaving $LHASSA_DIR as is; resolve manually with:"
            warn "    cd $LHASSA_DIR && git status"
            die "could not update the existing checkout"
        fi
        ok "updated: $(git -C "$LHASSA_DIR" rev-parse --short HEAD)"
    elif [ -d "$LHASSA_DIR" ] && [ -n "$(ls -A "$LHASSA_DIR" 2>/dev/null)" ]; then
        die "$LHASSA_DIR exists, is non-empty, and is not a git checkout; remove it" \
            "or set LHASSA_DIR to a different path and re-run"
    else
        mkdir -p "$(dirname "$LHASSA_DIR")"
        log "cloning $LHASSA_REPO_URL"
        git clone "$LHASSA_REPO_URL" "$LHASSA_DIR" || die "git clone failed"
        ok "cloned into $LHASSA_DIR"
    fi
}

# ── 4. uv sync ─────────────────────────────────────────────────────────────────
sync_project() {
    if [ "$INCLUDE_VOICE" -eq 1 ]; then
        log "Resolving the Python venv (uv sync --all-packages --extra runtime; voice pack runtime)"
        ( cd "$LHASSA_DIR" && uv sync --all-packages --extra runtime ) || die "uv sync --all-packages --extra runtime failed"
    else
        log "Resolving the Python venv (uv sync, headless; no voice runtime)"
        ( cd "$LHASSA_DIR" && uv sync ) || die "uv sync failed"
    fi
    ok "venv ready at $LHASSA_DIR/.venv"
}

# ── 5. Put `lhassa` on PATH ─────────────────────────────────────────────────────
install_shim() {
    log "Installing the lhassa command into $BIN_DIR"
    mkdir -p "$BIN_DIR"
    local venv_bin="$LHASSA_DIR/.venv/bin/lhassa"
    local shim="$BIN_DIR/lhassa"

    if [ ! -x "$venv_bin" ]; then
        die "expected $venv_bin after uv sync but it is missing" \
            "(check that pyproject.toml still defines a 'lhassa' entry point under [project.scripts])"
    fi

    cat > "$shim" <<EOF
#!/usr/bin/env bash
# Auto-generated by get-lhassa.sh; do not edit by hand, re-run get-lhassa.sh instead.
exec "$LHASSA_DIR/.venv/bin/lhassa" "\$@"
EOF
    chmod +x "$shim"
    ok "shim installed: $shim -> $venv_bin"

    case ":$PATH:" in
        *":$BIN_DIR:"*)
            ok "$BIN_DIR is already on PATH"
            ;;
        *)
            warn "$BIN_DIR is not on PATH in this shell"
            warn "  add it to your shell rc (e.g. ~/.bashrc or ~/.zshrc):"
            warn "    export PATH=\"$BIN_DIR:\$PATH\""
            warn "  then open a new shell, or run: export PATH=\"$BIN_DIR:\$PATH\""
            ;;
    esac
}

# ── 6. Honest closing ───────────────────────────────────────────────────────────
summary() {
    printf '\n'
    ok "Lhassa is installed at $LHASSA_DIR"
    printf '\n'
    log "Real next steps (this script does not do these for you):"
    echo "  1. Copy the env template and add ONE model backend key:"
    echo "       cd $LHASSA_DIR && cp .env.example .env"
    echo "     then set ONE of GROQ_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY /"
    echo "     OPENROUTER_API_KEY / CEREBRAS_API_KEY / DEEPSEEK_API_KEY in .env,"
    echo "     or point it at a local Ollama (see .env.example), or use a"
    echo "     subscription CLI you already have (claude / codex / opencode) —"
    echo "     those need no key at all."
    echo "  2. Check it: $BIN_DIR/lhassa --help"
    echo "     (this works with no model backend configured; commands that"
    echo "     actually need to think will fail until you add one)"
    echo "  3. Talk to it: lhassa \"hello\""
    echo "  4. Read the docs: $LHASSA_DIR/doc/getting-started.md"
    echo "     Want it always-on (systemd + Docker, Telegram, boot lingering)?"
    echo "     That is a separate, heavier step: bash $LHASSA_DIR/scripts/install.sh"
    printf '\n'
    if [ "$INCLUDE_VOICE" -eq 0 ]; then
        warn "voice runtime not installed (headless by default); re-run with --voice" \
             "if you want the local mic/speaker pipeline (main.py)"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    log "get-lhassa; the one-command bootstrap"
    detect_platform
    install_uv
    clone_or_update_repo
    sync_project
    install_shim
    summary
    log "Done."
}

main
