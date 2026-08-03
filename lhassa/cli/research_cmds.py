# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa research`` — the product's research line from the terminal.

Commands::

    lhassa research draft <topic> [--lang en|es]
    lhassa research list
    lhassa research assessments
    lhassa research assessment <idea-id>
    lhassa research propose-change <project> <draft> --objective ... --verify ...
    lhassa research build-change <change-id>
    lhassa research apply-change <change-id>

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


def _instance_id() -> str:
    from lhassa.config import Config

    return getattr(Config, "instance_id", None) or "inst_personal"


@research_app.command("assessments")
def assessments_cmd(
    status: str | None = typer.Option(None, "--status", help="Filter current status."),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=500),
) -> None:
    """List idea evaluations, their earned investment and disposition."""
    from lhassa.core import db

    async def _load():
        await db.init_db()
        return await db.list_idea_assessments(
            _instance_id(), status=status, limit=limit
        )

    rows = asyncio.run(_load())
    if not rows:
        _console.print("[stone]no idea assessments yet — use /idea <idea>[/stone]")
        return
    table = Table(**TABLE_KWARGS)
    for column in ("id", "status", "investment", "decision", "score", "idea"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row["id"],
            row["status"],
            row.get("investment_level") or "—",
            row.get("decision") or "—",
            "—" if row.get("weighted_score") is None else f"{row['weighted_score']}/42",
            str(row["idea"])[:70],
        )
    _console.print(table)


@research_app.command("assessment")
def assessment_cmd(
    assessment_id: str = typer.Argument(..., help="Idea assessment id."),
) -> None:
    """Show one evaluation and its complete append-only stage log."""
    from lhassa.core import db

    async def _load():
        await db.init_db()
        row = await db.get_idea_assessment(_instance_id(), assessment_id)
        events = await db.list_idea_assessment_events(
            _instance_id(), assessment_id
        )
        return row, events

    row, events = asyncio.run(_load())
    if row is None:
        _console.print(f"[warn]no idea assessment[/warn] {assessment_id!r}")
        raise typer.Exit(1)
    for key in (
        "id",
        "idea",
        "status",
        "investment_level",
        "decision",
        "weighted_score",
        "evidence_path",
        "assessment_path",
        "draft_path",
    ):
        _console.print(f"[stone]{key}:[/stone] {row.get(key) or '—'}")
    _console.print("\n[accent]criteria[/accent]")
    for name, item in (row.get("criteria") or {}).items():
        sources = ",".join(item.get("source_ids") or []) or "internal/UNVERIFIED"
        _console.print(
            f"  {name}: {item.get('score')}/3 · {item.get('rationale')} · {sources}"
        )
    _console.print("\n[accent]event log[/accent]")
    for event in events:
        _console.print(
            f"  {event['created_at']} · {event['stage']} · "
            f"{event.get('from_status') or '∅'} → {event['to_status']}"
        )


@research_app.command("propose-change")
def propose_change(
    project: str = typer.Argument(..., help="Code project id, exact name, or path."),
    draft: str = typer.Argument(..., help="A corpus draft that passes the research gate."),
    objective: str = typer.Option(..., "--objective", "-o", help="Small, testable product change."),
    verify: str = typer.Option(..., "--verify", "-v", help="Objective acceptance command."),
) -> None:
    """Turn gated research into an experiment proposal; change no product files."""
    from lhassa.agents import product_change

    try:
        record = asyncio.run(
            product_change.propose(
                _instance_id(),
                project_ref=project,
                research_path=draft,
                objective=objective,
                verify_command=verify,
            )
        )
    except (ValueError, RuntimeError) as exc:
        _console.print(f"[warn]refused:[/warn] {exc}")
        raise typer.Exit(1) from None
    _console.print(
        f"[ok]proposal {record['id']}[/ok] · research locked at "
        f"{record['research']['sha256'][:12]}…\n"
        f"Approve [accent]{record['experiment_approval_id']}[/accent], then run "
        f"`lhassa research build-change {record['id']}`. Nothing touched the product."
    )


@research_app.command("build-change")
def build_change(
    proposal_id: str = typer.Argument(..., help="Product-change proposal id."),
    cli: str | None = typer.Option(None, "--cli", help="claude, codex, or opencode."),
) -> None:
    """Build and verify an approved experiment in its external safe place."""
    from lhassa.agents import product_change

    try:
        record = asyncio.run(
            product_change.build(_instance_id(), proposal_id, cli=cli)
        )
    except (ValueError, RuntimeError) as exc:
        _console.print(f"[warn]stopped safely:[/warn] {exc}")
        raise typer.Exit(1) from None
    _console.print(
        f"[ok]candidate verified[/ok] in {record['verification']['isolation']} · "
        f"commit {record['candidate_commit'][:12]}\n"
        f"Approve [accent]{record['integration_approval_id']}[/accent], inspect "
        f"{record['workspace']}, then run "
        f"`lhassa research apply-change {record['id']}`."
    )


@research_app.command("apply-change")
def apply_change(
    proposal_id: str = typer.Argument(..., help="Product-change proposal id."),
) -> None:
    """Integrate an unchanged candidate after its distinct integration approval."""
    from lhassa.agents import product_change

    try:
        record = asyncio.run(product_change.apply(_instance_id(), proposal_id))
    except (ValueError, RuntimeError) as exc:
        _console.print(f"[warn]refused:[/warn] {exc}")
        raise typer.Exit(1) from None
    _console.print(
        f"[ok]integrated[/ok] as {record['merge_commit'][:12]}\n"
        f"Rollback (still a deliberate owner action): {record['rollback_command']}"
    )
