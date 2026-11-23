# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa approvals``: the pending queue (bare / ``--json``) and ``expire``.

A Typer group whose bare form still lists the queue, so ``lhassa approvals`` and
``lhassa approvals --json`` keep working; ``expire`` closes unanswered
self-proposals (see ``lhassa.security.approval.expire_stale``).
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

approvals_app = typer.Typer(
    help="The approval queue: list it, or close stale self-proposals (expire).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@approvals_app.callback()
def _queue(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Emit the queue as JSON."),
) -> None:
    if ctx.invoked_subcommand is None:
        from lhassa.cli.commands import approvals_cmd

        approvals_cmd(json_out)


@approvals_app.command("expire")
def expire_cmd(
    days: Optional[int] = typer.Option(None, "--days", help="Close self-proposals older than this (0 = never). Default: LHASSA_APPROVAL_EXPIRY_DAYS (30)."),
    cap: Optional[int] = typer.Option(None, "--cap", help="Keep at most this many pending per type (0 = no cap). Default: LHASSA_APPROVAL_PENDING_CAP (20)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would close; change nothing."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Close unanswered self-proposals (retire_capability, mint_*): too old, or beyond the cap."""
    import json

    from lhassa.cli.commands import _err, _instance_id, _out
    from lhassa.core import db
    from lhassa.security import approval

    async def _run() -> dict:
        await db.init_db()
        return await approval.expire_stale(_instance_id(), days=days, cap=cap, dry_run=dry_run)

    result = asyncio.run(_run())
    if json_out:
        _out().print_json(json.dumps(result))
        return
    verb = "would close" if dry_run else "closed"
    _out().print(
        f"{verb} {len(result['timeout'])} by timeout and {len(result['cap'])} by cap; "
        f"{result['kept']} self-proposals stay pending"
    )
    if dry_run and (result["timeout"] or result["cap"]):
        _err().print("(dry run: nothing was written)", style="dim")
