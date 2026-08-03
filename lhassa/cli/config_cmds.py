# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa config`` — read and change the runtime SETTINGS allowlist.

A small, offline surface over :mod:`lhassa.settings`: list the editable settings
(grouped, secrets masked), read one, or change one (persisted to
``~/.lhassa/settings.env`` and applied to the live :class:`Config`, never ``.env``).

Registered onto the main Typer ``app`` as a sub-app — see ``lhassa.cli.app``
(``app.add_typer(config_cmds.config_app, name="config")``) — alongside, and without
disturbing, the default chat/REPL and the existing power subcommands.
"""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console

config_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Inspect and change Lhassa's runtime settings (writes ~/.lhassa/settings.env).",
)


def _out() -> Console:
    return Console()


def _err() -> Console:
    return Console(stderr=True)


def _dump_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


# ── list ────────────────────────────────────────────────────────────────────────


def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the settings as JSON."),
) -> None:
    """List every editable setting, grouped, with secrets masked."""
    from lhassa import settings

    model = settings.effective()
    if json_out:
        _dump_json(model)
        return

    from rich.table import Table

    out = _out()
    rows = model["settings"]
    for group in model["groups"]:
        table = Table(title=group)
        for col in ("key", "value", "default", "overridden", "restart"):
            table.add_column(col)
        for r in (s for s in rows if s["group"] == group):
            table.add_row(
                str(r["key"]),
                str(r["value"]),
                str(r["default"]),
                "yes" if r["overridden"] else "—",
                str(r["restart"]),
            )
        out.print(table)


# ── get ─────────────────────────────────────────────────────────────────────────


def get_cmd(
    key: str = typer.Argument(..., help="The setting key (see `lhassa config list`)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the setting as JSON."),
) -> None:
    """Read one setting's current value, default and overridden flag."""
    from lhassa import settings

    row = settings.get(key)
    if json_out:
        _dump_json(row)
        return
    if "error" in row:
        _err().print(row["error"], style="red")
        raise typer.Exit(code=1)
    _out().print(
        f"{row['key']} = {row['value']}  "
        f"(default={row['default']}, overridden={'yes' if row['overridden'] else 'no'})"
    )


# ── set ─────────────────────────────────────────────────────────────────────────


def set_cmd(
    key: str = typer.Argument(..., help="The setting key to change."),
    value: str = typer.Argument(..., help="The new value."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Change one setting (persists to settings.env + applies to the live Config)."""
    from lhassa import settings

    result = settings.set(key, value)
    if json_out:
        _dump_json(result)
        if not result.get("applied"):
            raise typer.Exit(code=1)
        return
    if not result.get("applied"):
        _err().print(result.get("error", "could not apply setting"), style="red")
        raise typer.Exit(code=1)
    out = _out()
    out.print(f"{result['key']} = {result['value']}")
    out.print(result.get("restart_hint", ""), style="dim")


# ── registration ─────────────────────────────────────────────────────────────────

config_app.command("list")(list_cmd)
config_app.command("get")(get_cmd)
config_app.command("set")(set_cmd)
