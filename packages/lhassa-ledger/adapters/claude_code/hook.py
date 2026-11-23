#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Claude Code hook: record what the coding agent touched.

Claude Code runs on a subscription, so the interesting question is not what
a turn cost (the marginal token is already paid for) but **what it did**:
which files it wrote, which commands it ran, which endpoints it reached.
That is the half of the audit trail no billing dashboard can reconstruct.

Wire it up in `.claude/settings.json` (project) or `~/.claude/settings.json`
(every session):

    {
      "hooks": {
        "PostToolUse": [
          {"matcher": "*", "hooks": [
            {"type": "command",
             "command": "/path/to/adapters/claude_code/hook.py"}
          ]}
        ]
      }
    }

Contract this script holds to, because it runs inside every tool call the
owner makes all day:

- **It always exits 0.** A non-zero exit from a PostToolUse hook is feedback
  to the agent; an audit tool has no business steering the session it audits.
- **It is quiet.** Nothing on stdout, so it never injects text into a turn.
- **It is fast and it gives up.** No imports beyond stdlib, and any failure
  is swallowed rather than retried.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOST = "claude-code"

# The tool argument worth keeping, per tool. The point is a line an auditor
# can read six months later: "it wrote this file", not "it called Edit".
_TARGET_KEYS = ("file_path", "path", "command", "url", "pattern", "query", "prompt")


def _target(tool_input: dict) -> str:
    for key in _TARGET_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from agent_ledger.event import (
            COST_FROM_TABLE,
            KIND_TOOL_CALL,
            OUTCOME_ERROR,
            OUTCOME_OK,
            AgentEvent,
        )
        from agent_ledger.store import Store

        tool_input = payload.get("tool_input") or {}
        response = payload.get("tool_response")
        # Claude Code reports a failure either as an explicit flag or as an
        # error string in the response body, depending on the tool.
        failed = isinstance(response, dict) and (
            response.get("success") is False or bool(response.get("error"))
        )

        Store().record(
            AgentEvent(
                host=HOST,
                kind=KIND_TOOL_CALL,
                agent=payload.get("hook_event_name", ""),
                session=payload.get("session_id", ""),
                # Optional: set by the owner's shell profile or the installer.
                machine_id=os.environ.get("AGENT_LEDGER_MACHINE_ID", ""),
                instance_id=os.environ.get("AGENT_LEDGER_INSTANCE_ID", ""),
                tool=payload.get("tool_name", ""),
                target=_target(tool_input if isinstance(tool_input, dict) else {}),
                outcome=OUTCOME_ERROR if failed else OUTCOME_OK,
                # A subscription seat already paid for this call.
                cost_usd=0.0,
                cost_source=COST_FROM_TABLE,
                detail={"cwd": payload.get("cwd", "")},
            )
        )
    except Exception:
        # Never let an audit tool break the session it is auditing.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
