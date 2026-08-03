# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa tools`` — the self-written-tools surface (authoring + the quarantine room).

Commands::

    lhassa tools [--json]                          # list (unchanged, now a group)
    lhassa tools propose <name> --desc "..." [--example TEXT ...] [--baseline N]

``propose`` is the human/approval-driven door onto the missing first mile of
self-improvement 2b (:mod:`lhassa.agents.author`): it asks the routine model to
WRITE a small candidate tool, runs it through the quarantine + gates pipeline,
and — only if every gate passes — parks a ``tool_graduation`` approval. Nothing
here auto-graduates a tool; ``lhassa`` never fires this on its own.

``tools`` used to be a flat command (``lhassa.cli.commands.tools_cmd``); it is now
this Typer GROUP so ``propose`` can live alongside it. The bare/``--json`` form is
preserved byte-for-byte via ``invoke_without_command`` — reuses the exact same
loader (:func:`lhassa.cli.commands._load_tools`) so existing scripts/tests keep
working. Registered as a sub-app in ``lhassa.cli.app`` via::

    app.add_typer(tools_cmds.tools_app, name="tools")
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import typer
from rich.console import Console

tools_app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Self-written tools: list the quarantine room, or propose a new candidate.",
)


def _out() -> Console:
    return Console()


def _err() -> Console:
    return Console(stderr=True)


def _config():
    from lhassa.config import Config

    return Config()


@tools_app.callback(invoke_without_command=True)
def tools_default(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Emit the tool rows as JSON."),
) -> None:
    """List the self-written tools and their gate/usage state (bare ``lhassa tools``).

    Delegates to :func:`lhassa.cli.commands.tools_cmd` — the exact same rendering
    the flat command used before ``tools`` became a group — so this is a no-op
    change for existing callers. Does nothing when a subcommand (``propose``) was
    invoked instead.
    """
    if ctx.invoked_subcommand is not None:
        return
    from lhassa.cli.commands import tools_cmd

    tools_cmd(json_out=json_out)


@tools_app.command("propose")
def tools_propose(
    name: str = typer.Argument(..., help="Candidate tool name."),
    description: str = typer.Option(
        ..., "--desc", "-d", help="What the tool should do."
    ),
    examples: Optional[List[str]] = typer.Option(
        None, "--example", "-e", help="A usage example (repeatable)."
    ),
    baseline: Optional[float] = typer.Option(
        None, "--baseline", help="Baseline metric to beat (gate 3 'mejor'). "
        "Without one, the candidate cannot graduate (deny-by-default)."
    ),
    domain: str = typer.Option(
        "optimization", "--domain", help="Gate domain (default: optimization)."
    ),
    max_turns: int = typer.Option(
        3, "--max-turns", help="Bounded iterations: on a retryable gate failure "
        "(segura/correcta), the candidate is re-authored with the failing gate's "
        "detail threaded in as feedback, up to this many turns."
    ),
) -> None:
    """Author a candidate tool and run it through quarantine + the four gates.

    Asks the routine (cheap) model to write ``<name>.py`` + its test, commits
    the candidate on a local git branch, and runs the segura/correcta/mejor
    gates in order — ITERATING (``--max-turns``) on a retryable gate failure
    before giving up. If every gate passes, a ``tool_graduation`` approval is
    parked (resolve it via ``POST /api/tools/<name>/graduate`` or the
    dashboard) — this command NEVER auto-graduates a tool.
    """

    async def _run() -> dict:
        from lhassa.agents.author import propose_tool
        from lhassa.core import db as _db

        await _db.init_db()
        return await propose_tool(
            name,
            description,
            list(examples or []),
            domain=domain,
            baseline=baseline,
            max_turns=max_turns,
            instance_id=_config().instance_id,
        )

    out = _out()
    result = asyncio.run(_run())

    if not result.get("authored"):
        _err().print(f"[yellow]✗[/yellow] {result.get('reason', 'not authored')}")
        raise typer.Exit(code=1)

    turns = result.get("turns")
    turns_note = f" (turn {turns}/{max_turns})" if turns else ""

    if result.get("graduated"):
        out.print(
            f"[green]✓[/green] {name} passed all gates{turns_note} — "
            "[bold]approval parked[/bold]"
        )
        out.print(f"  [dim]approval_id: {result.get('approval_id')}[/dim]")
        out.print(f"  [dim]branch: {result.get('branch')}[/dim]")
    else:
        capped_note = " [red](capped)[/red]" if result.get("capped") else ""
        out.print(
            f"[yellow]✗[/yellow] {name} did not graduate{turns_note}{capped_note} — "
            f"{result.get('reason', '')}"
        )
        for gate in result.get("gates") or []:
            status = "[green]pass[/green]" if getattr(gate, "passed", False) else "[red]fail[/red]"
            out.print(f"  {status} {getattr(gate, 'gate', '?')}: {getattr(gate, 'detail', '')[:120]}")
