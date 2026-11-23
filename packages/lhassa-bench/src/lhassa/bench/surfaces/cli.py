# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa bench``: LhassaBench, the deterministic $0-scoring eval harness.

Commands::

    lhassa bench run [--subject S ...] [--fixture F ...]
    lhassa bench history [--fixture F] [--subject S] [--limit N]

``run`` walks the bundled fixture set (:mod:`lhassa.bench.loader`) against one
or more subjects (:mod:`lhassa.llm.provider` names; ``claude``/``codex``/
``opencode``/``default``/``local``) via :func:`lhassa.bench.runner.run_bench`,
scores every completion with the LLM-free árbitro
(:mod:`lhassa.agents.verify`), persists every row to ``bench_runs``, and prints
the compact per-subject table. With no ``--subject`` given it defaults to
whichever of ``claude``/``codex``/``default`` the box can actually run right
now (mirrors the fallback chain's own install-check; never assumes a
subscription CLI is present). ``history`` reads back past runs without
executing anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, TABLE_KWARGS

bench_app = typer.Typer(
    add_completion=False,
    help="LhassaBench: deterministic, $0-scoring eval harness over fixtures x subjects.",
)

# Candidate default subjects, in preference order; mirrors the routine
# fallback chain's own CLI-first bias (subscription CLIs before the metered
# cloud gateway, which defaults to the cheap/"deepseek"-class routine model;
# see ``Config.model_cheap``). Whichever of these actually resolves gets used.
_DEFAULT_SUBJECT_CANDIDATES = ("claude", "codex", "default")


def _out() -> Console:
    return Console(theme=LHASSA_THEME)


def _err() -> Console:
    return Console(stderr=True, theme=LHASSA_THEME)


def _config():
    from lhassa.config import Config

    return Config()


def _default_subjects(config) -> List[str]:
    """Whichever candidate subjects are actually runnable on this box."""
    from lhassa.bench.runner import subject_available

    available = [s for s in _DEFAULT_SUBJECT_CANDIDATES if subject_available(s, config)]
    return available or ["default"]


@bench_app.command("run")
def bench_run(
    subject: Optional[List[str]] = typer.Option(
        None, "--subject", "-s", help="Subject to run (repeatable). Default: whatever CLI is installed."
    ),
    fixture: Optional[List[str]] = typer.Option(
        None, "--fixture", "-f", help="Fixture id to restrict to (repeatable). Default: all."
    ),
    max_tokens: int = typer.Option(256, "--max-tokens", help="Cap on each completion."),
) -> None:
    """Run the bench and print a per-subject pass-rate/cost table."""
    from lhassa.bench.runner import run_bench, summarize
    from lhassa.core import db

    config = _config()
    subjects = list(subject) if subject else _default_subjects(config)
    fixtures = list(fixture) if fixture else None

    out = _out()
    out.print(f"[dim]LhassaBench: subjects={subjects} fixtures={fixtures or 'all'}[/dim]")

    async def _run() -> list[dict]:
        await db.init_db()
        return await run_bench(subjects, fixtures=fixtures, config=config, max_tokens=max_tokens)

    rows = asyncio.run(_run())
    if not rows:
        _err().print("[yellow]no rows produced (no fixtures/subjects resolved)[/yellow]")
        raise typer.Exit(code=1)

    table = Table(title="LhassaBench results", **TABLE_KWARGS)
    for col in ("subject", "n", "pass %", "avg metric", "cost usd"):
        table.add_column(col)
    for line in summarize(rows).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5:
            table.add_row(*parts[:5])
    out.print(table)

    failed = [r for r in rows if not r.get("passed")]
    if failed:
        out.print(f"[dim]{len(failed)}/{len(rows)} rows failed; see `lhassa bench history` for detail.[/dim]")


@bench_app.command("ingest")
def bench_ingest(
    paths: List[Path] = typer.Argument(
        ..., help="JSONL result files (e.g. research/results/*.jsonl) to map into bench_runs."
    ),
) -> None:
    """Ingest experiment result files into ``bench_runs`` (idempotent; see bench/ingest.py)."""
    from lhassa.bench.ingest import ingest_files
    from lhassa.core import db

    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        _err().print(f"[error]not a file: {', '.join(missing)}[/error]")
        raise typer.Exit(code=2)

    async def _run() -> dict:
        await db.init_db()
        return await ingest_files(paths, _config().instance_id)

    t = asyncio.run(_run())
    _out().print(
        f"ingested {t['inserted']} new rows from {t['files']} files "
        f"({t['duplicates']} already present, {t['skipped']} unreadable lines)"
    )


@bench_app.command("history")
def bench_history(
    fixture: Optional[str] = typer.Option(None, "--fixture", "-f", help="Filter by fixture id."),
    subject: Optional[str] = typer.Option(None, "--subject", "-s", help="Filter by subject."),
    limit: int = typer.Option(50, "--limit", help="Max rows to show."),
) -> None:
    """List past bench runs from ``bench_runs`` (newest first)."""
    from lhassa.core import db

    async def _load() -> list[dict]:
        await db.init_db()
        return await db.list_bench_runs(
            _config().instance_id, fixture_id=fixture, subject=subject, limit=limit
        )

    rows = asyncio.run(_load())
    out = _out()
    if not rows:
        out.print("[dim]no bench runs recorded yet; run `lhassa bench run` first.[/dim]")
        return

    table = Table(title="LhassaBench history", **TABLE_KWARGS)
    for col in ("fixture_id", "subject", "passed", "metric", "cost_usd", "created_at"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["fixture_id"]),
            str(r["subject"]),
            "[success]yes[/success]" if r["passed"] else "[error]no[/error]",
            "" if r["metric"] is None else f"{r['metric']:.2f}",
            f"{r['cost_usd']:.4f}",
            str(r["created_at"]),
        )
    out.print(table)


__all__ = ["bench_app"]
