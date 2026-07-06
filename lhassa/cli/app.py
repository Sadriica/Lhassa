"""
Lhassa terminal CLI.

One binary, three input modes:
  - ``lhassa "question"``                  one-shot from an argument
  - ``echo data | lhassa``                 one-shot from piped stdin
  - ``lhassa "summarize" < file``          argument + piped stdin (appended)
  - ``lhassa`` (interactive terminal)      a small REPL

Answer goes to stdout; the spinner, the dim token/cost line, and diagnostics go
to stderr, so output stays clean when piped. ``--json`` emits a single machine
-readable object instead of pretty output.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import List, Optional

import click
import typer
from rich.console import Console
from rich.markdown import Markdown
from typer.core import TyperGroup

# Silence loguru's default DEBUG sink BEFORE importing the heavy lhassa modules
# (they emit the pipecat banner / "Tools registered" / gateway DEBUG at import time).
from lhassa.cli._boot import quiet_logging

quiet_logging()

from lhassa.cli import agent  # noqa: E402  (must follow quiet_logging)
from lhassa.cli import capabilities as _capabilities  # noqa: E402
from lhassa.cli import commands as _commands  # noqa: E402
from lhassa.cli import config_cmds as _config_cmds  # noqa: E402
from lhassa.cli import extra_cmds as _extra_cmds  # noqa: E402
from lhassa.cli import org_cmds as _org_cmds  # noqa: E402
from lhassa.cli import seed_cmds as _seed_cmds  # noqa: E402
from lhassa.cli import skills_cmds as _skills_cmds  # noqa: E402
from lhassa.cli import task_cmds as _task_cmds  # noqa: E402
from lhassa.cli import tools_cmds as _tools_cmds  # noqa: E402
from lhassa.cli.theme import LHASSA_THEME, cost_markup, prompt_fragments  # noqa: E402


def _console(*, stderr: bool = False) -> Console:
    """A Console wired with the Lhasa theme."""
    return Console(stderr=stderr, theme=LHASSA_THEME)


class _ChatDefaultGroup(TyperGroup):
    """Route an unrecognized first token to the implicit ``chat`` command.

    The CLI has power subcommands (tools/report/fleet/...) AND must keep its three
    legacy input modes — ``lhassa "question"``, ``echo x | lhassa``, and bare REPL.
    A plain Typer group would reject ``lhassa "free text"`` with "No such command".
    Here, when the first token doesn't match a registered subcommand, we fall back
    to the hidden ``chat`` command so the free-text prompt still works. (A prompt
    whose first word IS a subcommand name should be quoted as one argument.)
    """

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.exceptions.UsageError:
            return super().resolve_command(ctx, ["chat", *args])


app = typer.Typer(cls=_ChatDefaultGroup, add_completion=False, no_args_is_help=False)


def _print_cost(err: Console, result: "agent.CliResult") -> None:
    err.print(
        cost_markup(
            result.model,
            result.input_tokens,
            result.output_tokens,
            result.cost_usd,
        )
    )


def _run_sync(prompt: str) -> "agent.CliResult":
    """Run one turn, with a gold stderr spinner when stderr is an interactive terminal."""
    err = _console(stderr=True)
    if err.is_terminal:
        with err.status("[prompt]Thinking…[/]", spinner="dots", spinner_style="prompt"):
            return asyncio.run(agent.run_once(prompt))
    return asyncio.run(agent.run_once(prompt))


def _emit(result: "agent.CliResult", json_out: bool) -> None:
    if json_out:
        obj = {
            "response": result.response,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
        }
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
        return

    # Console auto-disables color/styling when stdout is not a TTY.
    out = _console()
    err = _console(stderr=True)
    out.print(Markdown(result.response))
    _print_cost(err, result)


_REPL_HELP = (
    "[title]Lhassa[/] [dim]— commands:[/]\n"
    "  [accent]/help[/]    [dim]show this[/]\n"
    "  [accent]/status[/]  [dim]the brain at a glance (overview)[/]\n"
    "  [accent]/skills[/]  [dim]installed skills[/]\n"
    "  [accent]/tools[/]   [dim]self-written tools[/]\n"
    "  [accent]/usage[/]   [dim]tokens + cost this session[/]\n"
    "  [accent]/clear[/]   [dim]clear the screen[/]\n"
    "  [accent]/exit[/]    [dim]quit (or Ctrl-D)[/]\n"
    "[dim]Anything else is a message. For power tools run e.g. "
    "[/][model]lhassa org tree[/][dim], [/][model]lhassa skills[/][dim], "
    "[/][model]lhassa capabilities[/][dim].[/]"
)


def _repl() -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    out = _console()
    err = _console(stderr=True)
    session: PromptSession = PromptSession(history=InMemoryHistory(), multiline=False)
    prompt_text, prompt_style = prompt_fragments()

    totals = {"in": 0, "out": 0, "cost": 0.0}
    # A clean, quiet header (no log spam — see _boot.quiet_logging).
    err.print()
    err.print("  [brand]ᕮ Lhassa[/]  [dim]· quietly awake. Ask me anything.[/]")
    err.print("  [dim]/help · /exit (Ctrl-D) · /usage[/]")
    err.print()

    while True:
        try:
            line = session.prompt(prompt_text, style=prompt_style)
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line in ("/help", "/?"):
            err.print(_REPL_HELP)
            continue
        if line == "/clear":
            out.clear()
            continue
        if line in ("/status", "/overview"):
            try:
                err.print(_extra_cmds.overview_text())
            except Exception as e:  # a degraded subsystem must not kill the REPL
                err.print(f"[error]✗[/] [accent]{e}[/]")
            continue
        if line == "/skills":
            try:
                err.print(_extra_cmds.skills_text())
            except Exception as e:
                err.print(f"[error]✗[/] [accent]{e}[/]")
            continue
        if line == "/tools":
            try:
                err.print(_extra_cmds.tools_text())
            except Exception as e:
                err.print(f"[error]✗[/] [accent]{e}[/]")
            continue
        if line == "/usage":
            err.print(
                f"[dim]session ·[/] [tokens]{totals['in']}+{totals['out']} tok[/] "
                f"[dim]·[/] [cost]${totals['cost']:.4f}[/]"
            )
            continue
        try:
            result = _run_sync(line)
        except Exception as e:  # keep the REPL alive on any single-turn failure
            err.print(f"[error]✗[/] [accent]{e}[/]")
            continue
        out.print(Markdown(result.response))
        totals["in"] += result.input_tokens
        totals["out"] += result.output_tokens
        totals["cost"] += result.cost_usd
        _print_cost(err, result)

    err.print("[dim]bye.[/]")


def _chat_entry(prompt_text: str, json_out: bool) -> None:
    """The default behavior: one-shot (arg and/or piped stdin) or interactive REPL."""
    stdin_piped = not sys.stdin.isatty()
    stdin_text = sys.stdin.read() if stdin_piped else ""

    parts = [p for p in (prompt_text, stdin_text.strip()) if p]
    full = "\n\n".join(parts)

    if full:
        result = _run_sync(full)
        _emit(result, json_out)
        return

    # No prompt and nothing piped.
    if sys.stdout.isatty():
        _repl()
        return

    err = _console(stderr=True)
    err.print("[error]✗[/] No prompt given. Pass text as an argument or pipe it via stdin.")
    raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    """Build the brain, rent the body.

    I am Lhassa. I live on this machine — I watch quietly, remember what
    matters, and think only when it is worth the spend. Most of my day costs
    nothing; the wheel turns on its own.

    Ask me something in plain words, or use a subcommand. Piped text I read
    as a question; given nothing, I sit with you in a REPL."""
    if ctx.invoked_subcommand is not None:
        return
    # No subcommand: the legacy bare/piped behavior (REPL or stdin one-shot).
    _chat_entry("", False)


@app.command(hidden=True)
def chat(
    prompt: Optional[List[str]] = typer.Argument(
        None,
        metavar="[PROMPT]",
        help="Prompt text. If omitted (and nothing is piped in), starts an interactive REPL.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit one JSON object {response, model, input_tokens, output_tokens, cost_usd} to stdout.",
    ),
) -> None:
    """Ask Lhassa something from the terminal."""
    prompt_text = " ".join(prompt).strip() if prompt else ""
    _chat_entry(prompt_text, json_out)


# Attach the power subcommands (tools/approvals/approve/deny/report/memory/fleet).
_commands.register(app)

# Attach the action commands (status/overview, version, serve, beat, watch).
_extra_cmds.register(app)

# Attach `lhassa capabilities` (alias `doctor`): surface what's wired + on/off.
app.command("capabilities")(_capabilities.capabilities_cmd)
app.command("doctor")(_capabilities.capabilities_cmd)

# Attach the `lhassa org` management sub-app (topology: 1..N brains + hierarchy).
app.add_typer(_org_cmds.org_app, name="org")
app.add_typer(_skills_cmds.skills_app, name="skills")

# Attach the `lhassa seed` sub-app (reproducible bootstrap seeds — cv, …).
app.add_typer(_seed_cmds.seed_app, name="seed")

# Attach the `lhassa config` sub-app (runtime settings → ~/.lhassa/settings.env).
app.add_typer(_config_cmds.config_app, name="config")

# Attach the `lhassa task` sub-app (task/standing-goal management).
app.add_typer(_task_cmds.task_app, name="task")

# Attach the `lhassa tools` sub-app (self-written tools: list + propose).
app.add_typer(_tools_cmds.tools_app, name="tools")


def main() -> None:
    app()
