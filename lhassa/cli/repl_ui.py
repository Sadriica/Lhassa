# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the bare ``lhassa`` REPL shows: the welcome card, the slash-command
menu and the bottom toolbar.

* :data:`SLASH_COMMANDS` is the ONE table of REPL commands; the help text, the
  completer and the welcome hints all read it.
* :func:`welcome` is Lhassa introducing itself, in the first person and the
  owner's language (``digest_language``): the stupa mark, a greeting by the
  local hour, its last heartbeat and today's cost, the two model seats, the org
  mode, the working directory, a line of prayer flags, and what waits on the
  owner's word (calendar events, approvals, reminders from ``personal.agenda``).
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
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from lhassa.cli.theme import GOLD, GREEN, RED, STONE, WHITE
from lhassa.views import status as status_view

#: ``(command, argument hint, description)``, in menu order.
SLASH_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("/help", "", "show the commands"),
    ("/status", "", "the brain at a glance"),
    ("/agenda", "", "today, tomorrow and your commitments"),
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


# ── the voice: first person, the owner's language ──────────────────────────────

#: Lines the welcome speaks, English and Spanish (``digest_language`` picks; the
#: same signal ``persona.lead_phrases`` uses). Lhassa is an "it": the Spanish
#: phrasing avoids gendered adjectives on purpose.
VOICE: dict[str, dict[str, str]] = {
    "en": {
        "night": "Still up? So am I.",
        "morning": "Good morning. Here I am, awake.",
        "afternoon": "Good afternoon. Here I am, awake.",
        "evening": "Good evening. Here I am, awake.",
        "beat": "My last heartbeat was {age} ago; today has cost {cost}.",
        "beat_unknown": "No heartbeat yet today; nothing spent so far.",
        "seats": "I think with {chat} and keep watch with {beat}.",
        "org_live": "The org is live.",
        "org_resting": "The org is resting.",
        "budget": "{pct} of today's budget is spent.",
        "health": "Heads up: {checks} need a look.",
        "here": "We are in {cwd}.",
        "more": "+{n} more",
        "hint_commands": "commands",
        "hint_history": "history",
        "hint_cancel": "cancels a turn",
        "hint_sleep": "lets me sleep",
        "bye": "Going back to sleep. The heartbeat keeps watch.",
        "placeholder": "Ask me anything, or / for commands",
        "calendar": "Calendar",
        "today": "Today",
        "tomorrow": "Tomorrow",
        "free": "free",
        "all_day": "all day",
        "no_calendar": "No calendar connected (lhassa calendar add).",
        "calendar_slow": "The calendar did not answer in time.",
        "commitments": "Your commitments",
        "no_commitments": "Nothing pending. /remind or /goal on Telegram adds one.",
        "goal": "goal I keep watch on",
        "every_day": "every day",
        "every_days": "every {n} days",
        "every": "every {span}",
        "approvals_wait": "{n} approvals await your word",
    },
    "es": {
        "night": "¿Aún en pie? Yo también.",
        "morning": "Buenos días. Aquí sigo.",
        "afternoon": "Buenas tardes. Aquí sigo.",
        "evening": "Buenas noches. Aquí sigo.",
        "beat": "Mi último latido fue hace {age}; hoy llevo {cost}.",
        "beat_unknown": "Hoy no he latido todavía; nada gastado.",
        "seats": "Pienso con {chat} y vigilo con {beat}.",
        "org_live": "La org está en vivo.",
        "org_resting": "La org descansa.",
        "budget": "Llevo {pct} del presupuesto de hoy.",
        "health": "Ojo: {checks} necesitan una mirada.",
        "here": "Estamos en {cwd}.",
        "more": "+{n} más",
        "hint_commands": "comandos",
        "hint_history": "historial",
        "hint_cancel": "cancela un turno",
        "hint_sleep": "me deja dormir",
        "bye": "Vuelvo a dormir. El latido sigue de guardia.",
        "placeholder": "Pregúntame lo que sea, o / para comandos",
        "calendar": "Calendario",
        "today": "Hoy",
        "tomorrow": "Mañana",
        "free": "libre",
        "all_day": "todo el día",
        "no_calendar": "Sin calendario conectado (lhassa calendar add).",
        "calendar_slow": "El calendario no respondió a tiempo.",
        "commitments": "Tus compromisos",
        "no_commitments": "Nada pendiente. /remind o /goal en Telegram agregan uno.",
        "goal": "meta que vigilo",
        "every_day": "cada día",
        "every_days": "cada {n} días",
        "every": "cada {span}",
        "approvals_wait": "{n} aprobaciones esperan tu palabra",
    },
}

