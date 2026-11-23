# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa observe``: run the observers, see what is alive, get the shell hook.

Commands::

    lhassa observe run [--system] [--sway] [--shell] [--files] [--interval S]
    lhassa observe status [--json]
    lhassa observe hook bash|zsh
    lhassa observe example-config

Nothing runs unless it is enabled by a flag or by ``enabled`` in the user file
(``LHASSA_OBSERVE_CONFIG``, default ``~/.lhassa/observe.json``).
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from importlib import resources
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, TABLE_KWARGS

observe_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Read-only observers (system, sway, shell, files) that write to the history.",
)

EXAMPLE = {
    "enabled": ["system", "sway", "shell"],
    "system": {"interval": 30},
    "sway": {"titles": False},
    "shell": {"full_command": False},
    "files": {"folders": [{"path": "~/notes", "recursive": True, "max_depth": 2}], "max_watches": 256},
}


def _out() -> Console:
    return Console(theme=LHASSA_THEME)


def _config():
    from lhassa.config import Config

    return Config()


def _load(config, **flags):
    from lhassa.observe.config import load_config, with_overrides

    return with_overrides(load_config(config.observe_config), **flags)


@observe_app.command("run")
def run(
    system: bool = typer.Option(False, "--system", help="Sample CPU, memory, temperatures, battery."),
    sway: bool = typer.Option(False, "--sway", help="Record window and workspace focus (sway IPC)."),
    shell: bool = typer.Option(False, "--shell", help="Receive command metadata from the shell hook."),
    files: bool = typer.Option(False, "--files", help="Watch the folders declared in the config file."),
    interval: Optional[float] = typer.Option(None, "--interval", help="System sample period in seconds (5 to 3600)."),
    sway_titles: bool = typer.Option(False, "--sway-titles", help="Also keep window titles (scrubbed, never-keep gated)."),
    shell_full: bool = typer.Option(False, "--shell-full", help="Also keep the full command line (scrubbed, never-keep gated)."),
    duration: Optional[float] = typer.Option(None, "--duration", help="Stop after this many seconds (default: run until stopped)."),
) -> None:
    """Run the enabled observers until interrupted."""
    from lhassa.observe.coordinator import Coordinator
    from lhassa.observe.shell import runtime_dir

    config = _config()
    enable = frozenset(n for n, on in (("system", system), ("sway", sway), ("shell", shell), ("files", files)) if on)
    cfg = _load(
        config, enable=enable, interval=interval,
        sway_titles=True if sway_titles else None, shell_full_command=True if shell_full else None,
    )
    if not cfg.enabled:
        _out().print("[warning]Nothing is enabled, so nothing is observed.[/] Pass --system, --sway, --shell or --files, or set `enabled` in the config file.")
        raise typer.Exit(0)
    coord = Coordinator(
        cfg, records_dir=config.records_dir, instance_id=config.instance_id,
        shell_dir=runtime_dir(config.observe_runtime_dir),
    )
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    _out().print(f"Observing: {', '.join(sorted(cfg.enabled))} (Ctrl-C to stop)")
    coord.run(stop, duration=duration)


@observe_app.command("status")
def status(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")) -> None:
    """Which observers are active and when each last wrote an event."""
    from lhassa.observe.config import OBSERVERS
    from lhassa.observe.coordinator import read_status

    config = _config()
    data = read_status(config.records_dir, config.instance_id)
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        return
    out = _out()
    out.print("Coordinator: " + ("[success]running[/]" if data.get("running") else "[warning]not running[/]"))
    if data.get("note"):
        out.print(data["note"])
    table = Table(**TABLE_KWARGS)
    for col in ("observer", "state", "events", "last event", "note"):
        table.add_column(col)
    rows = data.get("observers") or {}
    for name in OBSERVERS:
        row = rows.get(name, {"state": "off"})
        ts = row.get("last_event_ts")
        ago = "never" if not ts else f"{max(0, int(time.time() - ts))}s ago"
        table.add_row(name, str(row.get("state", "off")), str(row.get("events", "")), ago if row.get("state") != "off" else "", str(row.get("note", "")))
    out.print(table)
    for p in data.get("problems") or []:
        out.print(f"[warning]config:[/] {p}")


@observe_app.command("hook")
def hook(shell: str = typer.Argument(..., help="bash or zsh")) -> None:
    """Print the shell hook to source from your rc file (it is never installed for you)."""
    if shell not in ("bash", "zsh"):
        typer.echo("shell must be bash or zsh", err=True)
        raise typer.Exit(2)
    text = (resources.files("lhassa.observe") / "hooks" / f"observe.{shell}").read_text(encoding="utf-8")
    sys.stdout.write(text)


@observe_app.command("example-config")
def example_config() -> None:
    """Print an example observe.json (nothing is written)."""
    sys.stdout.write(json.dumps(EXAMPLE, indent=2) + "\n")
