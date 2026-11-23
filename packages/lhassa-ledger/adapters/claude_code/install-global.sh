#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Add the agent-ledger PostToolUse hook to ~/.claude/settings.json.
#
# The owner runs this, not an agent: a hooks file executes a command on every
# tool call, so installing one is a decision, not a chore. It is idempotent,
# it backs the file up first, and it validates the JSON before replacing it.
#
#   bash adapters/claude_code/install-global.sh          # install
#   bash adapters/claude_code/install-global.sh --remove # undo
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$REPO/adapters/claude_code/hook.py"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
MODE="${1:-install}"

[ -x "$HOOK" ] || { echo "hook not executable: $HOOK" >&2; exit 1; }
[ -f "$SETTINGS" ] || { echo "no settings file at $SETTINGS" >&2; exit 1; }

BACKUP="$SETTINGS.bak-agent-ledger-$(date +%Y%m%d-%H%M%S)"
cp "$SETTINGS" "$BACKUP"

python3 - "$SETTINGS" "$HOOK" "$MODE" <<'PYEOF'
import json, sys

settings_path, hook_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]

with open(settings_path) as fh:
    settings = json.load(fh)

hooks = settings.setdefault("hooks", {})
entries = hooks.setdefault("PostToolUse", [])

def is_ours(entry):
    return any(h.get("command") == hook_path for h in entry.get("hooks", []))

before = len(entries)
entries[:] = [e for e in entries if not is_ours(e)]
removed = before - len(entries)

if mode == "--remove":
    if not entries:
        hooks.pop("PostToolUse", None)
    action = f"removed {removed} entry" if removed else "nothing to remove"
else:
    # Appended, never merged into the existing entry: another tool's hook
    # (Orca's, here) must keep its own timeout and its own failure surface.
    entries.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": hook_path, "timeout": 5}],
    })
    action = "updated" if removed else "installed"

rendered = json.dumps(settings, indent=2) + "\n"
json.loads(rendered)   # never write something Claude Code cannot parse
with open(settings_path, "w") as fh:
    fh.write(rendered)

print(f"agent-ledger hook {action}")
print(f"PostToolUse entries now: {len(hooks.get('PostToolUse', []))}")
PYEOF

echo "backup: $BACKUP"
echo "takes effect in new Claude Code sessions."
