# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa task``: manage tasks (standing goals, scheduled work).

Commands::

    lhassa task add <accion> [--objetivo O] [--params JSON]
                             [--due ISO8601|+Nh|+Nm] [--every hourly|daily|weekly|Ns]
                             [--think]
    lhassa task list [--estado E]
    lhassa task done <id>
    lhassa task reject <id>

``--think`` sets the judgment flag: any accion is allowed, the brain THINKS about
it on schedule rather than running a deterministic fixed action. Token cost applies
each time the task fires. Use for standing goals like "review my inbox daily".

Registered as a sub-app in ``lhassa.cli.app`` via::

    app.add_typer(task_cmds.task_app, name="task")
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli import capability_log as _capability_log

task_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage tasks and standing goals.",
)

# Convenience aliases for --every.
_RECURRENCE_ALIASES = {
    "hourly": "3600",
    "daily": "86400",
    "weekly": "604800",
}


def _out() -> Console:
    return Console()


def _err() -> Console:
    return Console(stderr=True)


def _config():
    from lhassa.config import Config
    return Config()


def _parse_recurrence(raw: str | None) -> Optional[str]:
    """Translate human recurrence strings into seconds-string or None."""
    if not raw:
        return None
    lo = raw.strip().lower()
    if lo in _RECURRENCE_ALIASES:
        return _RECURRENCE_ALIASES[lo]
    # Plain integer/float seconds.
    try:
        secs = int(float(lo.rstrip("s")))
        if secs > 0:
            return str(secs)
    except (ValueError, TypeError):
        pass
    return None


def _parse_due(raw: str | None) -> Optional[str]:
    """Parse --due: ISO 8601 or ``+Nh`` / ``+Nm`` shorthand, or None."""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("+"):
        suffix = s[1:].lower()
        try:
            if suffix.endswith("h"):
                delta = timedelta(hours=float(suffix[:-1]))
            elif suffix.endswith("m"):
                delta = timedelta(minutes=float(suffix[:-1]))
            elif suffix.endswith("s"):
                delta = timedelta(seconds=float(suffix[:-1]))
            else:
                delta = timedelta(seconds=float(suffix))
            return (datetime.now(timezone.utc) + delta).isoformat()
        except (ValueError, TypeError):
            _err().print(f"[red]Invalid --due shorthand {raw!r}. Use +Nh / +Nm / +Ns.[/red]")
            raise typer.Exit(code=1)
    # ISO 8601; validate by parsing.
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except (ValueError, TypeError):
        _err().print(f"[red]Invalid --due value {raw!r}. Use ISO 8601 or +Nh shorthand.[/red]")
        raise typer.Exit(code=1)


@task_app.command("add")
def task_add(
    accion: str = typer.Argument(..., help="Action name to mint."),
    objetivo: Optional[str] = typer.Option(None, "--objetivo", "-o", help="Target of the action."),
    params_json: Optional[str] = typer.Option(
        None, "--params", "-p", help="Extra params as a JSON object."
    ),
    due: Optional[str] = typer.Option(
        None, "--due", help="When to run: ISO 8601 timestamp or +Nh/+Nm shorthand."
    ),
    every: Optional[str] = typer.Option(
        None, "--every", help="Recurrence: hourly | daily | weekly | <seconds>."
    ),
    think: bool = typer.Option(
        False, "--think", help="Judgment flag: any accion allowed; the brain thinks each tick."
    ),
) -> None:
    """Mint a new task (standing goal or one-shot scheduled action).

    ``--think`` sets the judgment flag: any accion is accepted and the brain
    THINKS about it each time it fires rather than running a deterministic action.
    Without ``--think``, the accion must be in the registered ACTIONS registry.
    """
    params: dict = {}
    if params_json:
        try:
            params = json.loads(params_json)
            if not isinstance(params, dict):
                raise ValueError("params must be a JSON object")
        except (ValueError, TypeError) as exc:
            _err().print(f"[red]Invalid --params JSON: {exc}[/red]")
            raise typer.Exit(code=1)

    recurrence = _parse_recurrence(every)
    due_at = _parse_due(due)

    async def _run() -> None:
        from lhassa.core import actions as _actions
        from lhassa.core import db as _db
        from lhassa.core import dispatch as _dispatch
        from lhassa.security.trust import Origin, TrustLevel

        await _db.init_db()
        cfg = _config()
        instance_id = cfg.instance_id

        # Validate accion unless --think is set.
        if not think and accion not in _actions.ACTIONS:
            names = sorted(_actions.ACTIONS.keys())
            _err().print(
                f"[red]Unknown accion {accion!r}. Registered: {', '.join(names)}[/red]\n"
                "[dim]Use --think to allow any accion (costs tokens each tick).[/dim]"
            )
            raise typer.Exit(code=1)

        origin = Origin(trust=TrustLevel.HUMANO, actor_id="cli")
        task_id, estado = await _dispatch.mint_task(
            instance_id,
            accion,
            objetivo=objetivo or "",
            params=params,
            origin=origin,
            due_at=due_at,
            recurrence=recurrence,
            judgment=think,
        )
        if not task_id:
            _err().print(f"[red]{estado}[/red]")
            raise typer.Exit(code=1)

        out = _out()
        out.print(f"[green]✓[/green] Task [bold]{task_id}[/bold] minted (estado: {estado})")
        if recurrence:
            out.print(f"  [dim]Recurrence: every {recurrence}s[/dim]")
        if due_at:
            out.print(f"  [dim]Due: {due_at}[/dim]")
        if think:
            out.print("  [dim]Judgment: yes (brain will think each tick; token cost applies)[/dim]")

    asyncio.run(_run())
    _capability_log.record_sync("task add")


