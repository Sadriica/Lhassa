# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core system commands: /start /help /status /version /config /report.

Moved verbatim out of the former ``lhassa/notify/commands.py`` — see the
package ``__init__.py`` for where every other command handler now lives.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from loguru import logger

from ._registry import COMMANDS, register_command
from ._shared import _config, _health_line, _oops, _routine_provider_model, _today_spend

__all__ = ["DESCRIPTIONS", "menu_entries"]


@register_command("start")
async def _cmd_start(args: str, *, config=None) -> str:
    """Lhassa introduces itself — first person, the monk's register (see
    doc/narrative.md). Short, warm, gender-neutral, pointing at /help."""
    return (
        "I am Lhassa. I live on your machine — I watch quietly, remember what "
        "matters, and think only when it is worth the spend. Speak to me in "
        "plain words and I will answer; /help shows everything I can do."
    )


# One-line description for each built-in command, grouped for /help. A command
# registered by a plugin but missing here still appears — see _cmd_help's final
# "also available" line — so a drop-in command can never vanish from /help.
_HELP_GROUPS: list[Tuple[str, list[str]]] = [
    ("Chat & memory", ["ai", "moa", "recall", "memory", "knowledge", "remind", "event", "checkin", "today", "tomorrow"]),
    ("Calendars", ["calendar", "calendars"]),
    ("Decisions", ["approvals", "approve", "deny"]),
    ("Watch & goals", ["watch", "watches", "unwatch", "goal", "temple"]),
    ("CV", ["cvnewbase", "cvbase"]),
    ("Coding", ["code"]),
    ("System", ["status", "report", "capabilities", "tools", "skills", "org", "fleet", "version"]),
]

DESCRIPTIONS: dict[str, str] = {
    "start": "Introduce myself.",
    "help": "This catalog.",
    "ai": "<question> — I search the web AND answer it specifically (not just links).",
    "moa": "<question> — ask a panel of models (not just one) and synthesize their answers; costs more, use for high-stakes decisions.",
    "recall": "<text> — search past episodes.",
    "memory": "<query> — search durable facts.",
    "knowledge": "<query> — search your indexed notes/papers (document perception).",
    "checkin": "<text> — save a personal morning/evening note.",
    "remind": "<what> a las 5pm | in 30 min — a timed reminder + a calendar file.",
    "event": "<what> a las 5pm | in 30 min — just the calendar file, no reminder.",
    "calendar": (
        "add <name> <url> | remove <name> | primary <name> | sync | view <when> — "
        "manage your named calendars, the master-calendar reconcile, and a "
        "$0 deterministic 'what's on my calendar' lookup."
    ),
    "calendars": "List your configured calendars (name + masked url; marks the primary).",
    "today": "Personal agenda for today.",
    "tomorrow": "Personal agenda for tomorrow.",
    "approvals": "List what's waiting on your decision.",
    "approve": "<id> — approve a pending action.",
    "deny": "<id> — deny a pending action.",
    "watch": "<pattern> -> <accion> — add a watch rule.",
    "watches": "List configured watch rules.",
    "unwatch": "<index> — remove a watch rule.",
    "goal": (
        "<text> [--every daily|hourly] [--verify command:<cmd>|regex:<pattern>] "
        "[--turns N] — schedule a recurring think, optionally bounded + "
        "verify-terminated."
    ),
    "temple": (
        "<instruction> | schedule — internal work / the temple's own clock "
        "(e.g. 'distill', 'curate skills', 'sync calendar', 'derive world')."
    ),
    "cvnewbase": "Arm a base-CV replacement.",
    "cvbase": "Show the base CV on file.",
    "code": "<task> — launch a coding agent on your files (asks for your approval first).",
    "status": "Provider, health and today's spend.",
    "report": "[days] — spend summary (default 7d).",
    "capabilities": "What I have wired and what's on.",
    "tools": "The tools I've written myself, and their state.",
    "skills": "The skills installed on me.",
    "org": "The org chart — brains and how they hang together.",
    "fleet": "Known machines, last check-in, desired state.",
    "version": "My version, runtime and key toggles.",
    "config": "[key [value]] — read or change a runtime setting.",
    "idea": "<text|link> — the geshe's aides investigate it into a corpus draft.",
}


