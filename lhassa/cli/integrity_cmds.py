# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa integrity``: executable philosophy and its evidence."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, TABLE_KWARGS

integrity_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Run and inspect deterministic Witara/Lhassa conformance contracts.",
)
_console = Console(theme=LHASSA_THEME)


def _instance_id() -> str:
    from lhassa.config import Config

    return Config.instance_id


@integrity_app.command("check")
def check_cmd() -> None:
    """Run every versioned contract now and mint/withhold an attestation."""
    from lhassa.org.integrity import run_integrity

    result = asyncio.run(run_integrity(trigger="cli"))
    style = "ok" if result["status"] == "pass" else "warn"
    _console.print(
        f"[{style}]integrity={result['status']}[/{style}] · "
        f"run {result['run_id']} · "
        f"{result['findings_critical']} critical · "
        f"{result['findings_warn']} warning"
    )
    for check in result["checks"]:
        marker = "✓" if check["passed"] else "✗"
        _console.print(
            f"  {marker} {check['contract_id']}: {check['summary']}"
        )
    if result.get("attestation_expires_at"):
        _console.print(
            f"[stone]attestation expires:[/stone] "
            f"{result['attestation_expires_at']}"
        )
    if result["status"] != "pass":
        raise typer.Exit(1)


@integrity_app.command("status")
def status_cmd() -> None:
    """Show the latest run, attestation and current contract projection."""
    from lhassa.core import db
    from lhassa.core.conformance import attestation

    async def load():
        await db.init_db()
        return (
            await attestation(_instance_id()),
            await db.get_conformance_state(_instance_id()),
        )

    attested, rows = asyncio.run(load())
    _console.print(
        f"[stone]attestation:[/stone] "
        f"{'valid' if attested['valid'] else 'invalid'} · {attested['reason']}"
    )
    table = Table(**TABLE_KWARGS)
    for column in ("contract", "version", "severity", "passed", "summary", "checked"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row["contract_id"],
            str(row["contract_version"]),
            row["severity"],
            "yes" if row["passed"] else "NO",
            row["summary"],
            row["checked_at"],
        )
    _console.print(table)


@integrity_app.command("findings")
def findings_cmd(
    limit: int = typer.Option(100, "--limit", "-n", min=1, max=1000),
) -> None:
    """List append-only conformance findings, newest first."""
    from lhassa.core import db

    async def load():
        await db.init_db()
        return await db.list_conformance_findings(_instance_id(), limit=limit)

    rows = asyncio.run(load())
    if not rows:
        _console.print("[stone]no conformance findings[/stone]")
        return
    table = Table(**TABLE_KWARGS)
    for column in ("created", "severity", "contract", "summary", "run"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row["created_at"],
            row["severity"],
            row["contract_id"],
            row["summary"],
            row["run_id"],
        )
    _console.print(table)


@integrity_app.command("contracts")
def contracts_cmd() -> None:
    """List the active versioned invariant manifest."""
    from lhassa.org.integrity import CONTRACTS, MANIFEST_VERSION

    _console.print(f"[stone]manifest version:[/stone] {MANIFEST_VERSION}")
    for contract in CONTRACTS:
        _console.print(
            f"  {contract.id}@{contract.version} [{contract.severity}] · "
            f"{contract.description}"
        )
