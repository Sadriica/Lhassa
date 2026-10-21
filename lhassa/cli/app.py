# SPDX-License-Identifier: AGPL-3.0-or-later
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
from lhassa.cli import artifact_cmds as _artifact_cmds  # noqa: E402
from lhassa.cli import capabilities as _capabilities  # noqa: E402
from lhassa.cli import commands as _commands  # noqa: E402
from lhassa.cli import config_cmds as _config_cmds  # noqa: E402
from lhassa.cli import extra_cmds as _extra_cmds  # noqa: E402
from lhassa.cli import flow_cmds as _flow_cmds  # noqa: E402
from lhassa.cli import integrity_cmds as _integrity_cmds  # noqa: E402
from lhassa.cli import utility_cmds as _utility_cmds  # noqa: E402
from lhassa.cli import knowledge_cmds as _knowledge_cmds  # noqa: E402
from lhassa.cli import memory_cmds as _memory_cmds  # noqa: E402
from lhassa.cli import org_cmds as _org_cmds  # noqa: E402
from lhassa.cli import seed_cmds as _seed_cmds  # noqa: E402
from lhassa.cli import setup_cmds as _setup_cmds  # noqa: E402
from lhassa.cli import share_cmds as _share_cmds  # noqa: E402
from lhassa.cli import skills_cmds as _skills_cmds  # noqa: E402
from lhassa.cli import project_cmds as _project_cmds  # noqa: E402
from lhassa.cli import task_cmds as _task_cmds  # noqa: E402
from lhassa.cli import tools_cmds as _tools_cmds  # noqa: E402
from lhassa.cli.theme import LHASSA_THEME, cost_markup, prompt_fragments  # noqa: E402


def _console(*, stderr: bool = False) -> Console:
    """A Console wired with the Lhasa theme."""
    return Console(stderr=stderr, theme=LHASSA_THEME)