def menu_entries() -> list[tuple[str, str]]:
    """The bot-menu command list for Telegram's ``setMyCommands`` — one source.

    Built from the SAME registry + descriptions ``/help`` renders, so the menu
    can never drift from what actually answers: every described command first
    (curated order), then any registered-but-undescribed command (a plugin's,
    for example) with a generic line. Telegram constraints applied here:
    lowercase ``a-z0-9_`` names only, descriptions clamped to 3–256 chars.
    """
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _desc(text: str) -> str:
        text = " ".join((text or "").split()) or "A Lhassa command."
        if len(text) < 3:
            text = f"{text} — a Lhassa command."
        return text[:256]

    registered = set(COMMANDS.names())
    for name, desc in DESCRIPTIONS.items():
        if name in registered and re.fullmatch(r"[a-z0-9_]{1,32}", name):
            entries.append((name, _desc(desc)))
            seen.add(name)
    for name in sorted(registered - seen):
        if re.fullmatch(r"[a-z0-9_]{1,32}", name):
            entries.append((name, "A Lhassa command — /help describes it."))
    return entries


@register_command("help")
async def _cmd_help(args: str, *, config=None) -> str:
    """A grouped catalog of every command, kept in sync with COMMANDS at runtime.

    Renders :data:`_HELP_GROUPS` with their :data:`DESCRIPTIONS`; any command
    registered (by core or a plugin) but absent from both still shows up on a
    trailing line, so a drop-in command can never vanish from /help.
    """
    try:
        registered = set(COMMANDS.names())
        lines = [
            "I'm Lhassa. Talk to me in plain words and I'll answer through my "
            "brain — a live-web question gets researched and I'll reply again "
            "once I have it.",
            "",
            "Here is what my commands do:",
        ]
        seen: set = set()
        for title, names in _HELP_GROUPS:
            present = [n for n in names if n in registered]
            if not present:
                continue
            lines.append("")
            lines.append(f"{title}:")
            for n in present:
                seen.add(n)
                lines.append(f"  /{n} — {DESCRIPTIONS.get(n, '')}".rstrip())
        extra = sorted(n for n in registered if n not in seen)
        if extra:
            lines.append("")
            lines.append("Also available: " + ", ".join("/" + n for n in extra))
        lines.append("")
        lines.append("I think locally and cheaply, so chatting with me is light on cost.")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /help failed: {exc}")
        return "I'm Lhassa. Send me a message and I'll answer, or try /start."


@register_command("status")
async def _cmd_status(args: str, *, config=None) -> str:
    """A compact, LLM-free system status: routine provider+model, health, spend."""
    try:
        cfg = _config(config)
        provider_label, model = _routine_provider_model(cfg)
        lines = [
            "Lhassa status",
            f"Provider: {provider_label}",
            f"Model: {model}",
        ]
        health = _health_line()
        if health:
            lines.append(health)
        spend = await _today_spend(cfg)
        if spend is not None:
            lines.append(f"Spend today: ${spend:.4f}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /status failed: {exc}")
        return _oops("read my own status")


async def _ledger_window_cost(config, days: int) -> Optional[Tuple[float, float]]:
    """(today_cost_usd, window_cost_usd) from the gateway ledger, or ``None``.

    Reads the ledger DB directly (same override as :func:`_today_spend`) so this
    stays LLM-free and testable against a temp DB — never constructs a provider.
    """
    try:
        from datetime import datetime, timedelta, timezone
        from pathlib import Path

        from lhassa._vendor import aiosqlite

        override = getattr(config, "llm_gateway_db_path", None)
        path = Path(override) if override else Path.home() / ".lhassa" / "llm_gateway.db"
        if not path.exists():
            return None

        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        cutoff = (now - timedelta(days=max(days, 0))).isoformat()
        async with aiosqlite.connect(path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM ledger WHERE ts LIKE ?",
                (today + "%",),
            ) as cur:
                today_row = await cur.fetchone()
            async with db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM ledger WHERE ts >= ?",
                (cutoff,),
            ) as cur:
                window_row = await cur.fetchone()
        today_cost = float(today_row[0]) if today_row else 0.0
        window_cost = float(window_row[0]) if window_row else 0.0
        return today_cost, window_cost
    except Exception as exc:
        logger.debug(f"commands: /report ledger read failed: {exc}")
        return None