#: The mark: the dashboard's gold stupa with its monk-red finial, in cells.
STUPA: tuple[tuple[tuple[str, str], ...], ...] = (
    (("   ╻   ", RED),),
    (("  ▄█▄  ", GOLD),),
    ((" ▐▀▀▀▌ ", GOLD),),
    (("▄█████▄", GOLD),),
    (("▀▀▀▀▀▀▀", STONE),),
)

#: Prayer-flag colors, in the order the flag line repeats them (the house
#: palette stands in for the flags' blue, white, red, green and yellow).
FLAGS = (STONE, WHITE, RED, GREEN, GOLD)


def language() -> str:
    return status_view.language()


def voice(lang: Optional[str] = None) -> dict[str, str]:
    return VOICE.get(lang or language(), VOICE["en"])


def _greeting(now: Optional[datetime] = None, lang: str = "en") -> str:
    hour = (now or datetime.now()).hour
    key = "night" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 19 else "evening"
    return voice(lang)[key]


def _short_path(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


async def _days() -> "list[dict] | None":
    try:
        from lhassa.config import Config
        from lhassa.personal import agenda as agenda_mod

        return await asyncio.wait_for(agenda_mod.days(Config, count=2), AGENDA_TIMEOUT)
    except Exception:
        return None


async def _commitments() -> list:
    try:
        from lhassa.config import Config
        from lhassa.personal import agenda as agenda_mod

        return await agenda_mod.commitments(Config, limit=50)
    except Exception:
        return []


def _has_calendar() -> bool:
    try:
        from lhassa.personal.sources import AGENDA_DATED_SOURCES

        return len(AGENDA_DATED_SOURCES) > 0
    except Exception:
        return False


async def gather_welcome() -> dict:
    """Status, today and tomorrow, and the owner's commitments. Never raises."""
    snap, days, commitments = await asyncio.gather(status_view.gather(), _days(), _commitments())
    return {
        "status": snap,
        "days": days,
        "commitments": commitments,
        "calendar": _has_calendar(),
    }


def _line(*parts: tuple[str, str]) -> Text:
    return Text.assemble(*parts, no_wrap=True, overflow="ellipsis")


def flag_line(width: int) -> Text:
    """A string of prayer flags across ``width`` cells."""
    out = Text(no_wrap=True)
    cells = max(0, width)
    i = 0
    while len(out) + 3 <= cells:
        out.append("▬▬", style=FLAGS[i % len(FLAGS)])
        out.append(" ")
        i += 1
    return out


def _speech(s: dict, lang: str, cwd: str) -> list[Text]:
    v = voice(lang)
    fmt = status_view.format_duration
    lines = [Text(_greeting(lang=lang), style=f"bold {WHITE}")]
    cost = f"${float(s.get('cost_usd') or 0.0):.4f}"
    if s.get("beat_age") is None:
        lines.append(Text(v["beat_unknown"], style=STONE))
    else:
        lines.append(_styled(v["beat"], {"age": (fmt(s.get("beat_age")), WHITE), "cost": (cost, GREEN)}))
    lines.append(
        _styled(
            v["seats"],
            {
                "chat": (str(s.get("model") or "?"), f"bold {GOLD}"),
                "beat": (str(s.get("heartbeat_seat") or "auto"), WHITE),
            },
        )
    )
    tail = Text(v["org_live"] if s.get("org_live") else v["org_resting"],
                style=GOLD if s.get("org_live") else STONE)
    if int(s.get("budget") or 0) > 0:
        tail.append(" ")
        tail.append_text(
            _styled(v["budget"], {"pct": (f"{round(float(s.get('pct_used') or 0) * 100)}%", WHITE)})
        )
    lines.append(tail)
    failing = s.get("health_failing")
    if failing:
        lines.append(_styled(v["health"], {"checks": (", ".join(failing), f"bold {RED}")}, base=RED))
    lines.append(_styled(v["here"], {"cwd": (_short_path(cwd), WHITE)}))
    return lines


def _styled(template: str, fields: dict[str, tuple[str, str]], base: str = STONE) -> Text:
    """``template`` with ``{name}`` fields filled, each in its own style."""
    out = Text(no_wrap=True, overflow="ellipsis")
    rest = template
    while rest:
        start = rest.find("{")
        end = rest.find("}", start)
        if start < 0 or end < 0:
            out.append(rest, style=base)
            break
        out.append(rest[:start], style=base)
        value, style = fields.get(rest[start + 1 : end], ("", base))
        out.append(value, style=style)
        rest = rest[end + 1 :]
    return out


# ── dates in the owner's language (no locale dependency) ─────────────────────

_WEEKDAYS = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
}
_MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
}


