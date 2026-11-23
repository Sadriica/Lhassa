#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Link the Hermes adapter into a Hermes install. Reversible: it creates one
# symlink and enables nothing. Run `hermes plugins enable
# observability/agent-ledger` yourself once you have looked at it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
TARGET="$HERMES_HOME/hermes-agent/plugins/observability/agent-ledger"

if [ ! -d "$HERMES_HOME/hermes-agent/plugins/observability" ]; then
  echo "no Hermes install found at $HERMES_HOME" >&2
  exit 1
fi

ln -sfn "$REPO/adapters/hermes" "$TARGET"
echo "linked $TARGET -> $REPO/adapters/hermes"
echo
echo "next:"
echo "  hermes plugins enable observability/agent-ledger"
echo
echo "AGENT_LEDGER_HOME is only needed if you copied the plugin instead of"
echo "linking it: a symlinked plugin resolves the library through its own path."
echo "  $REPO/.venv/bin/python -m agent_ledger.cli report"
