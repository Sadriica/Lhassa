# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa memory``: search durable facts, add one, and a guided onboarding.

Three surfaces over the grande's typed facts (:mod:`lhassa.memory.facts`):

  - ``lhassa memory <query> [--json]``: the pre-existing substring/tag search
    (unchanged in behavior; still lives in :mod:`lhassa.cli.commands` as
    ``memory_cmd``, re-registered here as the hidden default subcommand so the
    legacy call shape keeps working).
  - ``lhassa memory add "<text>" [--tipo dato|directiva] [--tags a,b] [--pin]
    [--confianza 0.9]``: write one fact through :func:`lhassa.memory.facts.add_fact`.
  - ``lhassa onboard``: a short guided questionnaire (~5 questions) that seeds
    the grande with the first durable facts about the owner. Interactive only
    (a non-TTY invocation exits cleanly, doing nothing).

``memory`` is a small Typer GROUP (like ``lhassa org``/``lhassa skills``) rather
than a single command, but the group must not break ``lhassa memory <query>``;
the pre-existing call shape with no subcommand name. A custom group class
(mirroring ``lhassa.cli.app``'s ``_ChatDefaultGroup``) falls back to the hidden
``search`` command whenever the first token isn't a known subcommand name.
"""

from __future__ import annotations

import asyncio
import sys

import click
import typer
from rich.console import Console
from typer.core import TyperGroup

from lhassa.cli import capability_log as _capability_log
from lhassa.cli import commands as _commands
from lhassa.cli.theme import LHASSA_THEME


def _out() -> Console:
    return Console(theme=LHASSA_THEME)


def _err() -> Console:
    return Console(stderr=True, theme=LHASSA_THEME)


class _MemoryDefaultGroup(TyperGroup):
    """Route an unrecognized first token to the hidden ``search`` command.

    Mirrors ``lhassa.cli.app._ChatDefaultGroup``: ``lhassa memory add ...``
    dispatches to the real ``add`` subcommand; ``lhassa memory <query>`` (any
    other first token) falls back to ``search <query> ...`` so the legacy
    call shape never breaks.
    """

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.exceptions.UsageError:
            return super().resolve_command(ctx, ["search", *args])


memory_app = typer.Typer(
    cls=_MemoryDefaultGroup,
    add_completion=False,
    no_args_is_help=True,
    help="Search durable facts, or add a new one.",
)


# ``search`` reuses the exact pre-existing implementation (commands.memory_cmd);
# same behavior, same tests; just registered as a (hidden) subcommand so the
# group can also carry ``add``.
memory_app.command("search", hidden=True)(_commands.memory_cmd)


def add_cmd(
    text: str = typer.Argument(..., help="The fact text to remember."),
    tipo: str = typer.Option("dato", "--tipo", help="dato | directiva"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags."),
    pin: bool = typer.Option(False, "--pin", help="Pin this fact (always loaded)."),
    confianza: float = typer.Option(1.0, "--confianza", help="Confidence, 0..1."),
    json_out: bool = typer.Option(False, "--json", help="Emit the new fact as JSON."),
) -> None:
    """Add a durable fact to the grande (append-only)."""
    tipo_norm = (tipo or "").strip().lower()
    if tipo_norm not in ("dato", "directiva"):
        _err().print(f"[error]✗[/] --tipo must be 'dato' or 'directiva', got {tipo!r}.")
        raise typer.Exit(code=2)

    text = (text or "").strip()
    if not text:
        _err().print("[error]✗[/] give the fact some text to remember.")
        raise typer.Exit(code=2)

    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    from lhassa.config import Config
    from lhassa.memory import facts

    fact = asyncio.run(
        facts.add_fact(
            Config.instance_id,
            tipo_norm,
            text,
            pinned=pin,
            tags=tag_list,
            procedencia="cli",
            confianza=confianza,
        )
    )
    _capability_log.record_sync("memory add")
    if json_out:
        _commands._dump_json(
            {"id": fact.id, "tipo": fact.tipo, "contenido": fact.contenido, "pinned": fact.pinned}
        )
        return
    _out().print(f"[success]✓[/] fact added [model]{fact.id}[/]")


memory_app.command("add")(add_cmd)


# ── graph / entities; the deterministic knowledge graph over the grande ─────


def graph_cmd(
    query: str = typer.Argument(..., help="Entity/term to look up, e.g. 'sezzle'."),
    limit: int = typer.Option(10, "--limit", help="Max facts/episodes/terms to show."),
    json_out: bool = typer.Option(False, "--json", help="Emit the raw result as JSON."),
) -> None:
    """Answer 'what do I know about X?' from stored facts/episodes; $0, no model."""
    from lhassa.config import Config
    from lhassa.memory import graph

    result = asyncio.run(graph.what_do_i_know(Config.instance_id, query, limit=limit))

    if json_out:
        _commands._dump_json(result)
        return

    out = _out()
    if not result["facts"] and not result["episodes"]:
        out.print(f"[dim]Nothing known about[/] [model]{query}[/] [dim]yet.[/]")
        return

    out.print(f"[title]What I know about[/] [model]{query}[/]")
    if result["first_seen"]:
        span = result["first_seen"]
        if result["last_seen"] and result["last_seen"] != result["first_seen"]:
            span += f" → {result['last_seen']}"
        out.print(f"[dim]seen: {span}[/]")

    if result["facts"]:
        out.print("\n[title]Facts:[/]")
        for f in result["facts"]:
            pin = " [warn]★[/]" if f.get("pinned") else ""
            tags = ", ".join(f.get("tags") or [])
            tags_s = f" [dim]({tags})[/]" if tags else ""
            out.print(f"  - ({f['tipo']}) {f['contenido']}{tags_s}{pin}")

    if result["episodes"]:
        out.print("\n[title]Episodes:[/]")
        for ep in result["episodes"]:
            out.print(f"  - [{ep.get('creado_en', '')}] {ep.get('resultado', '')}")

    if result["related_terms"]:
        out.print("\n[title]Related:[/] " + ", ".join(result["related_terms"]))


def entities_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the raw list as JSON."),
) -> None:
    """List the distinct entities the brain tracks; a map of what it knows."""
    from lhassa.config import Config
    from lhassa.memory import graph

    rows = asyncio.run(graph.entities(Config.instance_id))

    if json_out:
        _commands._dump_json(rows)
        return

    out = _out()
    if not rows:
        out.print("[dim]No entities tracked yet.[/]")
        return

    from rich.table import Table
    from lhassa.cli.theme import TABLE_KWARGS

    table = Table(title="Entities", **TABLE_KWARGS)
    for col in ("term", "mentions", "last_seen", "source"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["term"]), str(r["mentions"]), str(r["last_seen"] or "—"), str(r["source"]))
    out.print(table)


memory_app.command("graph")(graph_cmd)
memory_app.command("entities")(entities_cmd)


# ── reindex; re-embed everything with the CURRENT embedder ──────────────────


def reindex_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Re-embed all facts and cached answers with the current embedder.

    Run this after switching the embedder backend/model (e.g. enabling Ollama
    with ``pull``ed model, or moving off the offline hashing embedder); vectors
    stored under a different embedder are cosine-garbage against a new one, so
    this brings every stored vector back in sync. Best-effort per row: a single
    row failing never aborts the batch.
    """
    from lhassa.config import Config
    from lhassa.memory.embeddings import reindex_all

    result = asyncio.run(reindex_all(Config.instance_id, Config))

    if json_out:
        _commands._dump_json(result)
        return

    out = _out()
    out.print(f"[title]Reindexed with[/] [model]{result['embedder']}[/]")
    out.print(f"  facts:   [success]{result['facts']}[/] re-embedded")
    answers_line = f"  answers: [success]{result['answers']}[/] re-embedded"
    if result.get("answers_failed"):
        answers_line += f", [error]{result['answers_failed']}[/] failed"
    out.print(answers_line)


memory_app.command("reindex")(reindex_cmd)


# ── onboard; a short guided questionnaire seeding the first owner facts ──────

# Each entry: (prompt text, tipo, pinned).
_ONBOARD_QUESTIONS: list[tuple[str, str, bool]] = [
    ("Who are you; name, role, any context worth knowing?", "dato", False),
    ("What are you currently working on?", "dato", False),
    ("How do you like my answers; style, length, tone?", "directiva", False),
    ("Your working hours and timezone?", "dato", False),
    ("Any hard limits; things I must never do?", "directiva", True),
]


async def _existing_contents(instance_id: str) -> list[str]:
    from lhassa.memory import facts

    resolved = facts.resolve(await facts.load_facts(instance_id))
    return [f.contenido for f in resolved]


def _already_known(answer: str, existing: list[str]) -> bool:
    low = answer.strip().lower()
    if not low:
        return False
    return any(low in e.lower() or e.lower() in low for e in existing if e)


async def _write_onboard_fact(instance_id: str, tipo: str, text: str, pinned: bool):
    from lhassa.memory import facts

    return await facts.add_fact(
        instance_id,
        tipo,
        text,
        pinned=pinned,
        tags=["owner", "onboard"],
        procedencia="onboard",
    )


def onboard_cmd() -> None:
    """A guided questionnaire (~5 questions) that seeds the first owner facts.

    Skippable per-question (empty answer = skip, nothing written). Dedupes
    against what's already known (substring match) before writing. Interactive
    only; a non-TTY invocation (piped/no stdin) prints a short hint and exits.
    """
    out = _out()
    if not sys.stdin.isatty():
        out.print(
            "[dim]Run[/] [model]lhassa onboard[/] [dim]in an interactive terminal "
            "to answer a few questions.[/]"
        )
        raise typer.Exit(code=1)

    from lhassa.config import Config

    instance_id = Config.instance_id
    existing = asyncio.run(_existing_contents(instance_id))

    out.print("[title]A few questions[/] [dim](press Enter on any of them to skip).[/]")
    learned = 0
    for question, tipo, pinned in _ONBOARD_QUESTIONS:
        try:
            answer = input(f"{question} ").strip()
        except (EOFError, KeyboardInterrupt):
            out.print()
            break
        if not answer:
            continue
        if _already_known(answer, existing):
            out.print("[dim]I already carry that; no need to say it twice.[/]")
            continue
        asyncio.run(_write_onboard_fact(instance_id, tipo, answer, pinned))
        existing.append(answer)
        learned += 1

    if learned:
        out.print(
            f"[success]✓[/] I've taken in {learned} thing{'s' if learned != 1 else ''} "
            "about you; quietly filed, and I'll carry them from here."
        )
    else:
        out.print(
            "[success]✓[/] Nothing new to file today; I already carry what matters."
        )


def register(app: "typer.Typer") -> None:
    """Attach ``lhassa memory`` (group) and ``lhassa onboard`` to the main app."""
    app.add_typer(memory_app, name="memory")
    app.command("onboard")(onboard_cmd)
