# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa utility``: the tribunal that asks whether a capability still serves.

The read surface over :mod:`lhassa.agents.utility`. Where ``lhassa capabilities``
answers "what is wired?", this answers "what is EARNING its place?": the
counterweight question. Everything here is a pure read: $0, no LLM, no writes.
"""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, TABLE_KWARGS

utility_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Which capabilities still serve the owner (the utility tribunal).",
)
_console = Console(theme=LHASSA_THEME)

_STATE_STYLE = {
    "active": ("ok", "✓"),
    "self_driven": ("warn", "↻"),
    "unused": ("stone", "–"),
}


def _instance_id() -> str:
    from lhassa.config import Config

    return Config.instance_id


def _render(rows: list, *, title: str) -> None:
    if not rows:
        _console.print("[stone]nothing to report.[/stone]")
        return
    table = Table(title=title, **TABLE_KWARGS)
    table.add_column("capability")
    table.add_column("state")
    table.add_column("owner", justify="right")
    table.add_column("self", justify="right")
    table.add_column("last owner use", justify="right")
    for r in rows:
        style, marker = _STATE_STYLE.get(r["state"], ("stone", "?"))
        days = r["days_since_owner_use"]
        last = f"{days:.0f}d ago" if days is not None else "never"
        name = r["capability"] + (" 📌" if r.get("pinned") else "")
        table.add_row(
            name,
            f"[{style}]{marker} {r['state']}[/{style}]",
            str(r["owner_uses"]),
            str(r["self_uses"]),
            last,
        )
    _console.print(table)


@utility_app.command("survey")
def survey_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit raw rows as JSON."),
    all_rows: bool = typer.Option(
        False, "--all", help="Include capabilities that are actively owner-driven."
    ),
) -> None:
    """Every registered capability, least-earned first.

    ``owner`` counts uses a human reached for; ``self`` counts uses Lhassa
    scheduled or delegated to itself. A capability with only ``self`` uses runs
   ; but does not earn its place.
    """
    from lhassa.actionpack import register_all
    from lhassa.agents import utility

    register_all()  # so the ACTIONS registry is fully populated before we read it
    rows = asyncio.run(utility.survey(_instance_id()))
    if not all_rows:
        rows = [r for r in rows if r["state"] != utility.STATE_ACTIVE]
    if json_out:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    _render(rows, title="capabilities; least earned first")


@utility_app.command("docket")
def docket_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit raw rows as JSON."),
) -> None:
    """The retirement docket: unused or self-driven, unpinned, past the grace period."""
    from lhassa.actionpack import register_all
    from lhassa.agents import utility

    register_all()
    rows = asyncio.run(utility.find_dormant(_instance_id()))
    if json_out:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        days = asyncio.run(utility.history_days(_instance_id()))
        from lhassa.config import Config

        grace = int(getattr(Config, "utility_grace_days", 14) or 14)
        if days < grace:
            _console.print(
                f"[stone]only {days:.0f}d of history; nothing is put on trial "
                f"before {grace}d.[/stone]"
            )
        else:
            _console.print("[ok]✓[/ok] every capability is earning its place.")
        return
    _render(rows, title="retirement docket")
    _console.print(
        "[stone]pin one with LHASSA_UTILITY_PINNED to keep it deliberately "
        "dormant.[/stone]"
    )


def register(app: "typer.Typer") -> None:
    """Attach ``lhassa utility`` to the main Typer app."""
    app.add_typer(utility_app, name="utility")