def short_date(d, lang: str = "en") -> str:
    """``Wed 21 Oct`` / ``mié 21 oct``."""
    lang = lang if lang in _WEEKDAYS else "en"
    return f"{_WEEKDAYS[lang][d.weekday()]} {d.day} {_MONTHS[lang][d.month - 1]}"


def _every(seconds: "int | None", lang: str) -> str:
    if not seconds:
        return ""
    v = voice(lang)
    days = seconds / 86400
    if days >= 1 and days == int(days):
        return v["every_days"].format(n=int(days)) if days > 1 else v["every_day"]
    return v["every"].format(span=status_view.format_duration(seconds))


def _event_line(ev: dict, now: datetime, lang: str) -> Text:
    v = voice(lang)
    start = ev["start"]
    try:
        past = (ev.get("end") or start) < now
    except TypeError:
        past = False
    when = v["all_day"] if ev.get("all_day") else start.astimezone().strftime("%H:%M")
    text_style = STONE if past else WHITE
    out = _line(
        ("· " if past else "◆ ", STONE if past else RED),
        (when.ljust(5), STONE if past else f"bold {WHITE}"),
        ("  " + (ev.get("summary") or "?"), text_style),
    )
    if ev.get("location"):
        out.append("  " + ev["location"], style=STONE)
    return out


def calendar_lines(days: "list[dict] | None", *, has_calendar: bool, lang: str,
                   per_day: Optional[int] = 4, now: Optional[datetime] = None) -> list[Text]:
    """Today and tomorrow, each under its own dated heading."""
    v = voice(lang)
    if not has_calendar:
        return [Text(v["calendar"], style=f"bold {GOLD}"), Text(v["no_calendar"], style=STONE)]
    if days is None:
        return [Text(v["calendar"], style=f"bold {GOLD}"), Text(v["calendar_slow"], style=STONE)]
    now = now or datetime.now().astimezone()
    lines: list[Text] = []
    for i, day in enumerate(days):
        label = v["today"] if i == 0 else v["tomorrow"] if i == 1 else ""
        if lines:
            lines.append(Text(""))
        lines.append(_line((label, f"bold {GOLD}"), (f"  {short_date(day['date'], lang)}", STONE)))
        events = day["events"]
        if not events:
            lines.append(Text("  " + v["free"], style=STONE))
            continue
        shown = events if per_day is None else events[:per_day]
        if per_day is not None and i == 0:
            # Today: keep what is still ahead, past ones only while room is left.
            ahead = [e for e in events if (e.get("end") or e["start"]) >= now]
            behind = [e for e in events if e not in ahead]
            room = per_day - len(ahead)
            shown = (behind[-room:] if room > 0 else []) + ahead[:per_day]
        lines += [_event_line(e, now, lang) for e in shown]
        hidden = len(events) - len(shown)
        if hidden > 0:
            lines.append(Text("  " + v["more"].format(n=hidden), style=STONE))
    return lines


