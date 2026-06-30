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

from lhassa.cli import agent
from lhassa.cli import commands as _commands
from lhassa.cli import org_cmds as _org_cmds


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


def _cost_line(result: "agent.CliResult") -> str:
    return (
        f"{result.model} · {result.input_tokens}+{result.output_tokens} tokens "
        f"· ${result.cost_usd:.4f}"
    )


def _run_sync(prompt: str) -> "agent.CliResult":
    """Run one turn, with a stderr spinner when stderr is an interactive terminal."""
    err = Console(stderr=True)
    if err.is_terminal:
        with err.status("Thinking…", spinner="dots"):
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
    out = Console()
    err = Console(stderr=True)
    out.print(Markdown(result.response))
    err.print(_cost_line(result), style="dim")


def _repl() -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    out = Console()
    err = Console(stderr=True)
    session: PromptSession = PromptSession(history=InMemoryHistory(), multiline=False)

    totals = {"in": 0, "out": 0, "cost": 0.0}
    err.print("Lhassa CLI — /exit or Ctrl-D to quit, /usage for this session's tokens.", style="dim")

    while True:
        try:
            line = session.prompt("lhassa› ")
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/usage":
            err.print(
                f"Session usage: {totals['in']}+{totals['out']} tokens · ${totals['cost']:.4f}",
                style="dim",
            )
            continue
        try:
            result = _run_sync(line)
        except Exception as e:  # keep the REPL alive on any single-turn failure
            err.print(f"Error: {e}", style="red")
            continue
        out.print(Markdown(result.response))
        totals["in"] += result.input_tokens
        totals["out"] += result.output_tokens
        totals["cost"] += result.cost_usd
        err.print(_cost_line(result), style="dim")

    err.print("Bye.", style="dim")


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

    err = Console(stderr=True)
    err.print("No prompt given. Pass text as an argument or pipe it via stdin.", style="red")
    raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    """Lhassa — ask it something, or use a subcommand (tools, approvals, report,
    memory, fleet). With no subcommand it reads a piped prompt or starts a REPL."""
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

# Attach the `lhassa org` management sub-app (topology: 1..N brains + hierarchy).
app.add_typer(_org_cmds.org_app, name="org")


def main() -> None:
    app()
