"""``lhassa research`` — the product's research line from the terminal.

Commands::

    lhassa research draft <topic> [--lang en|es]
    lhassa research list

The line itself is declared in ``doc/research.md``; drafts land in
``doc/notes/drafts/`` with a review-before-publishing header. Registered as a
sub-app in ``lhassa.cli.app`` via::

    app.add_typer(research_cmds.research_app, name="research")
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, TABLE_KWARGS

research_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="The research line: draft evidence-cited technical notes.",
)

_console = Console(theme=LHASSA_THEME)


@research_app.command("draft")
def draft(
    topic: str = typer.Argument(..., help="What the note is about (e.g. 'earned-autonomy')."),
    lang: str = typer.Option("en", "--lang", help="Draft language: en or es."),
) -> None:
    """Gather the evidence pack and draft one technical note."""
    from lhassa import actionpack
    from lhassa.actionpack._research_note import draft_research_note

    actionpack.register_all()
    out = asyncio.run(draft_research_note({"topic": topic, "lang": lang}))
    style = "ok" if "written to" in out else "warn"
    _console.print(f"[{style}]{out}[/{style}]")


@research_app.command("list")
def list_cmd() -> None:
    """List existing note drafts."""
    from lhassa.actionpack._research_note import _drafts_dir
    from lhassa.config import Config

    drafts = _drafts_dir(Config)
    files = sorted(drafts.glob("*.md")) if drafts.exists() else []
    if not files:
        _console.print("[stone]no drafts yet — lhassa research draft <topic>[/stone]")
        return
    table = Table(**TABLE_KWARGS)
    for col in ("draft", "size", "modified"):
        table.add_column(col)
    from datetime import datetime

    for f in files:
        st = f.stat()
        table.add_row(
            f.stem,
            f"{st.st_size} B",
            datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        )
    _console.print(table)