def _task_detail(row: dict, width: int = 46) -> str:
    """One line saying WHAT this task is, read off its params.

    Same idea as ``security/approval.py:describe`` and for the same reason: a
    column of bare action names makes 200 rows of ``temple_run`` and
    ``send_reminder`` indistinguishable, so the list cannot answer "is my Cohere
    reminder in there?". The payload shapes are known and few; anything
    unrecognized falls back to the first param value rather than showing
    nothing. Never raises: an odd row degrades to an empty cell.
    """
    try:
        params = row.get("params")
        if not isinstance(params, dict) or not params:
            return ""
        # The keys that actually carry the subject, in the order they are most
        # likely to be the point of the task.
        for key in ("text", "action", "objetivo", "query", "name", "flow_id", "message"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        else:
            first = next(iter(params.values()))
            text = str(first).strip()
        text = " ".join(text.split())
        return text if len(text) <= width else text[: width - 1] + "…"
    except Exception:
        return ""


@task_app.command("list")
def task_list(
    estado: Optional[str] = typer.Option(None, "--estado", "-e", help="Filter by estado."),
) -> None:
    """List tasks (newest first, cap 200)."""
    async def _run() -> list[dict]:
        from lhassa.core import db as _db
        await _db.init_db()
        return await _db.list_tasks(_config().instance_id, estado=estado, limit=200)

    rows = asyncio.run(_run())
    if not rows:
        _out().print("[dim]No tasks.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", max_width=18)
    table.add_column("Accion", no_wrap=True)
    table.add_column("Detail", no_wrap=True, overflow="ellipsis", max_width=46)
    table.add_column("Estado")
    table.add_column("Due at", style="dim")
    table.add_column("Recurrence", style="dim")
    for r in rows:
        table.add_row(
            r.get("id", "")[:16],
            r.get("accion", ""),
            _task_detail(r),
            r.get("estado", ""),
            r.get("due_at") or "",
            r.get("recurrence") or "",
        )
    _out().print(table)


@task_app.command("done")
def task_done(task_id: str = typer.Argument(..., help="Task id to mark as hecha.")) -> None:
    """Mark a task as completed (hecha)."""
    async def _run():
        from lhassa.core import db as _db
        await _db.init_db()
        await _db.set_task_state(_config().instance_id, task_id, "hecha")

    asyncio.run(_run())
    _out().print(f"[green]✓[/green] Task {task_id} marked hecha.")


@task_app.command("reject")
def task_reject(task_id: str = typer.Argument(..., help="Task id to reject.")) -> None:
    """Mark a task as rejected (rechazada)."""
    async def _run():
        from lhassa.core import db as _db
        await _db.init_db()
        await _db.set_task_state(_config().instance_id, task_id, "rechazada")

    asyncio.run(_run())
    _out().print(f"[yellow]✗[/yellow] Task {task_id} marked rechazada.")
