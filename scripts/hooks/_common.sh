# SPDX-License-Identifier: AGPL-3.0-or-later
# Shared helpers for the lhassa review hooks. Sourced, never executed.
# Contract: a review failure must never break a git operation. Everything here
# degrades to "do nothing" and the caller exits with the delegate's status.

LHASSA_HOOKS_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
LHASSA_REPO_ROOT="$(cd "$LHASSA_HOOKS_DIR/../.." && pwd)"

# Run the review engine. LHASSA_REVIEW_CMD overrides (a command prefix, e.g.
# "uv run --project /path lhassa review"). The default needs only python3: the
# engine is stdlib-only and is imported straight from this checkout.
lhassa_review() {
  if [ -n "${LHASSA_REVIEW_CMD:-}" ]; then
    # shellcheck disable=SC2086  # intentional word splitting of the command prefix
    $LHASSA_REVIEW_CMD "$@"
  else
    command -v python3 >/dev/null 2>&1 || return 0
    (cd "$LHASSA_REPO_ROOT" && python3 -m lhassa.review "$@")
  fi
}

# Run the repo's own hook (.git/hooks/<name>) when core.hooksPath hides it.
# Usage: lhassa_delegate <hook-name> [hook args...]; stdin is forwarded from
# the file named by LHASSA_STDIN_FILE when set. Returns the repo hook's status.
lhassa_delegate() {
  name="$1"; shift
  gitdir="$(git rev-parse --git-common-dir 2>/dev/null)" || return 0
  repo_hook="$gitdir/hooks/$name"
  [ -f "$repo_hook" ] && [ -x "$repo_hook" ] || return 0
  # Never run ourselves again (a repo that symlinks its hooks to this directory).
  [ "$repo_hook" -ef "$0" ] && return 0
  if [ -n "${LHASSA_STDIN_FILE:-}" ]; then
    "$repo_hook" "$@" < "$LHASSA_STDIN_FILE"
  else
    "$repo_hook" "$@"
  fi
}
