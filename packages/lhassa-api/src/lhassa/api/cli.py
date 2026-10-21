# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa serve``: run the dashboard + REST API (Ctrl-C stops it).

Mounted as a top-level command by core's ``cli/app.py`` through
``extensions.register_cli_command("serve", ...)`` (see ``lhassa.api.pack``).
FastAPI and uvicorn are imported only when the command actually runs.
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from lhassa.cli.theme import LHASSA_THEME


def _serve(host: str, port: int) -> None:
    """Boot the dashboard + REST API (blocks until Ctrl-C). Factored out so tests
    can patch the bind away and only exercise arg parsing."""
    from lhassa.api.server import run_api_server

    asyncio.run(run_api_server(host=host, port=port))


def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(7766, "--port", help="Bind port."),
) -> None:
    """Run the dashboard + REST API (Ctrl-C stops it)."""
    err = Console(stderr=True, theme=LHASSA_THEME)
    display = "localhost" if host in ("0.0.0.0", "::") else host
    err.print(
        f"[brand]ᕮ Lhassa[/] [dim]· dashboard + API at[/] "
        f"[model]http://{display}:{port}[/]  [dim](Ctrl-C to stop)[/]"
    )
    try:
        _serve(host, port)
    except KeyboardInterrupt:
        err.print("[dim]stopped.[/]")