@register_command("report")
async def _cmd_report(args: str, *, config=None) -> str:
    """``/report [days]`` — spend summary: today + a trailing window (default 7d).

    Reads the gateway ledger directly, LLM-free. No ledger yet → a plain "$0 so
    far" line rather than an error.
    """
    days_raw = (args or "").strip()
    days = 7
    if days_raw:
        try:
            days = max(int(days_raw), 1)
        except ValueError:
            return "Usage: /report [days] — a whole number of days, default 7."

    cfg = _config(config)
    result = await _ledger_window_cost(cfg, days)
    if result is None:
        return f"Spend today: $0.0000\nLast {days}d: $0.0000 (no ledger yet)."
    today_cost, window_cost = result
    return f"Spend today: ${today_cost:.4f}\nLast {days}d: ${window_cost:.4f}"


@register_command("config")
async def _cmd_config(args: str, *, config=None) -> str:
    """``/config [key [value]]`` — read/change a runtime setting, LLM-free.

    The CLI-parity door onto the settings service: the same EDITABLE allowlist,
    coercion, provider guard, live-apply and ``settings.env`` persistence that
    ``lhassa config`` and the dashboard panel use. Secrets stay masked and are
    not settable here (they live in ``.env``, never in settings.env). Setting
    happens INSIDE the poller process, so a knob like ``knowledge_roots`` is
    live for the very next chat-minted action without a restart.
    """
    try:
        from lhassa import settings as _settings

        text = (args or "").strip()
        cfg = _config(config)
        if not text:
            rows = _settings.effective(cfg)["settings"]
            lines = ["Editable settings (`/config <key> <value>` to change):"]
            for row in rows:
                mark = " *" if row.get("overridden") else ""
                lines.append(f"- {row['key']} = {row['value']}{mark}")
            lines.append("(* = overridden; secrets are masked and not settable here)")
            return "\n".join(lines)

        parts = text.split(maxsplit=1)
        key = parts[0].strip()
        if len(parts) == 1:
            row = _settings.get(key, cfg)
            if row.get("error"):
                return f"No editable setting {key!r} — see /config for the list."
            lines = [
                f"{row['key']} = {row['value']}"
                + (" (overridden)" if row.get("overridden") else ""),
                f"default: {row['default']}",
            ]
            if row.get("options"):
                lines.append("options: " + ", ".join(str(o) for o in row["options"]))
            if row.get("help"):
                lines.append(str(row["help"]))
            return "\n".join(lines)

        result = _settings.set(key, parts[1].strip())
        if not result.get("applied"):
            return f"Couldn't set {key!r}: {result.get('error', 'unknown error')}"
        reply = f"✅ {key} = {result.get('value')}"
        if result.get("restart_needed"):
            reply += f"\n(needs restart: {result.get('restart_hint', '')})"
        return reply
    except Exception as exc:
        logger.warning(f"commands: /config failed: {exc}")
        return _oops("change that setting")


@register_command("version")
async def _cmd_version(args: str, *, config=None) -> str:
    """``/version`` — version, runtime and key config toggles, LLM-free.

    Reuses :func:`lhassa.cli.extra_cmds.version_info` — the same source
    ``lhassa version`` prints — so the two surfaces never drift apart.
    """
    try:
        from lhassa.cli.extra_cmds import version_info

        info = version_info()
        toggles = info.get("toggles") or {}
        flags = ", ".join(f"{k}={'on' if v else 'off'}" for k, v in toggles.items())
        lines = [
            f"Lhassa {info.get('version')} · Python {info.get('python')}",
            f"Instance: {info.get('instance_id')}",
        ]
        if flags:
            lines.append(flags)
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /version failed: {exc}")
        return _oops("read my version")