class _ChatDefaultGroup(TyperGroup):
    """Route an unrecognized first token to the implicit ``chat`` command.

    The CLI has power subcommands (tools/report/fleet/...) AND must keep its three
    legacy input modes; ``lhassa "question"``, ``echo x | lhassa``, and bare REPL.
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


def _run_sync(prompt: str, history=None) -> "agent.CliResult":
    """Run one turn, with a gold stderr spinner when stderr is an interactive terminal."""
    err = _console(stderr=True)
    if err.is_terminal:
        with err.status("[prompt]Thinking…[/]", spinner="dots", spinner_style="prompt"):
            return asyncio.run(agent.run_once(prompt, history=history))
    return asyncio.run(agent.run_once(prompt, history=history))


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
    "[title]Lhassa[/] [dim]commands:[/]\n"
    "  [accent]/help[/]    [dim]show this[/]\n"
    "  [accent]/status[/]  [dim]the brain at a glance (overview)[/]\n"
    "  [accent]/skills[/]  [dim]installed skills[/]\n"
    "  [accent]/tools[/]   [dim]self-written tools[/]\n"
    "  [accent]/usage[/]   [dim]tokens + cost this session[/]\n"
    "  [accent]/reset[/]   [dim]forget this conversation (start fresh)[/]\n"
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
    # The conversation, in process memory only (never written to disk): the REPL is a
    # conversation, not a row of unrelated one-shots. Trimmed to the last N turns in
    # agent._seed_messages, and dropped entirely on /reset.
    history: list[tuple[str, str]] = []
    # A clean, quiet header (no log spam; see _boot.quiet_logging).
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
        if line in ("/reset", "/new"):
            history.clear()
            err.print("[dim]conversation reset.[/]")
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
            result = _run_sync(line, history)
        except KeyboardInterrupt:
            # Ctrl-C cancels the TURN, not the session. It is BaseException, so the
            # generic handler below never saw it and one impatient Ctrl-C used to
            # throw the whole conversation away.
            err.print("[dim]· cancelled. The conversation is still here.[/]")
            continue
        except Exception as e:  # keep the REPL alive on any single-turn failure
            err.print(f"[error]✗[/] [accent]{e}[/]")
            continue
        out.print(Markdown(result.response))
        history.append(("user", line))
        history.append(("assistant", result.response))
        if result.truncated:
            err.print("[dim]· step cap reached; the work above may be unfinished.[/]")
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

    I am Lhassa. I live on this machine; I watch quietly, remember what
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

# Attach the `lhassa flows` sub-app (stored work-flows + the flow-templates gallery).
app.add_typer(_flow_cmds.flow_app, name="flows")

# Attach the `lhassa flow` sub-app (the singular lifecycle: create/edit/run/watch/logs).
app.add_typer(_flow_cmds.one_flow_app, name="flow")

# Attach the `lhassa artifacts` sub-app (registered artifacts; the delivery chain).
app.add_typer(_artifact_cmds.artifact_app, name="artifacts")

# Attach the `lhassa seed` sub-app (reproducible bootstrap seeds; cv, …).
app.add_typer(_seed_cmds.seed_app, name="seed")

# Attach `lhassa setup`: one command, one wizard: clone to a working brain.
_setup_cmds.register(app)

# Attach the `lhassa config` sub-app (runtime settings → ~/.lhassa/settings.env).
app.add_typer(_config_cmds.config_app, name="config")

# Attach the `lhassa share` sub-app (cross-instance resource sharing, Stream 6 Phase A).
app.add_typer(_share_cmds.share_app, name="share")

# Attach the `lhassa task` sub-app (task/standing-goal management).
app.add_typer(_task_cmds.task_app, name="task")

# ── lhassa project … (the first-class workspace entity) ─────────────────────
app.add_typer(_project_cmds.project_app, name="project")

app.add_typer(_integrity_cmds.integrity_app, name="integrity")

# ── lhassa utility … (the tribunal: which capabilities still SERVE the owner) ──
app.add_typer(_utility_cmds.utility_app, name="utility")

# Attach the `lhassa tools` sub-app (self-written tools: list + propose).
app.add_typer(_tools_cmds.tools_app, name="tools")

# Attach the `lhassa knowledge` sub-app (document perception: ingest/search/clear).
app.add_typer(_knowledge_cmds.knowledge_app, name="knowledge")

# Attach `lhassa memory` (search + add) and `lhassa onboard` (guided questionnaire).
_memory_cmds.register(app)

# ── capability packs: each mounts its own `lhassa <name>` sub-app ───────────
# (``extensions.register_cli_app``). Discovery rides on register_all(); a
# failing factory is skipped so one broken pack never breaks the CLI.
from lhassa import actionpack as _actionpack  # noqa: E402
from lhassa.extensions import CLI_APPS as _CLI_APPS  # noqa: E402
from lhassa.extensions import CLI_COMMANDS as _CLI_COMMANDS  # noqa: E402

_actionpack.register_all()
for _pack_cli_name, _pack_cli_factory in _CLI_APPS.items().items():
    try:
        app.add_typer(_pack_cli_factory(), name=_pack_cli_name)
    except Exception as _exc:  # noqa: BLE001
        print(f"lhassa: pack command {_pack_cli_name!r} failed to load: {_exc}", file=sys.stderr)
for _pack_cmd_name, _pack_cmd_factory in _CLI_COMMANDS.items().items():
    try:
        app.command(_pack_cmd_name)(_pack_cmd_factory())
    except Exception as _exc:  # noqa: BLE001
        print(f"lhassa: pack command {_pack_cmd_name!r} failed to load: {_exc}", file=sys.stderr)

# A pack command that is not installed must not fall through to the implicit
# chat (a paid think on the word "tui"): mount a hidden stub naming the pack.
_ABSENT_PACK_COMMANDS = {"tui": "lhassa-tui"}


def _absent_pack_stub(cmd: str, dist: str):
    def _stub() -> None:
        _console(stderr=True).print(
            f"[error]x[/] `lhassa {cmd}` comes from the {dist} pack, which is not installed. "
            f"Install it with `uv sync --package {dist}` (or `uv sync --all-packages`)."
        )
        raise typer.Exit(code=1)

    return _stub


for _absent_cmd, _absent_dist in _ABSENT_PACK_COMMANDS.items():
    if _absent_cmd not in _CLI_COMMANDS and _absent_cmd not in _CLI_APPS:
        app.command(_absent_cmd, hidden=True)(_absent_pack_stub(_absent_cmd, _absent_dist))


def main() -> None:
    app()
