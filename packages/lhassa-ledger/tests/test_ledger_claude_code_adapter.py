# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Claude Code hook.

This script runs inside every tool call the owner makes, so the contract is
almost entirely about restraint: exit 0 whatever happens, print nothing,
and never let a malformed payload escape as an exception.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_ledger.store import Store

HOOK = Path(__file__).resolve().parents[1] / "adapters" / "claude_code" / "hook.py"


def _run(payload, db) -> subprocess.CompletedProcess:
    env = {**os.environ, "AGENT_LEDGER_DB": str(db)}
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)], input=body, capture_output=True, text=True, env=env
    )


def test_a_tool_use_is_recorded(tmp_path):
    db = tmp_path / "ledger.db"
    result = _run(
        {
            "session_id": "sess-1",
            "cwd": "/home/w/project",
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/home/w/project/main.py"},
            "tool_response": {"success": True},
        },
        db,
    )
    assert result.returncode == 0
    (event,) = list(Store(db).events())
    assert event.host == "claude-code"
    assert event.tool == "Edit"
    assert event.target == "/home/w/project/main.py"
    assert event.outcome == "ok"
    assert event.cost_usd == 0.0          # the seat already paid for it
    assert event.detail["cwd"] == "/home/w/project"


def test_a_bash_command_is_kept_as_the_target(tmp_path):
    db = tmp_path / "ledger.db"
    _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf build/"},
          "tool_response": {}}, db)
    (event,) = list(Store(db).events())
    assert event.target == "rm -rf build/"


def test_a_failed_tool_is_marked(tmp_path):
    db = tmp_path / "ledger.db"
    _run({"tool_name": "Bash", "tool_input": {"command": "false"},
          "tool_response": {"success": False}}, db)
    (event,) = list(Store(db).events())
    assert event.outcome == "error"


@pytest.mark.parametrize("payload", ["", "not json", "[]", "{}", '{"tool_input": "oops"}'])
def test_garbage_input_exits_clean_and_silent(tmp_path, payload):
    result = _run(payload, tmp_path / "ledger.db")
    assert result.returncode == 0
    # Anything on stdout would be injected back into the agent's turn.
    assert result.stdout == ""


def test_the_hook_prints_nothing_on_success(tmp_path):
    result = _run({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}},
                  tmp_path / "ledger.db")
    assert result.stdout == ""
