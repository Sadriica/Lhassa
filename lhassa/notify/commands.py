"""Slash commands for the inbound Telegram chat — a modular command registry.

The owner's standing principle is that Lhassa must be **fully elastic and highly
modular**: every swap point sits behind an interface + a registry, and new
capabilities are **drop-in** (add a module, never edit core). Chat commands are
one such swap point: today ``/start`` + ``/help`` + ``/status``, tomorrow whatever
a plugin registers — each is just another entry in :data:`COMMANDS`.

This module defines:

* :data:`COMMANDS` — the module-level :class:`~lhassa.registry.Registry` of command
  handlers, plus :func:`register_command`, a thin decorator/direct-call wrapper so a
  plugin can drop in a new command **without editing core**;
* :func:`parse` — split a raw message into ``(command | None, args)``. A message is
  a command iff it starts with ``/``; the leading ``/`` is stripped, the first token
  is the command name (Telegram's ``/cmd@BotName`` form is normalised to ``cmd``),
  and the remainder is ``args``. A non-slash message yields ``(None, text)``;
* :func:`dispatch` — run a known command and return its reply text; return a short
  "unknown command" hint for an unrecognised ``/command``; return ``None`` for a
  plain-language message so the caller can let the **brain** answer it.

A command handler is an ``async (args: str, *, config=None) -> str`` callable. Like
the rest of :mod:`lhassa.notify`, every handler is **fully defensive** — it wraps
its work and returns a friendly fallback rather than raising, so a command can never
crash the inbound poll loop. Nothing here calls an LLM: commands are answered
locally and cheaply.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from loguru import logger

from lhassa.registry import Registry

__all__ = [
    "COMMANDS",
    "register_command",
    "parse",
    "dispatch",
]


# The command registry — the single swap point for chat slash commands. The item
# is an ``async (args, *, config=None) -> str`` handler.
COMMANDS: Registry = Registry("command")


def register_command(name: str, handler=None, *, override: bool = False):
    """Register a command handler under ``name`` — a wrapper over :data:`COMMANDS`.

    Usable as a **decorator** (``@register_command("help")`` — ``handler`` omitted,
    the decorated coroutine is registered and returned unchanged) or as a **direct
    call** (``register_command("help", handler)``). A duplicate ``name`` raises
    unless ``override=True`` — so two plugins can never silently clobber each other.
    The name is normalised to lower-case so ``/Help`` and ``/help`` are the same.
    """
    return COMMANDS.register(name.strip().lower(), handler, override=override)


def parse(text: str) -> Tuple[Optional[str], str]:
    """Split ``text`` into ``(command | None, args)``.

    A message is a command iff its first non-space character is ``/``. The leading
    ``/`` is stripped, the first whitespace-delimited token is the command name, and
    the rest (stripped) is ``args``. Telegram's ``/cmd@BotName`` form is normalised
    by dropping the ``@BotName`` suffix; the name is lower-cased. Anything that is
    not a slash command yields ``(None, text)`` so the caller can route it onward.
    """
    if not isinstance(text, str):
        return None, ""

    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, text

    body = stripped[1:].strip()
    if not body:
        # A bare "/" is not a command — let the brain handle it.
        return None, text

    parts = body.split(maxsplit=1)
    name = parts[0]
    args = parts[1].strip() if len(parts) > 1 else ""

    # Telegram addresses a bot as "/cmd@BotName" in group chats — drop the suffix.
    if "@" in name:
        name = name.split("@", 1)[0]

    name = name.lower()
    if not name:
        # e.g. "/@BotName" — no actual command name.
        return None, text
    return name, args


# Short hint returned for an unrecognised "/command".
_UNKNOWN = "Unknown command /{name} — try /help."


async def dispatch(text: str, *, config=None) -> Optional[str]:
    """Route a message to its command handler.

    Returns the handler's reply text for a known command; a short "unknown command"
    hint for an unrecognised ``/command``; and ``None`` for a plain-language message
    (the signal: let the brain answer it). Never raises — a misbehaving handler
    degrades to a friendly fallback string.
    """
    name, args = parse(text)
    if name is None:
        # Not a command → let the brain answer.
        return None

    handler = COMMANDS.get_or_none(name)
    if handler is None:
        return _UNKNOWN.format(name=name)

    try:
        return await handler(args, config=config)
    except Exception as exc:  # a command must never crash the poll loop
        logger.warning(f"commands: handler {name!r} failed: {exc}")
        return "That one stumbled on me — try it again in a moment, or /help for the map."


# ── helpers (LLM-free, never raise) ───────────────────────────────────────────



def _oops(what: str) -> str:
    """A degraded-path reply in Lhassa's voice — calm, owned, with a next step.

    Every LLM-free command that fails says it the same way: first person, no
    alarm, no blame, and an invitation to retry. One voice, one place.
    """
    return f"I couldn't {what} just now. Give it a moment and try again."


def _config(config):
    """Return ``config`` or a freshly loaded default. Lazy import keeps this light."""
    if config is not None:
        return config
    from lhassa.config import Config

    return Config()


def _routine_provider_model(config) -> Tuple[str, str]:
    """Derive the active *routine* provider label + model — LLM-free, no model call.

    Mirrors :func:`lhassa.llm.router.pick_provider` / ``pick_model`` for the routine
    (heartbeat) path off plain config, **without** constructing a provider or making
    a network call. Returns ``(provider_label, model_name)``.
    """
    import shutil

    heartbeat = (getattr(config, "heartbeat_provider", "auto") or "auto").lower()
    local_url = (getattr(config, "local_model_url", "") or "").strip()
    cloud_model = getattr(config, "model", "") or "cloud"

    def _opencode_model() -> str:
        return (getattr(config, "opencode_model", "") or "") or cloud_model

    def _local_model() -> str:
        return (getattr(config, "local_model", "") or "") or cloud_model

    if heartbeat == "cloud":
        return "cloud", cloud_model
    if heartbeat == "opencode":
        return "opencode", _opencode_model()
    if heartbeat == "local" or (heartbeat == "auto" and local_url):
        if local_url:
            return "local", _local_model()
        # heartbeat='local' but unconfigured → cloud fallback (mirrors the router).
        return "cloud", cloud_model
    if heartbeat == "auto" and not local_url:
        # auto + no local backend → prefer the opencode CLI when its binary exists.
        opencode_bin = (getattr(config, "opencode_bin", "") or "").strip()
        if opencode_bin and shutil.which(opencode_bin):
            return "opencode", _opencode_model()
    return "cloud", cloud_model


def _health_line() -> Optional[str]:
    """A one-line health summary from :func:`lhassa.health.checks.run_all`.

    LLM-free; returns ``None`` (omit the line) if anything goes wrong.
    """
    try:
        from lhassa.health import checks

        digest = checks.run_all(notify=False)
        worst = digest.get("worst_level", "info")
        ok = "ok" if digest.get("ok") else "needs attention"
        return f"Health: {worst} ({ok})"
    except Exception as exc:
        logger.debug(f"commands: health summary unavailable: {exc}")
        return None


async def _today_spend(config) -> Optional[float]:
    """Today's LLM spend (USD) from the cost ledger — the same data /api/llm/limits
    reads. Defensive: returns ``None`` (omit the line) when the ledger is absent or
    unreadable. Reads the ledger DB directly so no model call is made.
    """
    try:
        from datetime import datetime, timezone
        from pathlib import Path

        from lhassa._vendor import aiosqlite

        override = getattr(config, "llm_gateway_db_path", None)
        path = Path(override) if override else Path.home() / ".lhassa" / "llm_gateway.db"
        if not path.exists():
            return None

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM ledger WHERE ts LIKE ?",
                (today + "%",),
            ) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row else 0.0
    except Exception as exc:
        logger.debug(f"commands: today's spend unavailable: {exc}")
        return None


# ── built-in commands ─────────────────────────────────────────────────────────


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
    ("Chat & memory", ["ai", "recall", "memory", "remind", "event", "checkin", "today", "tomorrow"]),
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
    "recall": "<text> — search past episodes.",
    "memory": "<query> — search durable facts.",
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
    "goal": "<text> [--every daily|hourly] — schedule a recurring think.",
    "temple": "<instruction> — hand internal work to my own inner department.",
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


@register_command("cvnewbase")
async def _cmd_cvnewbase(args: str, *, config=None) -> str:
    """Arm a base-CV replacement — the reply text for ``/cvnewbase``.

    The arming side-effect (marking this chat so its next upload replaces the base CV)
    lives in the inbound poller, which has the chat id; this handler owns only the
    instruction text, so ``/cvnewbase`` appears in ``/help`` and the command menu.
    """
    return (
        "Send your new base CV as the next file (a .tex is best — a PDF stores "
        "extracted text)."
    )


@register_command("cvbase")
async def _cmd_cvbase(args: str, *, config=None) -> str:
    """Show the base CV on file (``cv_largo.tex``) — LLM-free, never raises.

    Reports whether a base CV exists, its size and first line, and how to update it
    (arm ``/cvnewbase``, then send a document). Reads the file only — no model call.
    """
    try:
        from lhassa import cv

        info = cv.base_info()
        if not info.get("exists"):
            return (
                "No base CV on file yet. Use /cvnewbase, then send your CV as a "
                "document to save it as the base; /cv <job posting> will tailor it."
            )
        first = info.get("first_line") or ""
        first_line = f"\nFirst line: {first}" if first else ""
        return (
            f"Base CV on file ({info.get('chars', 0)} chars).{first_line}\n"
            f"Path: {info.get('path')}\n"
            "Use /cvnewbase and then send a document to replace it."
        )
    except Exception as exc:
        logger.warning(f"commands: /cvbase failed: {exc}")
        return "I couldn't read the base CV info right now."


# ── /code — launch an agentic coding run, gated behind an explicit approval ───

# A chat-borne task is capped so a pasted wall of text can't balloon the payload
# or the eventual CLI invocation.
_CODE_MAX_TASK = 500

# Appended to a successful /code reply, then stripped and parsed by the inbound
# poller (:mod:`lhassa.notify.telegram_inbound`) to attach the inline Approve/Deny
# keyboard to THIS reply — the same "hidden marker" discipline as the research/
# remember markers in that module. Never shown to the owner.
_CODE_APPROVAL_MARKER = "[[approval_id: {ap_id}]]"


def _code_cwd(config) -> str:
    """The working directory an approved /code run would launch in.

    Mirrors :func:`lhassa.api.code_routes._agent_cwd` (the dashboard Run
    section's own resolution: ``config.agent_dir`` if set, else the process
    cwd) — for DISPLAY purposes at park time only; the actual launch re-resolves
    it the same way. Never raises.
    """
    try:
        from lhassa.api.code_routes import _agent_cwd

        return str(_agent_cwd())
    except Exception:
        return "?"


@register_command("code")
async def _cmd_code(args: str, *, config=None) -> str:
    """``/code <task>`` — park an agentic coding run for approval, never run it here.

    Launching a coding agent means write access to the owner's files, so it NEVER
    runs directly off a chat message: this parks a ``tipo="code_run"`` approval
    (payload: ``accion``, the capped ``task`` text, the auto-detected ``cli`` — the
    same detection :func:`lhassa.agents.local_cli.detect_ai_cli` the dashboard's Run
    section uses — and the resolved ``cwd``) and replies in Lhassa's own voice that
    it is waiting on the owner. The inbound poller attaches an inline Approve/Deny
    keyboard to this exact reply (see :data:`_CODE_APPROVAL_MARKER`); a tap on
    Approve is what actually launches the run.
    """
    task = (args or "").strip()
    if not task:
        return (
            "Usage: /code <task> — I'll park it for your approval before touching "
            "any files; tap Approve (or /approve <id>) to launch it."
        )
    task = task[:_CODE_MAX_TASK]

    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.agents import local_cli
        from lhassa.core import db as _db
        from lhassa.security import approval as _approval

        cli = local_cli.detect_ai_cli()
        cwd = _code_cwd(cfg)

        await _db.init_db()
        ap_id = await _approval.request_approval(
            instance_id,
            tipo="code_run",
            payload={
                "accion": "code_run",
                "task": task,
                "cli": cli,
                "cwd": cwd,
                "actor_id": "telegram",
                "origen_trust": "humano",
            },
        )
    except Exception as exc:
        logger.warning(f"commands: /code failed: {exc}")
        return _oops("park that coding task")

    reply = (
        f"Parked: I'll run '{cli}' on your files for \"{task}\" once you approve — "
        f"nothing runs yet. A button is coming; or /approve {ap_id} / /deny {ap_id}."
    )
    return reply + "\n" + _CODE_APPROVAL_MARKER.format(ap_id=ap_id)


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


# ── Etapa 2 — approve/deny from chat ──────────────────────────────────────────


def _approval_age(creado_en) -> str:
    """A compact relative age ('3h ago') for an approval row; '' when unparseable."""
    from datetime import datetime, timezone

    try:
        ts = datetime.fromisoformat(str(creado_en or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    seconds = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


@register_command("approvals")
async def _cmd_approvals(args: str, *, config=None) -> str:
    """List pending approvals — LLM-free; renders ``security.approval.describe``
    so the owner sees WHAT each one is (not just an id) plus its age and the
    exact commands to resolve it."""
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db
        from lhassa.security import approval as _approval

        await _db.init_db()
        rows = await _approval.pending(instance_id)
        if not rows:
            return "No pending approvals."

        lines = [f"Pending approvals ({len(rows)}):"]
        for i, row in enumerate(rows[:10], start=1):
            desc = _approval.describe(row)
            age = _approval_age(row.get("creado_en"))
            lines.append("")
            lines.append(f"{i}. {desc['title']}{f' — {age}' if age else ''}")
            if desc.get("detail"):
                lines.append(f"   {desc['detail']}")
            lines.append(f"   /approve {row.get('id', '?')} · /deny {row.get('id', '?')}")
        if len(rows) > 10:
            lines.append(f"\n…+{len(rows) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /approvals failed: {exc}")
        return _oops("open the approvals queue")


async def _resolve_from_chat(args: str, *, config, approved: bool) -> str:
    """Shared body for ``/approve`` and ``/deny`` — resolves one approval by id.

    Mirrors what ``api/approval_routes.py``'s POST endpoint does
    (``security.approval.resolve``), attributed to the ``"telegram"`` actor.
    """
    verb = "approve" if approved else "deny"
    ap_id = (args or "").strip().split()[0] if (args or "").strip() else ""
    if not ap_id:
        return f"Usage: /{verb} <id> — see /approvals for pending ids."
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db
        from lhassa.security import approval as _approval

        await _db.init_db()
        existing = await _db.get_approval(instance_id, ap_id)
        if existing is None:
            return f"No pending approval with id {ap_id!r}."
        await _approval.resolve(
            instance_id, ap_id, approved=approved, by_actor="telegram"
        )

        # A resolved mint_route approval TURNS ON that capability's route —
        # same approval->apply connection as the inline-keyboard callback path
        # in lhassa.notify.telegram_inbound._handle_callback_query.
        if approved and existing.get("tipo") == "mint_route":
            from lhassa.notify import capabilities as _capabilities

            _capabilities.apply_approved_route(
                existing.get("payload") or {}, config=cfg
            )

        return f"Approval {ap_id} {'approved' if approved else 'denied'}."
    except Exception as exc:
        logger.warning(f"commands: /{verb} {ap_id} failed: {exc}")
        return _oops(f"resolve approval {ap_id}")


@register_command("approve")
async def _cmd_approve(args: str, *, config=None) -> str:
    """``/approve <id>`` — approve a pending action from chat."""
    return await _resolve_from_chat(args, config=config, approved=True)


@register_command("deny")
async def _cmd_deny(args: str, *, config=None) -> str:
    """``/deny <id>`` — deny a pending action from chat."""
    return await _resolve_from_chat(args, config=config, approved=False)


# ── Etapa 2 — goal/watch capture from chat ────────────────────────────────────

# "<pattern> -> <accion>" — the arrow-syntax watch rule.
_WATCH_ARROW_RE = re.compile(r"^\s*(.+?)\s*->\s*(.+?)\s*$")

# "--every daily|hourly" flag on /goal (defaults to daily when absent).
_GOAL_EVERY_RE = re.compile(r"--every\s+(daily|hourly)\b", re.IGNORECASE)
_GOAL_CADENCE_SECONDS = {"hourly": 3600, "daily": 86400}


async def _known_actions(config) -> set:
    """The full set of registered fixed-action names (core actions + actionpack).

    Ensures the actionpack is registered first (idempotent, $0) so a freshly
    started process still recognises actionpack action names in ``/watch``.
    """
    try:
        from lhassa import actionpack

        actionpack.register_all()
    except Exception as exc:
        logger.debug(f"commands: actionpack.register_all failed: {exc}")
    from lhassa.core.actions import ACTIONS

    return set(ACTIONS.keys())


@register_command("watch")
async def _cmd_watch(args: str, *, config=None) -> str:
    """``/watch <pattern> -> <accion>`` — add a message-pattern watch rule.

    ``accion`` must be a registered fixed action or the literal ``"judgment"``
    (any action, the brain thinks about it later). ``pattern`` must compile as a
    regex. On success the rule is appended to the same JSON file the Telegram
    poller reads (:mod:`lhassa.notify.telegram_inbound`); on any validation
    failure NOTHING is written and a clear reason is returned.
    """
    m = _WATCH_ARROW_RE.match(args or "")
    if not m:
        return "Usage: /watch <pattern> -> <accion>"
    pattern_src, accion = m.group(1).strip(), m.group(2).strip()
    if not pattern_src or not accion:
        return "Usage: /watch <pattern> -> <accion>"

    try:
        re.compile(pattern_src)
    except re.error as exc:
        return f"Invalid regex {pattern_src!r}: {exc}"

    cfg = _config(config)
    if accion != "judgment":
        known = await _known_actions(cfg)
        if accion not in known:
            return (
                f"Unknown accion {accion!r}. Must be a registered action or "
                "'judgment'."
            )

    try:
        from lhassa.notify import telegram_inbound as _ti

        rules = _ti._load_raw_watch_rules(cfg)
        rules.append({"pattern": pattern_src, "accion": accion, "params": {}})
        if not _ti.save_watch_rules(cfg, rules):
            return _oops("save the watch rule (is watch_rules_file configured?)")
        return f"Watch added (#{len(rules) - 1}): /{pattern_src}/ -> {accion}"
    except Exception as exc:
        logger.warning(f"commands: /watch failed: {exc}")
        return _oops("add that watch rule")


@register_command("watches")
async def _cmd_watches(args: str, *, config=None) -> str:
    """``/watches`` — list configured watch rules with their index."""
    try:
        cfg = _config(config)
        from lhassa.notify import telegram_inbound as _ti

        rules = _ti._load_raw_watch_rules(cfg)
        if not rules:
            return "No watch rules configured."
        lines = ["Watch rules:"]
        for i, rule in enumerate(rules):
            lines.append(f"{i}: /{rule.get('pattern', '')}/ -> {rule.get('accion', '')}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /watches failed: {exc}")
        return _oops("list the watch rules")


@register_command("unwatch")
async def _cmd_unwatch(args: str, *, config=None) -> str:
    """``/unwatch <index>`` — remove a watch rule by its ``/watches`` index."""
    idx_raw = (args or "").strip()
    if not idx_raw.lstrip("-").isdigit():
        return "Usage: /unwatch <index> — see /watches for indices."
    idx = int(idx_raw)

    try:
        cfg = _config(config)
        from lhassa.notify import telegram_inbound as _ti

        rules = _ti._load_raw_watch_rules(cfg)
        if idx < 0 or idx >= len(rules):
            return f"No watch rule at index {idx}. See /watches."
        removed = rules.pop(idx)
        if not _ti.save_watch_rules(cfg, rules):
            return _oops("save after removing that rule")
        return (
            f"Removed watch rule {idx}: "
            f"/{removed.get('pattern', '')}/ -> {removed.get('accion', '')}"
        )
    except Exception as exc:
        logger.warning(f"commands: /unwatch failed: {exc}")
        return _oops("remove that watch rule")


# ── Fase 1 — Recall (LLM-free full-text search over the episode log) ─────────


@register_command("recall")
async def _cmd_recall(args: str, *, config=None) -> str:
    """``/recall <text>`` — full-text search over the episode log, LLM-free.

    Shows up to 5 matches: relative age, ``despertar_por``, ``decision`` and a
    short ``resultado`` snippet. $0 — a deterministic FTS5 query, never a model
    call. No args → a usage hint.
    """
    text = (args or "").strip()
    if not text:
        return "Usage: /recall <text> — searches past episodes for a match."
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db

        await _db.init_db()
        rows = await _db.search_episodes(instance_id, text, 5)
        if not rows:
            return f"No episodes match {text!r}."

        lines = [f"Recall for {text!r} ({len(rows)}):"]
        for i, row in enumerate(rows, start=1):
            age = _approval_age(row.get("creado_en"))
            despertar = row.get("despertar_por") or "?"
            decision = row.get("decision") or "?"
            snippet = (row.get("resultado") or "").replace("\n", " ").strip()
            lines.append("")
            lines.append(f"{i}. {despertar} — {decision}{f' — {age}' if age else ''}")
            if snippet:
                lines.append(f"   {snippet}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /recall {text!r} failed: {exc}")
        return _oops("search my episodes")


@register_command("goal")
async def _cmd_goal(args: str, *, config=None) -> str:
    """``/goal <text> [--every daily|hourly]`` — schedule a recurring judgment task.

    Mints a recurring ``judgment=True`` task (``core.dispatch.mint_task``) with
    HUMANO-origin trust and actor ``"telegram"``. The task is picked up and REASONED
    about by the brain on its own schedule (a real, later token spend) — minting it
    here is $0; only that later think costs anything.
    """
    text = args or ""
    cadence = "daily"
    m = _GOAL_EVERY_RE.search(text)
    if m:
        cadence = m.group(1).lower()
        text = _GOAL_EVERY_RE.sub("", text).strip()
    text = text.strip()
    if not text:
        return "Usage: /goal <text> [--every daily|hourly]"

    try:
        from datetime import datetime, timezone

        from lhassa.core import db as _db
        from lhassa.core.dispatch import mint_task
        from lhassa.security.trust import Origin, TrustLevel

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        await _db.init_db()

        origin = Origin(trust=TrustLevel.HUMANO, actor_id="telegram")
        due_at = datetime.now(timezone.utc).isoformat()
        interval = _GOAL_CADENCE_SECONDS[cadence]
        task_id, estado = await mint_task(
            instance_id,
            "goal",
            objetivo=text,
            params={},
            origin=origin,
            due_at=due_at,
            recurrence=str(interval),
            judgment=True,
        )
    except Exception as exc:
        logger.warning(f"commands: /goal failed: {exc}")
        return _oops("schedule that goal")

    if not task_id:
        return f"Could not schedule goal: {estado}"
    return f"Goal scheduled ({cadence}): {text} [task {task_id}]"


# Free-form instruction -> a known templo action, LLM-free (a keyword match, not
# NLP). Anything that doesn't match falls through to org_run(department="templo")
# — the judgment door for internal work that isn't one of these fixed actions.
_TEMPLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("distill", "distill_memory"),
    ("repetition", "detect_repetition"),
    ("repeat", "detect_repetition"),
    ("review tool", "review_tools"),
    ("curate", "curate_skills"),
    ("skill", "curate_skills"),
    ("calendar", "calendar_sync"),
    ("sync", "calendar_sync"),
)


def _match_temple_action(text: str) -> Optional[str]:
    """The known templo action ``text`` maps to, or ``None`` — LLM-free."""
    lowered = text.lower()
    for keyword, action in _TEMPLE_KEYWORDS:
        if keyword in lowered:
            return action
    return None


@register_command("temple")
async def _cmd_temple(args: str, *, config=None) -> str:
    """``/temple <instruction>`` — hand internal work to Lhassa's own inner
    department (templo), the down-execute-up loop (see :mod:`lhassa.org.temple`).

    LLM-free where the instruction keyword-matches a known deterministic
    action (distill/repetition/review tools/curate skills/calendar sync) — runs
    it immediately through :func:`lhassa.org.temple.delegate_internal` and
    returns the result. Otherwise mints an ``org_run`` task with
    ``department="templo"`` — the judgment door, resolved on the next tick.
    """
    text = (args or "").strip()
    if not text:
        return "Usage: /temple <instruction> (e.g. /temple distill recent episodes)"

    cfg = _config(config)
    action = _match_temple_action(text)
    if action:
        try:
            from lhassa.org.temple import delegate_internal

            record = await delegate_internal(action, {}, config=cfg, instruction=text)
        except Exception as exc:
            logger.warning(f"commands: /temple failed running {action}: {exc}")
            return _oops("run that through templo")
        result = str(record.get("result") or "")[:300]
        return f"templo ran {action}: {result}"

    # No fixed-action match — the judgment door: org_run(department="templo").
    try:
        from lhassa.core import db as _db
        from lhassa.core.dispatch import mint_task
        from lhassa.security.trust import Origin, TrustLevel

        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        await _db.init_db()
        origin = Origin(trust=TrustLevel.HUMANO, actor_id="telegram")
        task_id, estado = await mint_task(
            instance_id,
            "org_run",
            objetivo=text[:200],
            params={"goal": text, "department": "templo"},
            origin=origin,
        )
    except Exception as exc:
        logger.warning(f"commands: /temple delegate failed: {exc}")
        return _oops("hand that to templo")

    if not task_id:
        return f"Could not hand that to templo: {estado}"
    return f"Handed to templo: {text} [task {task_id}]"


# ── CLI parity batch — report / memory / capabilities / tools / skills ───────


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


@register_command("memory")
async def _cmd_memory(args: str, *, config=None) -> str:
    """``/memory <query>`` — search durable facts (substring/tag), LLM-free.

    Mirrors ``lhassa memory``'s search (:mod:`lhassa.memory.facts`): a query
    matches a fact's content or any of its tags. Shows up to 5 matches: tipo, a
    short snippet, and confianza. No args → a usage hint.
    """
    query = (args or "").strip()
    if not query:
        return "Usage: /memory <query> — searches durable facts by text or tag."
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.memory import facts

        resolved = facts.resolve(await facts.load_facts(instance_id))
        q = query.lower()
        matches = [
            f
            for f in resolved
            if q in f.contenido.lower() or any(q in t.lower() for t in f.tags)
        ]
        if not matches:
            return f"No facts match {query!r}."
        lines = [f"Facts matching {query!r} ({len(matches)}):"]
        for f in matches[:5]:
            snippet = f.contenido.replace("\n", " ").strip()
            if len(snippet) > 100:
                snippet = snippet[:97] + "…"
            lines.append(f"- [{f.tipo}] {snippet} (conf {f.confianza:.2f})")
        if len(matches) > 5:
            lines.append(f"…+{len(matches) - 5} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /memory {query!r} failed: {exc}")
        return _oops("search my memory")


@register_command("checkin")
async def _cmd_checkin(args: str, *, config=None) -> str:
    """``/checkin <text>`` — persist an owner-authored daily check-in.

    This is the explicit capture path for the personal digest questions. It is
    deterministic and $0: append one tagged fact to the grande, then acknowledge.
    """
    text = (args or "").strip()
    if not text:
        return (
            "Usage: /checkin <how you are / how you slept / what I should remember>"
        )
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db
        from lhassa.memory import checkins

        await _db.init_db()
        item = await checkins.record(instance_id, text, source="telegram-checkin")
        if item.slot == "morning":
            return f"Noted for the morning trail: {item.id}"
        if item.slot == "evening":
            return f"Noted for tomorrow: {item.id}"
        return f"Noted: {item.id}"
    except Exception as exc:
        logger.warning(f"commands: /checkin failed: {exc}")
        return _oops("save that check-in")


async def _render_personal_agenda(config, *, slot: str) -> str:
    cfg = _config(config)
    from lhassa.personal import agenda as personal_agenda

    summary = await personal_agenda.build(cfg, slot=slot)
    lang = getattr(cfg, "digest_language", "") or "en"
    return personal_agenda.render(summary, language=lang)


@register_command("today")
async def _cmd_today(args: str, *, config=None) -> str:
    """``/today`` — personal agenda for the current day, LLM-free."""
    try:
        return await _render_personal_agenda(config, slot="today")
    except Exception as exc:
        logger.warning(f"commands: /today failed: {exc}")
        return _oops("read today's agenda")


@register_command("tomorrow")
async def _cmd_tomorrow(args: str, *, config=None) -> str:
    """``/tomorrow`` — personal agenda for the next day, LLM-free."""
    try:
        return await _render_personal_agenda(config, slot="tomorrow")
    except Exception as exc:
        logger.warning(f"commands: /tomorrow failed: {exc}")
        return _oops("read tomorrow's agenda")


@register_command("capabilities")
async def _cmd_capabilities(args: str, *, config=None) -> str:
    """``/capabilities`` — a compact, LLM-free wired/on-off summary.

    A phone-sized subset of ``lhassa capabilities`` (:mod:`lhassa.cli.capabilities`):
    providers wired, channels active, org shape, and the monitor/voice/org-live
    toggles. Reads config + registries only — never constructs a provider.
    """
    try:
        cfg = _config(config)
        lines = ["Capabilities:"]

        try:
            from lhassa.llm.provider import PROVIDERS

            lines.append(f"Providers: {', '.join(PROVIDERS.names()) or '(none)'}")
        except Exception:
            pass

        try:
            from lhassa.notify.channels import active_channels

            active = [c.name for c in active_channels(cfg)]
            lines.append(f"Channels active: {', '.join(active) or '(none)'}")
        except Exception:
            pass

        try:
            from lhassa.org.topology import load_topology

            topology = load_topology(cfg)
            shape = (
                "standalone (1 brain)"
                if topology.is_standalone()
                else f"{len(topology.nodes())}-brain org"
            )
            lines.append(f"Org: {shape}")
        except Exception:
            lines.append("Org: unavailable")

        try:
            from lhassa.org.coordinator import is_live

            org_live = is_live()
        except Exception:
            org_live = False
        monitor_on = bool(getattr(cfg, "monitor_enabled", False))
        voice_on = bool(getattr(cfg, "voice_enabled", False))
        lines.append(
            f"Org live: {_flag(org_live)} · Monitor: {_flag(monitor_on)} · "
            f"Voice: {_flag(voice_on)}"
        )
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /capabilities failed: {exc}")
        return _oops("read my capabilities")


def _flag(value: bool) -> str:
    return "on" if value else "off"


@register_command("tools")
async def _cmd_tools(args: str, *, config=None) -> str:
    """``/tools`` — the self-written tools and their gate/usage state, LLM-free.

    Reads the same ``tools`` table :func:`lhassa.cli.commands.tools_cmd` renders
    (:func:`lhassa.core.db.list_tools`): name, dominio, estado, and usos/fallos.
    """
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db

        await _db.init_db()
        rows = await _db.list_tools(instance_id)
        if not rows:
            return "No self-written tools yet."
        lines = [f"Tools ({len(rows)}):"]
        for r in rows[:10]:
            name = r.get("nombre") or "?"
            estado = r.get("estado") or "—"
            dominio = r.get("dominio") or "—"
            usos = r.get("usos") or 0
            fallos = r.get("fallos") or 0
            lines.append(f"- {name} [{dominio}] {estado} · {usos} uses, {fallos} fails")
        if len(rows) > 10:
            lines.append(f"…+{len(rows) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /tools failed: {exc}")
        return _oops("list my tools")


@register_command("org")
async def _cmd_org(args: str, *, config=None) -> str:
    """``/org`` — the org chart as a compact indented tree, LLM-free.

    Renders :func:`lhassa.org.topology.load_topology`: each brain's id, role and
    department (when it has one), indented by depth. The permanent administrator
    (the root) is marked ``★``; a coordinating brain ``○``; a leaf that actually
    executes ``●``. Never constructs a provider — reads the topology file/derived
    default only.
    """
    try:
        cfg = _config(config)
        from lhassa.org.topology import load_topology

        topology = load_topology(cfg)
        nodes = topology.nodes()
        if not nodes:
            return _oops("read the org chart")

        lines = ["Org chart:"]

        def render(node_id: str, depth: int) -> None:
            node = topology.node(node_id)
            if topology.is_admin(node_id):
                marker = "★"
            elif topology.is_leaf(node_id):
                marker = "●"
            else:
                marker = "○"
            dept = f" [{node.department}]" if node.department else ""
            lines.append("  " * depth + f"{marker} {node.id} ({node.role}){dept}")
            for child in topology.children_of(node_id):
                render(child.id, depth + 1)

        render(topology.root().id, 0)
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /org failed: {exc}")
        return _oops("read the org chart")


@register_command("fleet")
async def _cmd_fleet(args: str, *, config=None) -> str:
    """``/fleet`` — known machines, last check-in and desired state, LLM-free.

    Reuses :func:`lhassa.api.fleet_routes.get_fleet` directly — the exact same
    read ``/api/fleet`` serves — rather than re-deriving the fleet from episodes.
    """
    try:
        from lhassa.api.fleet_routes import get_fleet

        rows = await get_fleet()
        if not rows:
            return "No machines known yet."
        lines = [f"Fleet ({len(rows)}):"]
        for r in rows[:10]:
            mid = r.get("machine_id") or "?"
            last = r.get("last_checkin") or "never"
            ds = r.get("desired_state") or {}
            ds_str = ", ".join(f"{k}={v}" for k, v in ds.items()) or "—"
            lines.append(f"- {mid}: last {last} · {ds_str}")
        if len(rows) > 10:
            lines.append(f"…+{len(rows) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /fleet failed: {exc}")
        return _oops("read the fleet")


# ── /ai — a raw passthrough to the active provider (the one paid direct door) ──────


async def _resolve_chat_provider(cfg):
    """The (provider, model) attempts for a chat-seat call — pin/router + chain."""
    from lhassa.llm import router
    from lhassa.llm.provider import get_model_provider
    from lhassa.llm.router import fallback_chain

    pinned = (getattr(cfg, "telegram_provider", "") or "").strip().lower()
    if pinned and pinned != "auto":
        primary = get_model_provider(cfg, name=pinned)
    else:
        primary = router.pick_provider(sensitive=False, hard=False, config=cfg)
    model = router.pick_model(primary, hard=False, config=cfg)
    return [(primary, model), *fallback_chain(cfg, primary)]


@register_command("ai")
async def _cmd_ai(args: str, *, config=None) -> str:
    """``/ai <question>`` — search the live web AND let the model SYNTHESIZE a
    concrete answer from what it finds.

    The difference from a plain question: free-text chat (tool-less) can only
    hand back links via the research bridge. ``/ai`` does the search itself,
    feeds the real results to the active model (with persona + the owner's
    durable facts as context), and asks it to ANSWER — a specific figure, a
    direct conclusion — citing the sources it used. It DOES spend one model
    call (the one paid direct door, like ``/cv``); kept explicit so it never
    fires on every message. Never raises.
    """
    text = (args or "").strip()
    if not text:
        return "Ask me to look something up and answer it: /ai how much is a flight from Madrid to Bogotá?"
    try:
        from lhassa.llm.gateway import BudgetExceeded
        from lhassa.llm.provider import ProviderNotConfigured
        from lhassa.persona import load_identity
        from lhassa.tools import search as _search

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"

        # 1) Live search — news vertical for news-intent, else the web.
        results: list[dict] = []
        try:
            if re.search(r"\b(noticias?|news|titulares|headlines?)\b", text, re.IGNORECASE):
                results = await _search.news_search(text, max_results=6) or []
            if not results:
                results = await _search.web_search(text, max_results=6) or []
        except Exception as exc:
            logger.debug(f"commands: /ai search failed: {exc}")

        sources = "\n".join(
            f"- {str(r.get('title') or '')[:100]} — {str(r.get('body') or '')[:200]} "
            f"({r.get('url') or ''})"
            for r in results[:6]
        ) or "(no web results — answer from your own knowledge and say it's not live-sourced)"

        # 2) Persona + durable facts as grounding context.
        identity = load_identity(instance_id)
        facts_block = ""
        try:
            from lhassa.memory import facts as _facts

            rows = await _facts.load_facts(instance_id, pinned=True)
            if rows:
                facts_block = "What you know about your owner:\n" + "\n".join(
                    f"- {getattr(r, 'contenido', '')}" for r in rows[:8]
                )
        except Exception:
            facts_block = ""

        system = "\n\n".join(
            p for p in (
                identity.persona_block,
                facts_block,
                "You are given live web results. ANSWER the question directly and "
                "specifically — a figure, a range, a concrete conclusion — using them; "
                "cite the source name in-line. If the results don't cover it, say so "
                "plainly. Be brief, plain, and honest about uncertainty.",
            ) if p
        )
        user = f"Question: {text}\n\nLive web results:\n{sources}"

        # 3) Synthesize through the active chat provider (with the fallback chain).
        last_exc = None
        for prov, mdl in await _resolve_chat_provider(cfg):
            try:
                out = await prov.complete(
                    [{"role": "user", "content": user}],
                    model=mdl, max_tokens=1024, system=system,
                )
            except (ProviderNotConfigured, BudgetExceeded) as exc:
                last_exc = exc
                continue
            except Exception as exc:
                logger.warning(f"commands: /ai provider {getattr(prov,'name','?')!r} failed: {exc}")
                last_exc = exc
                continue
            answer = (out.text or "").strip()
            return answer or "The model returned nothing."
        return _oops(f"reach a model for that ({last_exc})")
    except Exception as exc:
        logger.warning(f"commands: /ai failed: {exc}")
        return _oops("look that up and answer it")


# ── /remind — a timed reminder in plain words, parsed deterministically ($0) ──────

# Time phrases understood, in order of specificity. All local-time; conversion to
# UTC mirrors the schedule tool (a past time today rolls to tomorrow).
_REMIND_AT_RE = re.compile(
    r"(?:\b(?:a\s+las?|at)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
    r"|(?:\b(?:a\s+las?|at)\s+)(\d{1,2})(?::(\d{2}))?\b",
    re.IGNORECASE,
)
_REMIND_IN_RE = re.compile(
    r"\b(?:en|in)\s+(\d+)\s*(min(?:uto)?s?|m|h|horas?|hours?)\b", re.IGNORECASE
)
_REMIND_TOMORROW_RE = re.compile(r"\b(mañana|tomorrow)\b", re.IGNORECASE)
# Leading filler after the trigger word: "de", "que", "de que", "to".
_REMIND_FILLER_RE = re.compile(r"^(?:de\s+que\s+|de\s+|que\s+|to\s+)", re.IGNORECASE)


def parse_remind(text: str, *, now=None):
    """Parse '<what> a las 5pm' / 'in 30 min <what>' → ``(run_at_utc, what, when)``.

    Deterministic, $0, local-time aware: bare hours honor am/pm, a past time
    today rolls to tomorrow, 'mañana/tomorrow' adds a day. Returns ``None`` when
    no time phrase is found (the caller decides what to say). The matched time
    words are stripped from the reminder text.
    """
    from datetime import datetime, timedelta, timezone

    raw = " ".join((text or "").split())
    if not raw:
        return None
    now_local = now or datetime.now().astimezone()

    m = _REMIND_IN_RE.search(raw)
    if m:
        qty = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(hours=qty) if unit.startswith(("h",)) else timedelta(minutes=qty)
        run_at = now_local + delta
        what = (raw[: m.start()] + raw[m.end():]).strip()
        when = f"in {qty} {'h' if unit.startswith('h') else 'min'}"
    else:
        m = _REMIND_AT_RE.search(raw)
        if not m:
            return None
        hour = int(m.group(1) or m.group(4))
        minute = int(m.group(2) or m.group(5) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        run_at = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        what = (raw[: m.start()] + raw[m.end():]).strip()
        tomorrow = _REMIND_TOMORROW_RE.search(what)
        if tomorrow:
            what = (what[: tomorrow.start()] + what[tomorrow.end():]).strip()
            run_at += timedelta(days=1)
        elif run_at <= now_local:
            run_at += timedelta(days=1)
        when = f"at {hour:02d}:{minute:02d}"

    what = _REMIND_FILLER_RE.sub("", what).strip(" ,.-") or "Reminder"
    return (run_at.astimezone(timezone.utc), what[:300], when)


async def _deliver_calendar_event(what: str, when: str, run_at, *, config=None) -> bool:
    """Build + deliver a ``.ics`` for ``what``/``run_at`` — best-effort, never
    raises. Returns whether the delivery send succeeded (``False`` on ANY
    trouble — .ics generation, write, or delivery — so a caller can degrade
    silently without breaking its own reply).

    Lazy shell→shell import of :mod:`lhassa.org.deliver` (same pattern as
    ``/cvbase`` reaching into :mod:`lhassa.cv`): the calendar leaf package
    itself never sends anything, this is the one caller that hands the file to
    the sender-of-record seam. ``origin_department=""`` — a calendar event is
    Lhassa's own hand, not delegated work, same as ``send_reminder``.
    """
    try:
        from lhassa.calendar import build_event, write_ics

        content = build_event(what, run_at)
        if not content:
            return False
        ics_path = write_ics(content, name=what)

        from lhassa.org.deliver import deliver

        delivered, _artifact_id = await deliver(
            document=ics_path,
            caption=f"📅 {what} — {when}",
            kind="calendar_event",
            metadata={"summary": what, "when": when},
            origin_department="",
            config=config,
        )
        return bool(delivered)
    except Exception as exc:
        logger.warning(f"commands: calendar event delivery failed: {exc}")
        return False


@register_command("remind")
async def _cmd_remind(args: str, *, config=None) -> str:
    """``/remind <what> a las 5pm | at 17:00 | in 30 min [mañana]`` — a timed
    reminder through the one clock (a ``send_reminder`` task the heartbeat
    delivers). Deterministic parse, $0; the think never gets involved.

    ADDITIVELY also generates a ``.ics`` for the same event and delivers it
    (a tappable calendar file, on top of the internal reminder) — see
    :func:`_deliver_calendar_event`. Best-effort: the internal reminder is
    minted and its reply returned regardless of whether the ``.ics`` step
    succeeds, so a calendar hiccup never breaks ``/remind``.
    """
    try:
        parsed = parse_remind(args)
        if parsed is None:
            return (
                "Tell me what and when — e.g. /remind llamar al banco a las 5pm, "
                "/remind stretch in 45 min, /remind pagar arriendo mañana 9am."
            )
        run_at, what, when = parsed

        from lhassa.core import db as _db
        from lhassa.core.dispatch import mint_task
        from lhassa.security.trust import Origin, TrustLevel

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        await _db.init_db()
        task_id, estado = await mint_task(
            instance_id,
            "send_reminder",
            objetivo=what[:80],
            params={"text": f"⏰ {what}"},
            origin=Origin(trust=TrustLevel.HUMANO, actor_id="telegram"),
            due_at=run_at.isoformat(),
        )
        if not task_id:
            return _oops(f"schedule that reminder ({estado})")

        try:
            await _deliver_calendar_event(what, when, run_at, config=cfg)
        except Exception as exc:  # never let the .ics step break /remind
            logger.warning(f"commands: /remind calendar delivery failed: {exc}")

        local = run_at.astimezone().strftime("%a %H:%M")
        return f"⏰ Noted — I'll remind you {when} ({local}): {what}"
    except Exception as exc:
        logger.warning(f"commands: /remind failed: {exc}")
        return _oops("schedule that reminder")


@register_command("event")
async def _cmd_event(args: str, *, config=None) -> str:
    """``/event <what> a las 5pm | at 17:00 | in 30 min [mañana]`` — calendar-only:
    generates and delivers a ``.ics`` for the event, no internal reminder task
    (see ``/remind`` for both). Same deterministic ``parse_remind``, $0."""
    try:
        parsed = parse_remind(args)
        if parsed is None:
            return (
                "Tell me what and when — e.g. /event dentist appointment at 3pm, "
                "/event flight in 2 hours, /event standup mañana 9am."
            )
        run_at, what, when = parsed
        cfg = _config(config)

        ok = await _deliver_calendar_event(what, when, run_at, config=cfg)
        if not ok:
            return _oops("build and send that calendar event")

        local = run_at.astimezone().strftime("%a %H:%M")
        return f"📅 Sent — calendar event for {when} ({local}): {what}"
    except Exception as exc:
        logger.warning(f"commands: /event failed: {exc}")
        return _oops("build and send that calendar event")


# ── /calendar, /calendars — multiple NAMED calendar feeds ─────────────────────
#
# An owner can subscribe several calendars (institutional, personal, a project
# one), each with a name, so /today shows which calendar an event is from (see
# lhassa.calendar.store). These commands never touch the network — they only
# edit the JSON store; the next /today simply picks up the new list.


def _mask_url(url: str) -> str:
    """A short, non-secret preview of a feed url — never the full thing.

    Calendar URLs carry read access to the owner's calendar, so /calendars must
    never echo one back in full. Shows scheme+host plus a short prefix of the
    path, elided.
    """
    url = (url or "").strip()
    if len(url) <= 40:
        return url
    return url[:40] + "…"


@register_command("calendars")
async def _cmd_calendars(args: str, *, config=None) -> str:
    """``/calendars`` — list configured named calendars (name + a masked url)."""
    try:
        cfg = _config(config)
        from lhassa.calendar import store as _cal_store

        items = _cal_store.all_feeds(cfg)
        if not items:
            return (
                "No calendars configured yet. Use /calendar add <name> <url> to "
                "subscribe one."
            )
        primary = _cal_store.get_primary(cfg)
        lines = [f"Calendars ({len(items)}):"]
        for it in items:
            name = it.get("name")
            marker = " (primary)" if primary and name == primary else ""
            lines.append(f"- {name}{marker}: {_mask_url(it.get('url', ''))}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /calendars failed: {exc}")
        return _oops("list your calendars")


@register_command("calendar")
async def _cmd_calendar(args: str, *, config=None) -> str:
    """``/calendar add <name> <url> | remove <name> | primary <name> | sync`` —
    manage named calendar feeds and the master-calendar reconcile.

    ``add``/``remove`` never touch the network — they only edit the JSON store
    (:mod:`lhassa.calendar.store`); the feed is fetched lazily, the next time
    /today (or /calendar sync) runs. ``primary`` designates the MASTER calendar
    (see :mod:`lhassa.calendar.reconcile`); ``sync`` runs the $0 diff now and
    reports what it found/delivered.
    """
    text = (args or "").strip()
    if not text:
        return (
            "Usage: /calendar add <name> <url> — subscribe a named calendar.\n"
            "       /calendar remove <name> — drop one.\n"
            "       /calendar primary <name> — set your master calendar.\n"
            "       /calendar sync — find events missing from the master.\n"
            "       /calendar view <when> — e.g. 'friday', 'esta semana', 'hoy'.\n"
            "See /calendars to list what's configured."
        )
    parts = text.split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    cfg = _config(config)
    try:
        from lhassa.calendar import store as _cal_store

        if sub == "add":
            add_parts = rest.split(maxsplit=1)
            if len(add_parts) < 2:
                return "Usage: /calendar add <name> <url>"
            name, url = add_parts[0], add_parts[1].strip()
            ok = _cal_store.add_calendar(name, url, config=cfg)
            if not ok:
                return (
                    "Couldn't add that — check the name isn't empty and the url "
                    "starts with http:// or https://."
                )
            return f"Calendar added: {name}. See /calendars, /today."

        if sub == "remove":
            name = rest.strip()
            if not name:
                return "Usage: /calendar remove <name>"
            ok = _cal_store.remove_calendar(name, config=cfg)
            if not ok:
                return f"No calendar named {name!r}. See /calendars."
            return f"Calendar removed: {name}."

        if sub == "primary":
            name = rest.strip()
            if not name:
                return "Usage: /calendar primary <name>"
            ok = _cal_store.set_primary(name, config=cfg)
            if not ok:
                return f"No calendar named {name!r}. See /calendars."
            return f"Master calendar set: {name}. /calendar sync will diff every other calendar against it."

        if sub == "sync":
            from lhassa.actionpack._calendar_sync import calendar_sync

            instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
            summary = await calendar_sync({"instance_id": instance_id, "config": cfg})
            return _calendar_sync_reply(summary)

        if sub in ("view", "on", "when"):
            when = rest.strip()
            if not when:
                return "Usage: /calendar view <when> — e.g. 'friday', 'esta semana', 'hoy'."
            from lhassa.calendar import query as _cal_query

            instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
            # Force a calendar answer even for a bare time phrase with no
            # calendar keyword ("/calendar view friday") — the command itself
            # already establishes intent, so route straight past
            # is_calendar_query's stricter chat-message heuristic.
            reply = await _cal_query.answer(instance_id, f"calendar {when}", config=cfg)
            if reply is None:
                return (
                    f"Couldn't work out a time reference in {when!r}. Try "
                    "'friday', 'tomorrow', 'esta semana', 'hoy'…"
                )
            return reply

        return (
            f"Unknown /calendar sub-command {sub!r}. Usage: /calendar add <name> "
            "<url> | remove <name> | primary <name> | sync | view <when>."
        )
    except Exception as exc:
        logger.warning(f"commands: /calendar {sub} failed: {exc}")
        return _oops("manage your calendars")


_CALENDAR_SYNC_RE = re.compile(
    r"^calendar_sync: (\d+) unsynced event\(s\), (\d+) delivered$"
)


def _calendar_sync_reply(summary: str) -> str:
    """Render :func:`lhassa.actionpack._calendar_sync.calendar_sync`'s plain
    outcome string in the Telegram voice. Falls back to the raw string for the
    "no primary" / "nothing to sync" / error outcomes — those already read
    fine verbatim."""
    m = _CALENDAR_SYNC_RE.match(summary or "")
    if not m:
        return summary
    n, delivered = int(m.group(1)), int(m.group(2))
    if n == 0:
        return "Your other calendars are already in sync with your master calendar."
    return (
        f"I found {n} event(s) on your other calendars not in your master "
        f"calendar — sent you {delivered} to add."
    )


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


@register_command("skills")
async def _cmd_skills(args: str, *, config=None) -> str:
    """``/skills`` — installed skills, LLM-free.

    Reads :class:`lhassa.skills.store.SkillStore` (list) and, where cheap,
    :func:`lhassa.skills.curator.get_usage` for the pinned/state flag per skill.
    """
    try:
        cfg = _config(config)
        from lhassa.skills.store import SkillStore

        skills = SkillStore(cfg).list_skills()
        if not skills:
            return "No skills installed."

        try:
            from lhassa.skills import curator

            usage = curator.get_usage(cfg)
        except Exception:
            usage = {}

        lines = [f"Skills ({len(skills)}):"]
        for s in skills[:10]:
            name = s.name or "?"
            desc = (s.description or "").strip()
            if len(desc) > 60:
                desc = desc[:57] + "…"
            entry = usage.get(name) or {}
            pinned = " (pinned)" if entry.get("pinned") else ""
            state = entry.get("state")
            state_str = f" [{state}]" if state and state != "active" else ""
            lines.append(f"- {name}{pinned}{state_str}{f' — {desc}' if desc else ''}")
        if len(skills) > 10:
            lines.append(f"…+{len(skills) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /skills failed: {exc}")
        return _oops("list my skills")
