# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for the terminal app (``lhassa.plugins`` -> ``tui``).

Importing this module registers the pack and mounts ``lhassa tui`` as a single
top-level command. Textual is imported only when the command runs, never here.
"""

from __future__ import annotations

from lhassa.extensions import register_cli_command, register_pack

register_pack(
    "tui",
    "lhassa tui: the Textual terminal app over the brain (direct DB reads)",
    module=__name__,
)


def _cli_command():
    from lhassa.tui.command import tui_cmd

    return tui_cmd


register_cli_command("tui", _cli_command)
