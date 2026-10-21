# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``lhassa tui`` command function, mounted by ``cli/app.py`` via
``extensions.register_cli_command``. Imports nothing heavy at module level."""

from __future__ import annotations


def tui_cmd() -> None:
    """Launch the Textual terminal dashboard (offline, direct DB reads)."""
    from lhassa.cli._boot import quiet_logging

    # Belt-and-suspenders: cli/app.py already calls quiet_logging() at import,
    # but the call is idempotent and cheap, and the TUI must NEVER run with
    # loguru's noisy default sink live even if something reaches this command
    # without that import having happened first.
    quiet_logging()
    # Lazy import: importing the CLI must never pay Textual's import cost.
    from lhassa.tui.app import run_tui

    run_tui()


__all__ = ["tui_cmd"]
