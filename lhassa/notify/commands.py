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
        return "Sorry, that command hit a snag. Try /help."


# ── helpers (LLM-free, never raise) ───────────────────────────────────────────


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
    """A short, warm, gender-neutral greeting pointing at /help."""
    return (
        "Hi, I'm Lhassa — your personal assistant. Ask me anything in plain "
        "language and I'll think it through and answer. Send /help to see what "
        "I can do."
    )


@register_command("help")
async def _cmd_help(args: str, *, config=None) -> str:
    """List what Lhassa can do; the command list stays in sync via COMMANDS.names()."""
    try:
        cmd_list = ", ".join("/" + n for n in COMMANDS.names()) or "(none)"
        return (
            "I'm Lhassa. Here's what I can do:\n"
            "- Ask me anything in plain language and I'll answer it through my "
            "brain.\n"
            f"- Commands: {cmd_list}\n"
            "I think locally and cheaply, so chatting with me is light on cost."
        )
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
        return "Status is unavailable right now."