def commitment_lines(commitments: list, status: dict, *, lang: str,
                     limit: Optional[int] = 4) -> list[Text]:
    """What the owner asked Lhassa to remember or keep watch over, soonest first."""
    v = voice(lang)
    lines: list[Text] = [Text(v["commitments"], style=f"bold {GOLD}")]
    shown = commitments if limit is None else commitments[:limit]
    for c in shown:
        if c.due:
            local = c.due.astimezone()
            day, hour = short_date(local, lang), local.strftime("%H:%M")
        else:
            day, hour = "?", ""
        lines.append(_line(("◇ ", GOLD), (day.ljust(11), f"bold {WHITE}"), (hour.ljust(6), STONE),
                           (c.text or "?", WHITE)))
        extra = [v["goal"]] if c.kind == "goal" else []
        if c.every_seconds:
            extra.append(_every(c.every_seconds, lang))
        if extra:
            lines.append(_line((" " * 19 + "↻ " + " · ".join(extra), STONE)))
    if not commitments:
        lines.append(Text(v["no_commitments"], style=STONE))
    elif limit is not None and len(commitments) > limit:
        lines.append(Text("  " + v["more"].format(n=len(commitments) - limit), style=STONE))
    approvals = status.get("approvals")
    if approvals:
        lines.append(Text(""))
        lines.append(_line(("◆ ", RED), (v["approvals_wait"].format(n=approvals), f"bold {GOLD}"),
                           ("  /approvals", STONE)))
    return lines


def welcome(
    data: dict,
    *,
    width: int = 100,
    cwd: Optional[str] = None,
    version: str = "",
    lang: Optional[str] = None,
) -> Group:
    """The welcome for ``data`` (from :func:`gather_welcome`). Pure.

    No box: the stupa on the left, Lhassa speaking in the first person beside
    it, a line of prayer flags, then the calendar by day (today, tomorrow) and,
    beside it on a wide terminal, the owner's commitments.
    """
    lang = lang or language()
    s = data.get("status") or {}
    width = max(40, min(width, 120))

    speech = _speech(s, lang, cwd or os.getcwd())
    head = Table.grid(padding=(0, 3))
    head.add_column(no_wrap=True)
    head.add_column(no_wrap=True, overflow="ellipsis", max_width=width - 14)
    mark = [Text.assemble(*row) for row in STUPA]
    title = Text.assemble(("Lhassa", f"bold {GOLD}"), (f"  {version}" if version else "", STONE))
    rows = [title] + speech
    for i in range(max(len(mark), len(rows))):
        head.add_row(mark[i] if i < len(mark) else Text(""), rows[i] if i < len(rows) else Text(""))

    cal = Group(*calendar_lines(data.get("days"), has_calendar=bool(data.get("calendar")), lang=lang))
    com = Group(*commitment_lines(data.get("commitments") or [], s, lang=lang))
    if width >= 96:
        below = Table.grid(padding=(0, 4))
        below.add_column(width=(width - 8) * 11 // 20, no_wrap=True, overflow="ellipsis")
        below.add_column(width=(width - 8) * 9 // 20, no_wrap=True, overflow="ellipsis")
        below.add_row(cal, com)
        tail: list = [below]
    else:
        tail = [cal, Text(""), com]

    parts: list = [Text(""), head, Text(""), flag_line(width - 2), Text(""), *tail, Text("")]
    return Group(*[Padding(p, (0, 0, 0, 2)) for p in parts])


def hints(lang: Optional[str] = None) -> Text:
    v = voice(lang)
    sep = ("   ", STONE)
    return Text.assemble(
        ("  /", RED), (" " + v["hint_commands"], STONE), sep,
        ("↑", RED), (" " + v["hint_history"], STONE), sep,
        ("Ctrl-C", RED), (" " + v["hint_cancel"], STONE), sep,
        ("Ctrl-D", RED), (" " + v["hint_sleep"], STONE),
    )


async def gather_agenda() -> dict:
    """What ``/agenda`` shows: the welcome's data with nothing trimmed."""
    return await gather_welcome()


def agenda_view(data: dict, *, lang: Optional[str] = None) -> Group:
    """``/agenda``: today and tomorrow in full, then every commitment."""
    lang = lang or language()
    parts: list = [Text("")]
    parts += calendar_lines(data.get("days"), has_calendar=bool(data.get("calendar")),
                            lang=lang, per_day=None)
    parts.append(Text(""))
    parts += commitment_lines(data.get("commitments") or [], data.get("status") or {},
                              lang=lang, limit=None)
    parts.append(Text(""))
    return Group(*[Padding(p, (0, 0, 0, 2)) for p in parts])


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
    "st.bar-empty": STONE,
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
