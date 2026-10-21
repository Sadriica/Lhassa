# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the bare ``lhassa`` REPL shows: the welcome card, the slash-command
menu and the bottom toolbar.

* :data:`SLASH_COMMANDS` is the ONE table of REPL commands; the help text, the
  completer and the welcome hints all read it.
* :func:`welcome` renders a card: a greeting in the owner's local time, the two
  model seats, today's spend, the working directory, and what is waiting on the
  owner (calendar events, approvals, due tasks from ``personal.agenda``).
* :class:`SlashCompleter` opens a menu as soon as the line starts with ``/``.
* :func:`toolbar` is the status line under the prompt, the same read-model the
  TUI's status bar shows (``lhassa.views.status``).

Every read is local and $0; anything that fails or is slow (the calendar feed)
is skipped, never raised, so the REPL always opens.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from prompt_toolkit.completion import Completer, Completion
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lhassa.cli.theme import GOLD, GREEN, RED, STONE, WHITE
from lhassa.views import status as status_view

#: ``(command, argument hint, description)``, in menu order.
SLASH_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("/help", "", "show the commands"),
    ("/status", "", "the brain at a glance"),
    ("/agenda", "", "what is waiting on you today"),
    ("/approvals", "", "the pending approval queue"),
    ("/approve", "<id>", "approve one pending approval"),
    ("/deny", "<id>", "deny one pending approval"),
    ("/skills", "", "installed skills"),
    ("/tools", "", "self-written tools"),
    ("/usage", "", "tokens and cost this session"),
    ("/reset", "", "forget this conversation"),
    ("/clear", "", "clear the screen"),
    ("/exit", "", "leave (or Ctrl-D)"),
)

#: Aliases the loop accepts that the menu does not list.
ALIASES = {"/?": "/help", "/quit": "/exit", "/new": "/reset", "/overview": "/status"}

#: How long the welcome card waits for the agenda (the calendar feed may be remote).
AGENDA_TIMEOUT = 2.0


def help_markup() -> str:
    width = max(len(c) + (len(a) + 1 if a else 0) for c, a, _ in SLASH_COMMANDS)
    lines = ["[title]Lhassa[/] [dim]commands:[/]"]
    for cmd, arg, desc in SLASH_COMMANDS:
        label = f"{cmd} {arg}".strip()
        lines.append(f"  [accent]{label.ljust(width)}[/]  [dim]{desc}[/]")
    lines.append(
        "[dim]Anything else is a message. For power tools run e.g. "
        "[/][model]lhassa org tree[/][dim], [/][model]lhassa skills[/][dim], "
        "[/][model]lhassa tui[/][dim].[/]"
    )
    return "\n".join(lines)


def history_path() -> Path:
    """Where the REPL keeps its input history: next to the brain DB."""
    try:
        from lhassa.config import Config

        base = Path(Config.brain_db_path).expanduser().parent
    except Exception:
        base = Path.home() / ".lhassa"
    return base / "repl_history"


def _greeting(now: Optional[datetime] = None) -> str:
    hour = (now or datetime.now()).hour
    if hour < 5:
        return "Up late."
    if hour < 12:
        return "Good morning."
    if hour < 19:
        return "Good afternoon."
    return "Good evening."


