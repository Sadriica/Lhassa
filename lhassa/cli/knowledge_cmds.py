# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa knowledge``: document perception from the terminal.

Mirrors the ``lhassa calendar``/``lhassa memory`` sub-app pattern: a small
Typer group over :mod:`lhassa.memory.knowledge`. $0, offline, no LLM; a
filesystem walk + the offline embedder + a local SQLite index.
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console

from lhassa.cli import capability_log as _capability_log
from lhassa.cli.theme import LHASSA_THEME

knowledge_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Index and search the owner's own documents (knowledge roots).",
)


def _out() -> Console:
    return Console(theme=LHASSA_THEME)


def _err() -> Console:
    return Console(stderr=True, theme=LHASSA_THEME)


def _dump_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _config():
    from lhassa.config import Config

    return Config()


def ingest_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the summary as JSON."),
) -> None:
    """Walk the configured knowledge roots and index new/changed files."""
    from lhassa.memory import knowledge

    cfg = _config()
    instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
    if not (getattr(cfg, "knowledge_roots", "") or "").strip():
        if json_out:
            _dump_json({"roots": 0, "message": "no knowledge roots set"})
            return
        _out().print(
            "[dim]No knowledge roots set; configure LHASSA_KNOWLEDGE_ROOTS "
            "to point Lhassa at your notes/papers.[/]"
        )
        return

    summary = asyncio.run(knowledge.ingest(instance_id, config=cfg))
    _capability_log.record_sync("knowledge ingest")
    if json_out:
        _dump_json(summary)
        return
    _out().print(
        f"[success]✓[/] indexed [model]{summary['files_indexed']}[/] file(s), "
        f"[model]{summary['chunks']}[/] chunk(s) across "
        f"[model]{summary['roots']}[/] root(s); "
        f"skipped [model]{summary['skipped_secrets']}[/] secret-shaped file(s)."
    )


def search_cmd(
    query: str = typer.Argument(..., help="Text to search your indexed documents for."),
    limit: int = typer.Option(5, "--limit", help="Max results."),
    json_out: bool = typer.Option(False, "--json", help="Emit the results as JSON."),
) -> None:
    """Search the owner's indexed documents; $0, offline cosine search."""
    import os as _os

    from lhassa.memory import knowledge

    cfg = _config()
    instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
    hits = asyncio.run(knowledge.search(instance_id, query, limit=limit, config=cfg))

    if json_out:
        _dump_json(hits)
        return

    out = _out()
    if not hits:
        out.print(f"[dim]No indexed documents match[/] [model]{query}[/][dim].[/]")
        return
    out.print(f"[title]Your documents matching[/] [model]{query}[/] [title]({len(hits)}):[/]")
    for h in hits:
        name = _os.path.basename(h.get("source_path", "")) or "?"
        snippet = (h.get("text") or "").replace("\n", " ").strip()
        if len(snippet) > 150:
            snippet = snippet[:147] + "…"
        out.print(f"  [dim]{name}[/]; {snippet}")


def clear_cmd(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Wipe the whole knowledge index (a re-ingest or a privacy reset)."""
    from lhassa.memory import knowledge

    if not yes:
        confirmed = typer.confirm("Clear the entire knowledge index?", default=False)
        if not confirmed:
            _err().print("[dim]Cancelled.[/]")
            raise typer.Exit(code=1)

    cfg = _config()
    instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
    deleted = asyncio.run(knowledge.clear(instance_id, config=cfg))
    if json_out:
        _dump_json({"deleted": deleted})
        return
    _out().print(f"[success]✓[/] cleared [model]{deleted}[/] indexed chunk(s).")


knowledge_app.command("ingest")(ingest_cmd)
knowledge_app.command("search")(search_cmd)
knowledge_app.command("clear")(clear_cmd)
