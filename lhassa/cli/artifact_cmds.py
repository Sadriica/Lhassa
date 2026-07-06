"""``lhassa artifacts`` — inspect the registered delivery-chain artifacts.

A small, read-only surface over :mod:`lhassa.core.artifacts` (the append-only
registry a leaf brain writes to via :mod:`lhassa.org.deliver` whenever it
delivers a work product — a tailored CV's ``.tex``+``.pdf``, a report, any file
the owner ends up receiving). No writes happen here; artifacts are only ever
registered as a side effect of a delivery.

Registered onto the main Typer ``app`` as a sub-app — see ``lhassa.cli.app``
(``app.add_typer(artifact_cmds.artifact_app, name="artifacts")``), the same
pattern as ``lhassa flows``.
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console
from rich.table import Table

artifact_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Inspect registered artifacts — the delivery chain's audit trail.",
)


def _out() -> Console:
    return Console()


def _err() -> Console:
    return Console(stderr=True)


def _dump_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _config():
    from lhassa.config import Config

    return Config()


# ── list ─────────────────────────────────────────────────────────────────────────


def list_cmd(
    limit: int = typer.Option(50, "--limit", help="Max rows (newest first)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the artifacts as JSON."),
) -> None:
    """List the newest registered artifacts (id, kind, origin, first path, when)."""

    async def _run() -> list:
        from lhassa.core import artifacts, db

        await db.init_db()
        return await artifacts.list_artifacts(_config().instance_id, limit=limit)

    rows = asyncio.run(_run())
    if json_out:
        _dump_json(rows)
        return
    table = Table(title="Artifacts")
    for col in ("id", "kind", "origin", "first path", "creado_en"):
        table.add_column(col)
    for row in rows:
        paths = row.get("paths") or []
        table.add_row(
            str(row.get("id", "")),
            str(row.get("kind", "")),
            str(row.get("origin_department", "") or "—"),
            str(paths[0]) if paths else "—",
            str(row.get("creado_en", "")),
        )
    _out().print(table)


# ── show (one artifact) ──────────────────────────────────────────────────────────


def show_cmd(
    artifact_id: str = typer.Argument(..., help="Artifact id to inspect."),
    json_out: bool = typer.Option(False, "--json", help="Emit the artifact as JSON."),
) -> None:
    """Show one artifact's full row (paths, metadata, origin, episode)."""

    async def _run():
        from lhassa.core import artifacts, db

        await db.init_db()
        return await artifacts.get(_config().instance_id, artifact_id)

    row = asyncio.run(_run())
    if row is None:
        _err().print(f"No artifact {artifact_id!r} in the store.", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(row)
        return
    out = _out()
    out.print(f"id:                {row.get('id')}")
    out.print(f"kind:              {row.get('kind')}")
    out.print(f"origin_department: {row.get('origin_department') or '—'}")
    out.print(f"episode_id:        {row.get('episode_id') or '—'}")
    out.print(f"creado_en:         {row.get('creado_en')}")
    out.print("paths:")
    for p in row.get("paths") or []:
        out.print(f"  - {p}")
    out.print(f"metadata:          {json.dumps(row.get('metadata') or {}, ensure_ascii=False)}")


# ── registration ──────────────────────────────────────────────────────────────────

artifact_app.command("list")(list_cmd)
artifact_app.command("show")(show_cmd)
