"""Inbound Telegram poller — turns Lhassa's Telegram chat into a command channel.

Long-polls the Telegram Bot API ``getUpdates`` (offset-based) over httpx, answers
each accepted message through a **tool-less brain think** (identity + layered
memory + the router's routine provider → ``provider.complete``, no tools — the same
shape as :func:`lhassa.voice.brain_bridge.answer`, but logging a
``despertar_por="telegram"`` episode), and replies via the existing outbound
:func:`lhassa.notify.telegram.send`.

A tool-less think is deliberate: routing chat through the full 26-tool agent on the
cheap heartbeat backend produced malformed tool calls and failed replies, whereas a
plain completion on the routine (opencode) provider answers reliably.

Same discipline as the rest of :mod:`lhassa.notify`:
- **gated** — runs only when ``config.telegram_poll_enabled`` is true AND both a
  bot token and chat id are configured; otherwise it is a silent, network-free
  no-op (logged once);
- **owner-only** — messages whose ``chat.id`` is not exactly ``telegram_chat_id``
  are ignored (security: the bot answers nobody but its owner);
- **never raises** — a bad/malformed update is logged and skipped; a single error
  can never kill the poll loop.

Run standalone with ``python -m lhassa.notify.telegram_inbound``. It is **not**
wired into ``main.py`` — that is a later step.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil

import httpx
from loguru import logger

from lhassa.notify import commands, telegram

# The getUpdates endpoint. ``<token>`` is interpolated per call.
_GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"

# Long-poll seconds passed to Telegram; the client timeout sits a little above it.
_LONG_POLL_SECONDS = 30
_TIMEOUT = float(_LONG_POLL_SECONDS) + 15.0

# Backoff bounds for run_forever when a cycle errors out (seconds).
_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0

# A chat turn is a short answer, not a long job.
_CHAT_MAX_TOKENS = 512

# Short fallback when the brain can't reach a model — sent verbatim to the chat.
_FALLBACK_REPLY = "I can't reach my model right now."

# Marker for an episode whose model could not run (mirrors core/step + voice).
_DEFERRED_DECISION = "deferred: telegram needs judgment"

# Chat-output instruction layered on the persona (plain text for a chat bubble).
_CHAT_INSTRUCTION = (
    "You are answering in a Telegram chat with your owner. Reply in short, natural "
    "messages. Lead with the answer and keep it brief."
)


def _enabled(config) -> bool:
    """True only when polling is switched on AND credentials are present."""
    return bool(getattr(config, "telegram_poll_enabled", False)) and telegram.is_configured(config)


# Provider names that are backed by a local CLI binary — skipped from the chain
# when that binary is not installed (``shutil.which``), so the chain degrades
# cleanly on a machine that lacks a given subscription CLI. The binary name can be
# overridden per-provider via a ``<name>_bin`` config attr (e.g. ``opencode_bin``).
_CLI_PROVIDERS = {"opencode", "claude", "codex"}


def _model_for(name: str, config) -> str | None:
    """The model arg for a chain provider.

    The cloud gateway (``default``) uses the cheap routine model, as before; the
    CLI subscriptions (claude/codex/opencode) and other providers pass ``None`` so
    each CLI/provider picks its own model from the owner's subscription/config.
    """
    if name == "default":
        return _secondary_model(config)
    return None


def _fallback_chain(config, primary):
    """Build the configured chat/think fallback chain as ``[(provider, model), …]``.

    Walks ``config.fallback_providers`` (comma-separated names, default
    ``"claude,codex,default"``) IN ORDER, resolving each via
    :func:`lhassa.llm.provider.get_model_provider`. This keeps the owner on their
    CLI subscriptions (``opencode → claude → codex``) before paying for the cloud
    gateway (``default``). Each entry is:

    - **skipped if it is the same kind as the primary** (``name == primary.name``) —
      don't retry e.g. opencode→opencode;
    - **skipped if it is a CLI provider whose binary is not installed**
      (``shutil.which``), so the chain degrades cleanly on a machine missing that CLI;
    - **skipped if it cannot be resolved** (unknown/raising factory).

    Never raises — a bad config or a failed resolve yields a shorter chain. This is
    the seam tests patch to assert the chain order / skipping behaviour.
    """
    chain: list[tuple] = []
    try:
        names = [
            n.strip()
            for n in (getattr(config, "fallback_providers", "") or "").split(",")
            if n.strip()
        ]
    except Exception:
        return chain

    from lhassa.llm.provider import get_model_provider

    primary_name = getattr(primary, "name", "")
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        if name == primary_name:
            continue  # don't retry the same kind as the primary
        if name in _CLI_PROVIDERS:
            binary = (getattr(config, f"{name}_bin", "") or "") or name
            if shutil.which(binary) is None:
                continue  # CLI not installed → skip, degrade cleanly
        try:
            provider = get_model_provider(config, name=name)
        except Exception:
            continue
        if provider is not None:
            chain.append((provider, _model_for(name, config)))
    return chain


def _secondary_model(config):
    """The model the cloud fallback should use (cheap routine model)."""
    return getattr(config, "model_cheap", "") or getattr(config, "model", "") or None


async def _brain_reply(text: str, *, config) -> str:
    """Answer one message via a tool-less brain think. Never raises.

    Mirrors :func:`lhassa.voice.brain_bridge.answer`: load identity + layered memory
    (with ``text`` as the semantic query), let the router pick the routine
    provider+model (so the opencode heartbeat backend is preferred), call
    ``provider.complete`` with **no tools**, and log a ``despertar_por="telegram"``
    episode. Degrades to :data:`_FALLBACK_REPLY` on a missing provider / spent
    budget / any error — it never raises into the poll loop.
    """
    from lhassa.core import db, watchlist
    from lhassa.core.episode import Episode
    from lhassa.llm import router
    from lhassa.llm.gateway import BudgetExceeded
    from lhassa.llm.provider import ProviderNotConfigured
    from lhassa.memory import load as memory_load
    from lhassa.persona import load_identity

    instance_id = getattr(config, "instance_id", None) or "inst_personal"

    # Idempotent — a chat turn must work even if it wakes the brain first.
    await db.init_db()

    ep = Episode.begin(despertar_por="telegram", instance_id=instance_id)
    reply = _FALLBACK_REPLY
    try:
        # Layer memory (semantic query = the message; layer 4 fires only when
        # enabled + indexed, otherwise it's the unchanged pinned/recent/tags).
        ctx = await memory_load.load(
            instance_id,
            token_budget=getattr(config, "context_token_budget", 2000),
            query=text,
        )

        # Routine path: not delegated, not flagged hard → the router prefers the
        # local/opencode heartbeat provider (§3-Fase 6).
        trigger = watchlist.Trigger(kind="telegram", action="responder", params={})
        decision = router.decide(trigger, ctx, config=config)

        identity = load_identity(instance_id)
        parts = [identity.persona_block]
        memory_block = ctx.to_prompt()
        if memory_block:
            parts.append(memory_block)
        parts.append(_CHAT_INSTRUCTION)
        system = "\n\n".join(parts)

        # Try the routine provider (opencode/local — $0), then walk the configured
        # fallback CHAIN: the owner's CLI subscriptions (claude → codex) before the
        # cloud gateway (default). opencode boots a fresh runtime per call and flakes
        # under the long-running poller, so the chain backs it up — the owner always
        # gets an answer. The chain is the mockable ``_fallback_chain`` seam.
        attempts: list[tuple] = []
        if decision.provider is not None:
            attempts.append((decision.provider, decision.model))
        attempts.extend(_fallback_chain(config, decision.provider))
        if not attempts:
            raise ProviderNotConfigured("router resolved no provider for telegram turn")

        last_exc = None
        for prov, mdl in attempts:
            try:
                result = await prov.complete(
                    [{"role": "user", "content": text}],
                    model=mdl,
                    max_tokens=_CHAT_MAX_TOKENS,
                    system=system,
                )
            except (ProviderNotConfigured, BudgetExceeded) as exc:
                last_exc = exc
                continue
            except Exception as exc:  # a flaky provider must not end the turn
                logger.warning(
                    f"Telegram inbound: provider {getattr(prov, 'name', '?')!r} failed: {exc}"
                )
                last_exc = exc
                continue
            reply = (result.text or "").strip() or _FALLBACK_REPLY
            ep.finish(
                decision=decision.reason,
                accion="responder",
                resultado=reply,
                costo_usd=result.cost_usd,
            )
            return reply

        # Every provider (routine + fallback chain) failed — defer, keep the episode.
        ep.finish(
            decision=_DEFERRED_DECISION,
            accion="responder",
            resultado=f"deferred: all providers failed ({last_exc})",
        )
        return _FALLBACK_REPLY

    except Exception as exc:  # never raise into the poll loop
        logger.warning(f"Telegram inbound: brain think failed: {exc}")
        ep.finish(
            decision="telegram: error",
            accion="responder",
            resultado=f"error: {type(exc).__name__}: {exc}",
        )
        return _FALLBACK_REPLY
    finally:
        # Always log the episode; a logging failure must not break the turn.
        with contextlib.suppress(Exception):
            await ep.append()


async def _answer(text: str, *, config) -> None:
    """Answer one message and reply with it. Never raises.

    A slash command is handled by the modular command registry
    (:mod:`lhassa.notify.commands`) — a fast, LLM-free local reply, no brain
    episode. Anything else (plain language, or an unrecognised command that the
    registry already turned into a help hint) falls through to the tool-less brain
    think only when the registry returns ``None`` (i.e. it was not a command).
    """
    reply = None
    try:
        reply = await commands.dispatch(text, config=config)
    except Exception as exc:  # dispatch is defensive, but belt-and-braces
        logger.warning(f"Telegram inbound: command dispatch failed: {exc}")
        reply = None

    if reply is None:
        # Not a command → let the brain answer (the existing plain-language path).
        reply = await _brain_reply(text, config=config)

    try:
        await telegram.send(reply, config=config)
    except Exception as exc:
        logger.warning(f"Telegram inbound: reply send failed: {exc}")


async def poll_once(config=None, *, offset=None):
    """Run one ``getUpdates`` cycle. Returns the next offset to use.

    Gated + defensive: when not enabled/configured it returns ``offset`` unchanged
    and makes **no network call**. Accepts only messages from the configured chat
    id, answers each through the brain, and advances the offset past every update
    seen (so even ignored updates are not re-fetched). Never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    if not _enabled(config):
        return offset

    token = (getattr(config, "telegram_bot_token", "") or "").strip()
    allowed_chat = str(getattr(config, "telegram_chat_id", "") or "").strip()
    url = _GET_UPDATES_URL.format(token=token)
    params: dict = {"timeout": _LONG_POLL_SECONDS}
    if offset is not None:
        params["offset"] = offset

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, params=params)
    except Exception as exc:  # transport error — keep the offset, let caller retry
        logger.warning(f"Telegram inbound: getUpdates failed (transport): {exc}")
        return offset

    if response.status_code != 200:
        logger.warning(
            f"Telegram inbound: getUpdates HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
        return offset

    try:
        data = response.json()
    except Exception:
        logger.warning("Telegram inbound: could not decode getUpdates JSON")
        return offset

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(f"Telegram inbound: getUpdates rejected: {str(data)[:200]}")
        return offset

    updates = data.get("result") or []
    new_offset = offset
    for update in updates:
        # Advance past every update we see, even malformed/ignored ones, so the
        # next cycle does not re-fetch them.
        try:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                new_offset = update_id + 1

            message = update.get("message") or update.get("edited_message")
            if not isinstance(message, dict):
                continue

            chat_id = str((message.get("chat") or {}).get("id", "")).strip()
            if chat_id != allowed_chat:
                # Security: answer nobody but the owner.
                logger.info(f"Telegram inbound: ignoring message from chat {chat_id!r}")
                continue

            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                continue

            await _answer(text.strip(), config=config)
        except Exception as exc:  # one bad update must not kill the loop
            logger.warning(f"Telegram inbound: skipping bad update: {exc}")
            continue

    return new_offset


async def run_forever(config=None) -> None:
    """Poll forever, calling :func:`poll_once` with exponential backoff on error.

    When polling is disabled or unconfigured this logs once and returns
    immediately — no network, no loop.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    if not _enabled(config):
        logger.info(
            "Telegram inbound poller not started "
            "(telegram_poll_enabled off or token/chat id missing)."
        )
        return

    logger.info("Telegram inbound poller started.")
    offset = None
    backoff = _BACKOFF_START
    while True:
        try:
            new_offset = await poll_once(config=config, offset=offset)
            if new_offset != offset:
                offset = new_offset
            backoff = _BACKOFF_START  # a clean cycle resets the backoff
        except Exception as exc:  # poll_once is defensive, but belt-and-braces
            logger.warning(f"Telegram inbound: poll cycle error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


if __name__ == "__main__":
    asyncio.run(run_forever())
