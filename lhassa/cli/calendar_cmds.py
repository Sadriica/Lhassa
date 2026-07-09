"""``lhassa calendar`` — manage named calendar feeds from the terminal.

Mirrors the ``lhassa flows``/``lhassa memory`` sub-app pattern: a small Typer
group over :mod:`lhassa.calendar.store` (add/list/remove). No network, no LLM —
just edits to the JSON store; the feed itself is fetched lazily by ``/today``.
"""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, TABLE_KWARGS

calendar_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage named calendar feeds (add/list/remove).",
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


def _mask_url(url: str) -> str:
    url = (url or "").strip()
    if len(url) <= 40:
        return url
    return url[:40] + "…"


def add_cmd(
    name: str = typer.Argument(..., help="A short name for this calendar, e.g. Work."),
    url: str = typer.Argument(..., help="Its published .ics feed url."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Add (or replace, by name) a named calendar feed."""
    from lhassa.calendar import store as _cal_store

    ok = _cal_store.add_calendar(name, url, config=_config())
    if not ok:
        _err().print(
            f"[error]✗[/] couldn't add {name!r} — check the url starts with "
            "http:// or https://."
        )
        raise typer.Exit(code=1)
    if json_out:
        _dump_json({"added": name})
        return
    _out().print(f"[success]✓[/] calendar added: {name}")


def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the calendars as JSON."),
) -> None:
    """List every configured calendar (name + a masked url)."""
    from lhassa.calendar import store as _cal_store

    items = _cal_store.all_feeds(_config())
    if json_out:
        _dump_json(items)
        return

    out = _out()
    if not items:
        out.print("[dim]No calendars configured yet.[/]")
        return

    table = Table(title="Calendars", **TABLE_KWARGS)
    table.add_column("name")
    table.add_column("url")
    for it in items:
        table.add_row(str(it.get("name", "")), _mask_url(it.get("url", "")))
    out.print(table)


def remove_cmd(
    name: str = typer.Argument(..., help="The calendar name to remove."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Remove a named calendar feed."""
    from lhassa.calendar import store as _cal_store

    ok = _cal_store.remove_calendar(name, config=_config())
    if not ok:
        _err().print(f"[error]✗[/] no calendar named {name!r}.")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json({"removed": name})
        return
    _out().print(f"[success]✓[/] calendar removed: {name}")


calendar_app.command("add")(add_cmd)
calendar_app.command("list")(list_cmd)
calendar_app.command("remove")(remove_cmd)
