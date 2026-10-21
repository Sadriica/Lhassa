# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tui pack mounts ``lhassa tui`` through ``extensions.register_cli_command``."""

from __future__ import annotations

import subprocess
import sys

from typer.testing import CliRunner


def test_pack_registers_manifest_and_command():
    import lhassa.tui.pack  # noqa: F401
    from lhassa.extensions import CLI_COMMANDS, PACKS
    from lhassa.tui.command import tui_cmd

    assert "tui" in PACKS
    assert CLI_COMMANDS.get("tui")() is tui_cmd


def test_pack_import_does_not_pull_textual():
    code = "import sys, lhassa.tui.pack; print('textual' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_lhassa_tui_is_a_top_level_command(monkeypatch):
    from lhassa.cli.app import app

    called = []
    monkeypatch.setattr("lhassa.tui.app.run_tui", lambda: called.append(True))
    result = CliRunner().invoke(app, ["tui"])
    assert result.exit_code == 0, result.output
    assert called == [True]
    help_out = CliRunner().invoke(app, ["tui", "--help"])
    assert help_out.exit_code == 0 and "Textual terminal dashboard" in help_out.output
