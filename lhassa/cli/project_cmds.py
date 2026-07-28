"""``lhassa project`` — the first-class workspace entity from the terminal.

Commands::

    lhassa project create <name> [--kind code] [--path .]
    lhassa project list [--all] [--kind K]
    lhassa project show <id-or-name>
    lhassa project archive <id-or-name>

Registered as a sub-app in ``lhassa.cli.app`` via::

    app.add_typer(project_cmds.project_app, name="project")
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, TABLE_KWARGS

project_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage projects — the workspaces Lhassa works on.",
)

_console = Console(theme=LHASSA_THEME)


def _instance_id() -> str:
    from lhassa.config import Config

    return getattr(Config, "instance_id", None) or "inst_personal"


@project_app.command("create")
def create(
    name: str = typer.Argument(..., help="Project name (unique among active)."),
    kind: str = typer.Option("generic", "--kind", "-k", help="Project type: code/research/ops/personal/generic."),
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Root directory/repo of the project."),
) -> None:
    """Create a project. A ``code`` project is what standing brains key off."""
    from lhassa.core import projects

    try:
        row = asyncio.run(
            projects.create_project(_instance_id(), name, kind=kind, path=path)
        )
    except ValueError as exc:
        _console.print(f"[warn]refused:[/warn] {exc}")
        raise typer.Exit(1) from None
    where = f" · {row['path']}" if row.get("path") else ""
    _console.print(f"[ok]created[/ok] {row['name']} ({row['kind']}){where} — id [accent]{row['id']}[/accent]")


@project_app.command("list")
def list_cmd(
    all_: bool = typer.Option(False, "--all", help="Include archived projects."),
    kind: Optional[str] = typer.Option(None, "--kind", "-k"),
) -> None:
    """List projects (newest first)."""
    from lhassa.core import projects

    rows = asyncio.run(
        projects.list_projects(
            _instance_id(), status=None if all_ else "active", kind=kind
        )
    )
    if not rows:
        _console.print("[stone]no projects yet — lhassa project create <name>[/stone]")
        return
    table = Table(**TABLE_KWARGS)
    for col in ("id", "name", "kind", "status", "path"):
        table.add_column(col)
    for row in rows:
        table.add_row(row["id"], row["name"], row["kind"], row["status"], row.get("path") or "—")
    _console.print(table)


@project_app.command("show")
def show(ref: str = typer.Argument(..., help="Project id, name, or a path inside it.")) -> None:
    """One project in full (metadata included)."""
    from lhassa.core import projects

    row = asyncio.run(projects.resolve_project(_instance_id(), ref))
    if row is None:
        _console.print(f"[warn]no project matches[/warn] {ref!r}")
        raise typer.Exit(1)
    for key in ("id", "name", "kind", "status", "path", "owner_department", "created_at", "updated_at"):
        _console.print(f"[stone]{key}:[/stone] {row.get(key) or '—'}")
    meta = row.get("metadata") or {}
    if meta:
        _console.print(f"[stone]metadata:[/stone] {meta}")


@project_app.command("archive")
def archive(ref: str = typer.Argument(...)) -> None:
    """Archive a project (never deleted; excluded from active listings)."""
    from lhassa.core import projects

    row = asyncio.run(projects.resolve_project(_instance_id(), ref))
    if row is None:
        _console.print(f"[warn]no project matches[/warn] {ref!r}")
        raise typer.Exit(1)
    ok = asyncio.run(projects.archive_project(_instance_id(), row["id"]))
    _console.print("[ok]archived[/ok]" if ok else "[warn]nothing changed[/warn]")