def _short_path(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


async def _agenda_items() -> "dict | None":
    """Events, approval titles and due tasks; ``None`` if the agenda is unreadable."""
    try:
        from lhassa.config import Config
        from lhassa.personal import agenda as agenda_mod

        built = await asyncio.wait_for(agenda_mod.build(Config, horizon_hours=24), AGENDA_TIMEOUT)
        return {
            "events": list(built.events),
            "approvals": list(built.approvals),
            "tasks": [(t.action, t.due_at) for t in built.pending_tasks],
        }
    except Exception:
        return None


async def gather_welcome() -> dict:
    """The status snapshot plus the agenda, fetched together. Never raises."""
    snap, agenda = await asyncio.gather(status_view.gather(), _agenda_items())
    return {"status": snap, "agenda": agenda}


#: Task actions that are the owner's own (reminders); the rest of the queue is
#: Lhassa's internal upkeep (temple_run, digests) and stays out of the card.
OWNER_TASK_ACTIONS = frozenset({"send_reminder"})


def _line(*parts: tuple[str, str]) -> Text:
    return Text.assemble(*parts, no_wrap=True, overflow="ellipsis")


def _waiting_lines(agenda: "dict | None", status: dict) -> list[Text]:
    lines: list[Text] = []
    if agenda is None:
        count = status.get("approvals")
        if count:
            lines.append(_line((f"{count} approvals", f"bold {GOLD}"), ("  /approvals", STONE)))
        return lines
    for event in agenda["events"][:3]:
        lines.append(_line((event, WHITE)))
    approvals = agenda["approvals"]
    if approvals:
        lines.append(_line((f"{len(approvals)} approvals", f"bold {GOLD}"), ("  /approvals", STONE)))
        for title in approvals[:2]:
            lines.append(_line(("  " + title, STONE)))
    reminders = [due for action, due in agenda["tasks"] if action in OWNER_TASK_ACTIONS]
    for due in reminders[:3]:
        lines.append(_line(("reminder", WHITE), (f"  {due}" if due else "", STONE)))
    if len(reminders) > 3:
        lines.append(_line((f"+{len(reminders) - 3} more reminders", STONE)))
    return lines


def welcome(data: dict, *, width: int = 100, cwd: Optional[str] = None, version: str = "") -> Panel:
    """The welcome card for ``data`` (from :func:`gather_welcome`). Pure."""
    s = data.get("status") or {}
    fmt = status_view.format_duration

    seats = Table.grid(padding=(0, 2))
    seats.add_column(style=STONE, no_wrap=True)
    seats.add_column(no_wrap=True, overflow="ellipsis")
    seats.add_row("chat", Text.assemble((str(s.get("model") or "?"), f"bold {GOLD}"),
                                        (f"  {s.get('chat_provider') or ''}", STONE)))
    seats.add_row("beat", Text.assemble((str(s.get("heartbeat_seat") or "auto"), WHITE),
                                        (f"  last beat {fmt(s.get('beat_age'))} ago", STONE)))
    budget = int(s.get("budget") or 0)
    spend = Text(f"${float(s.get('cost_usd') or 0.0):.4f}", style=GREEN)
    if budget > 0:
        spend.append(f"  {round(float(s.get('pct_used') or 0.0) * 100)}% of today's budget", style=STONE)
    else:
        spend.append("  budget unlimited", style=STONE)
    seats.add_row("today", spend)
    seats.add_row("org", Text("live", style=GOLD) if s.get("org_live") else Text("offline", style=STONE))
    seats.add_row("here", Text(_short_path(cwd or os.getcwd()), style=WHITE))
    failing = s.get("health_failing")
    if failing:
        seats.add_row("health", Text(", ".join(failing), style=RED))

    left = Group(Text(_greeting(), style=f"bold {WHITE}"), Text(""), seats)

    waiting = _waiting_lines(data.get("agenda"), s)
    right_parts: list = [Text("Waiting on you", style=f"bold {GOLD}")]
    right_parts += waiting or [Text("Nothing. The wheel turns on its own.", style=STONE)]
    right_parts += [
        Text(""),
        Text("Try", style=f"bold {GOLD}"),
        _line(("/agenda", RED), ("  your day", STONE)),
        _line(("lhassa tui", RED), ("  the whole brain", STONE)),
    ]
    right = Group(*right_parts)

    if width >= 96:
        body = Table.grid(padding=(0, 4), expand=True)
        body.add_column(ratio=1)
        body.add_column(ratio=1)
        body.add_row(left, right)
    else:
        body = Group(left, Text(""), right)

    return Panel(
        body,
        title=Text.assemble(("ᕮ ", GOLD), ("Lhassa", f"bold {GOLD}")),
        title_align="left",
        subtitle=Text(version, style=STONE) if version else None,
        subtitle_align="right",
        border_style=GOLD,
        padding=(1, 2),
        width=min(width, 120),
    )


def hints() -> Text:
    return Text.assemble(
        ("  / ", RED), ("commands", STONE), ("  ·  ", STONE),
        ("↑ ", RED), ("history", STONE), ("  ·  ", STONE),
        ("Ctrl-C ", RED), ("cancels a turn", STONE), ("  ·  ", STONE),
        ("Ctrl-D ", RED), ("leaves", STONE),
    )


def agenda_markup(agenda: "dict | None") -> str:
    """``/agenda`` as Rich markup: the next 24 hours, owner items first."""
    if agenda is None:
        return "[dim]The agenda could not be read right now.[/]"
    lines = ["[title]Next 24 hours[/]"]
    if agenda["events"]:
        lines += [f"  [model]{e}[/]" for e in agenda["events"]]
    else:
        lines.append("  [dim]no calendar events[/]")
    reminders = [d for a, d in agenda["tasks"] if a in OWNER_TASK_ACTIONS]
    if reminders:
        lines.append("[title]Reminders[/]")
        lines += [f"  reminder [dim]{d}[/]" for d in reminders[:10]]
    if agenda["approvals"]:
        lines.append(f"[title]Approvals[/] [dim]({len(agenda['approvals'])})[/]")
        lines += [f"  {t}" for t in agenda["approvals"][:5]]
        if len(agenda["approvals"]) > 5:
            lines.append("  [dim]… /approvals for the rest[/]")
    upkeep = len(agenda["tasks"]) - len(reminders)
    if upkeep:
        lines.append(f"[dim]{upkeep} upkeep tasks of my own are scheduled too.[/]")
    return "\n".join(lines)


# ── prompt_toolkit pieces ──────────────────────────────────────────────────────

#: prompt_toolkit style classes for the toolbar roles and the completion menu.
TOOLBAR_STYLE = {
    "bottom-toolbar": f"noreverse bg:default {STONE}",
    "st.brand": f"bold {GOLD}",
    "st.label": STONE,
    "st.value": WHITE,
    "st.model": f"bold {GOLD}",
    "st.muted": STONE,
    "st.ok": GREEN,
    "st.warn": GOLD,
    "st.alert": RED,
    "st.bar-ok": GREEN,
    "st.bar-warn": GOLD,
    "st.bar-alert": RED,
    "st.bar-empty": "#3a4630",
    "completion-menu": "bg:#1c1813 #ece4d3",
    "completion-menu.completion.current": f"bg:#2a241b bold {GOLD}",
    "completion-menu.meta.completion": f"bg:#1c1813 {STONE}",
    "completion-menu.meta.completion.current": f"bg:#2a241b {WHITE}",
    "placeholder": STONE,
}


def toolbar_fragments(snap: dict) -> list[tuple[str, str]]:
    """The status read-model as prompt_toolkit ``(style, text)`` fragments."""
    out: list[tuple[str, str]] = []
    for role, text in status_view.segments(snap):
        out.append((f"class:st.{role}" if role else "", text))
    return out


class SlashCompleter(Completer):
    """Completes ``/commands`` with their description as the menu meta."""

    def get_completions(self, document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for cmd, arg, desc in SLASH_COMMANDS:
            if cmd.startswith(text):
                yield Completion(
                    cmd + (" " if arg else ""),
                    start_position=-len(text),
                    display=f"{cmd} {arg}".strip(),
                    display_meta=desc,
                )
