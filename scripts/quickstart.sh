#!/usr/bin/env bash
# Lhassa; quickstart: talking to it in about two minutes.
#
# This is the LIGHT on-ramp for a curious stranger cloning the repo; it is
# NOT the always-on installer (that's scripts/install.sh: systemd + Docker).
# Quickstart only:
#   - resolves the Python venv (uv sync, no dashboard build, no voice runtime),
#   - makes sure a .env exists,
#   - detects whether ANY model backend is configured (an API key, or a local
#     model via Ollama), and if not, explains the two easiest paths and stops,
#   - if a backend IS configured, runs a quick sanity check and prints the
#     next two commands to run; it does not launch an interactive TUI itself.
#
# It is idempotent: safe to re-run after adding a key or installing Ollama.
#
# Usage:
#   bash scripts/quickstart.sh
set -euo pipefail

# ── Resolve paths from this script's location (repo root = scripts/..) ────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
ENV_EXAMPLE="$REPO_ROOT/.env.example"

# ── Logging helpers (matches scripts/install.sh's voice) ──────────────────────
_c() { if [ -t 1 ]; then printf '\033[%sm' "$1"; fi; }
log()  { printf '%s==>%s %s\n' "$(_c '1;34')" "$(_c 0)" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$(_c '1;32')" "$(_c 0)" "$*"; }
warn() { printf '%swarn%s %s\n' "$(_c '1;33')" "$(_c 0)" "$*" >&2; }
err()  { printf '%s err%s %s\n' "$(_c '1;31')" "$(_c 0)" "$*" >&2; }
die()  { err "$*"; exit 1; }

log "Lhassa quickstart; repo: $REPO_ROOT"

# ── 1. Python + uv ─────────────────────────────────────────────────────────────
log "Checking Python and uv"

if command -v python3 >/dev/null 2>&1; then
    PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    PYMAJOR="${PYVER%%.*}"
    PYMINOR="${PYVER#*.}"
    if [ "$PYMAJOR" -gt 3 ] || { [ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -ge 11 ]; }; then
        ok "python3: $PYVER"
    else
        warn "python3: $PYVER (Lhassa's pyproject.toml asks for 3.13+; uv will fetch its own"
        warn "  interpreter if needed, so this is informational, not a hard blocker)"
    fi
else
    warn "python3 not found on PATH; uv can still provision one for you"
fi

if ! command -v uv >/dev/null 2>&1; then
    err "uv not found. Lhassa's toolchain (dependency resolution + venv) needs it."
    err "  Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    err "  Docs: https://docs.astral.sh/uv/  (or see https://astral.sh/uv)"
    err "Then re-run: bash scripts/quickstart.sh"
    exit 1
fi
ok "uv: $(uv --version 2>&1)"

# ── 2. Dependencies (no dashboard build; that's for the full app) ────────────
log "Installing Python dependencies (uv sync)"
( cd "$REPO_ROOT" && uv sync ) || die "uv sync failed; see the error above"
ok "venv ready at $REPO_ROOT/.venv"

# ── 3. .env ─────────────────────────────────────────────────────────────────
log "Checking .env"
if [ -f "$ENV_FILE" ]; then
    ok ".env already exists; leaving it as is"
else
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        ok "created .env from .env.example"
    else
        warn ".env.example not found; skipping .env creation"
        touch "$ENV_FILE"
    fi
fi

# ── 4. Detect a model backend ──────────────────────────────────────────────────
log "Looking for a model backend"

_env_value() {
    # Read KEY from the process environment first, then from .env (uncommented,
    # non-empty). Never echoes secrets to the terminal; only used for presence.
    local key="$1" val=""
    val="$(printenv "$key" 2>/dev/null || true)"
    if [ -z "$val" ] && [ -f "$ENV_FILE" ]; then
        val="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" 2>/dev/null \
              | tail -n1 | cut -d'=' -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true)"
    fi
    printf '%s' "$val"
}

API_KEY_FOUND=""
for key in GROQ_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY OPENROUTER_API_KEY CEREBRAS_API_KEY; do
    if [ -n "$(_env_value "$key")" ]; then
        API_KEY_FOUND="$key"
        break
    fi
done

LOCAL_URL="$(_env_value LHASSA_LOCAL_MODEL_URL)"
OLLAMA_URL="${LOCAL_URL:-http://localhost:11434}"
OLLAMA_UP=0
if command -v curl >/dev/null 2>&1; then
    if curl -s -m 1 "$OLLAMA_URL" >/dev/null 2>&1; then
        OLLAMA_UP=1
    fi
fi

BACKEND_READY=0
if [ -n "$API_KEY_FOUND" ]; then
    ok "found $API_KEY_FOUND in the environment/.env"
    BACKEND_READY=1
fi
if [ "$OLLAMA_UP" -eq 1 ]; then
    ok "a local model responded at $OLLAMA_URL (Ollama)"
    BACKEND_READY=1
fi

if [ "$BACKEND_READY" -eq 0 ]; then
    cat <<'EOF'

Lhassa needs a brain to think with; it doesn't ship one. Bring your own,
either path is fine and both are quick:

  (a) A free/cheap API key (easiest start)
      Groq has a generous free tier: https://console.groq.com
      Grab a key, then add it to .env:
          GROQ_API_KEY=your-key-here
      (Anthropic / OpenAI / OpenRouter / Cerebras keys work too. Lhassa
      auto-detects whichever one you set, no extra config needed.)

  (b) Fully local, $0, no key at all. Ollama
      Install:  https://ollama.com
      Then:     ollama pull llama3.2
      Lhassa will find it automatically at http://localhost:11434.

Add one of the two, then re-run:
    bash scripts/quickstart.sh

EOF
    ok "dependencies are installed; you're one step away."
    exit 0
fi

# ── 5. Sanity check (no API calls, text-chat only) ─────────────────────────────
log "Running a quick sanity check"
if ( cd "$REPO_ROOT" && uv run --extra dev python -m scripts.check ); then
    ok "sanity check passed"
else
    warn "scripts/check.py flagged some items above; most (DEEPGRAM/CARTESIA/audio)"
    warn "  are about the OPT-IN voice pipeline and don't block text chat over the CLI"
fi

# ── 6. What's next ──────────────────────────────────────────────────────────────
cat <<EOF

$(_c '1;32')You're set.$(_c 0) A model backend is configured; next:

  1. Teach it a few founding facts about you (~5 quick questions):
       uv run lhassa onboard

  2. Talk to it:
       uv run lhassa

First things to try once it's up:
  - just talk to it; anything, plain language
  - uv run lhassa recall <text>       search its own past episodes
  - uv run lhassa memory <query>      search durable facts
  - uv run lhassa capabilities        what's wired, what's on/off

Want it always-on; a heartbeat that keeps ticking while you sleep, Telegram,
systemd + Docker? That's a separate, heavier step:
    bash scripts/install.sh

EOF

log "Done."
