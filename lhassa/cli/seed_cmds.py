# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa seed``: apply reproducible BOOTSTRAP seeds (the elastic seed registry).

A small, OFFLINE surface over :mod:`lhassa.seeds`: list the registered seeds and
apply one (or all). A *seed* recreates a whole working structure from code so a fresh
environment has it without hand-rebuilding; the ``cv`` seed (the "Impossible-to-reject
CV" pipeline) is the first, and future seeds show up here automatically as they
register (drop-in via ``@register_seed``; no edit to this file).

Applying a seed is idempotent and non-destructive: re-running never duplicates brains
and never overwrites an existing real base CV. Registered onto the main Typer ``app``
as the ``seed`` sub-app; see :mod:`lhassa.cli.app`.
"""

from __future__ import annotations

import json
import sys

import typer

from lhassa.cli.theme import LHASSA_THEME

seed_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Apply reproducible bootstrap seeds (cv, …); recreate a pipeline from code.",
)


def _out():
    from rich.console import Console

    return Console(theme=LHASSA_THEME)


def _err():
    from rich.console import Console

    return Console(stderr=True, theme=LHASSA_THEME)


def _dump_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _config():
    from lhassa.config import Config

    return Config()


def _print_summary(out, name: str, summary: dict) -> None:
    """Render one seed's summary in the Lhasa theme."""
    created = summary.get("brains_created") or []
    existing = summary.get("brains_existing") or []
    out.print(f"[brand]ᕮ seed[/] [title]{name}[/]")
    out.print(
        f"  brains created: [success]{', '.join(created) or '—'}[/]",
        markup=True,
    )
    out.print(f"  brains kept:    [dim]{', '.join(existing) or '—'}[/]", markup=True)
    if "flow_id" in summary:
        out.print(f"  flow:           [model]{summary.get('flow_id')}[/]")
    if "monitor" in summary:
        out.print(f"  monitor:        [model]{summary.get('monitor')}[/]")
    if "base_cv" in summary:
        label = summary.get("base_cv")
        style = "warn" if label == "placeholder" else "dim"
        note = " (placeholder written; replace with your real CV)" if label == "placeholder" else " (existing CV kept)"
        out.print(f"  base CV:        [{style}]{label}[/]{note}")
    for err in summary.get("errors") or []:
        out.print(f"  [error]✗[/] {err}", markup=True)


# ── list ───────────────────────────────────────────────────────────────────────────


def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the seeds as JSON."),
) -> None:
    """List the registered seeds and what each recreates."""
    from lhassa.seeds import list_seeds

    seeds = list_seeds()
    if json_out:
        _dump_json(seeds)
        return
    from rich.table import Table

    table = Table(title="Seeds")
    table.add_column("name")
    table.add_column("description")
    for s in seeds:
        table.add_row(str(s["name"]), str(s["description"]))
    _out().print(table)


# ── apply ──────────────────────────────────────────────────────────────────────────


def apply_cmd(
    name: str = typer.Argument(..., help="Seed name (see `lhassa seed list`)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the summary as JSON."),
) -> None:
    """Apply one seed; idempotent + non-destructive. Prints what it created/kept."""
    from lhassa.registry import NotRegistered
    from lhassa.seeds import apply_seed

    try:
        summary = apply_seed(name, _config())
    except NotRegistered as exc:
        _err().print(f"[error]✗[/] {exc}")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(summary)
        return
    _print_summary(_out(), name, summary)


# ── apply-all ──────────────────────────────────────────────────────────────────────


def apply_all_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit all summaries as JSON."),
) -> None:
    """Apply EVERY registered seed (idempotent). The full bootstrap for a fresh env."""
    from lhassa.seeds import apply_all

    result = apply_all(_config())
    if json_out:
        _dump_json(result)
        return
    out = _out()
    if not result.get("applied"):
        out.print("[dim]No seeds registered.[/]")
        return
    for name, summary in (result.get("results") or {}).items():
        _print_summary(out, name, summary)
        out.print()


# ── registration ─────────────────────────────────────────────────────────────────

seed_app.command("list")(list_cmd)
seed_app.command("apply")(apply_cmd)
seed_app.command("apply-all")(apply_all_cmd)
