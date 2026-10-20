# SPDX-License-Identifier: AGPL-3.0-or-later
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

Run standalone with ``python -m lhassa.notify.telegram_inbound`` (the
``lhassa-telegram`` systemd unit runs exactly that); it is deliberately a
separate process from ``main.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import re
import shutil  # noqa: F401 - re-exported so callers/tests can monkeypatch
import time
from collections import deque
from typing import Optional

import httpx
from loguru import logger

from lhassa import persona as _persona
from lhassa.notify import commands, inbound_hooks, telegram

# The getUpdates endpoint. ``<token>`` is interpolated per call.
_GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"

# Long-poll seconds passed to Telegram; the client timeout sits a little above it.
_LONG_POLL_SECONDS = 30
_TIMEOUT = float(_LONG_POLL_SECONDS) + 15.0

# Backoff bounds for run_forever when a cycle errors out (seconds).
_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0

# Process-local guard against Telegram redelivering an ``update_id`` we already
# dispatched. The offset param sent on the NEXT getUpdates call is the only
# thing that tells Telegram "stop sending me this"; Telegram's own docs warn
# that updates already delivered may still show up again "for a short period
# of time" around an offset change, and this poller's ``offset`` is an
# in-memory variable in ``run_forever`` — never persisted to disk — so it is
# also lost on a crash/restart between "processed the batch" and "sent the
# advanced offset back". Either way the same update can reach this loop twice.
# A bounded, process-local seen-set is the cheap fix: it cannot survive a
# restart (nothing here claims otherwise — the poller is a single process, and
# that is the documented scope), but it stops the two realistic single-process
# cases: a resend racing the offset ack, and a retried cycle after a transient
# local error that still landed the previous batch. Bounded so it cannot grow
# unbounded across a long-lived process.
_SEEN_UPDATE_IDS_MAXLEN = 2048
_seen_update_ids: "deque[int]" = deque(maxlen=_SEEN_UPDATE_IDS_MAXLEN)
_seen_update_ids_set: set[int] = set()


def _mark_update_seen(update_id: int) -> bool:
    """Record ``update_id`` as processed; return True if it was already seen.

    Bounded FIFO: once the deque evicts an old id past ``maxlen``, it is
    dropped from the set too, so memory never grows unbounded. Never raises.
    """
    if update_id in _seen_update_ids_set:
        return True
    if len(_seen_update_ids) >= _SEEN_UPDATE_IDS_MAXLEN:
        oldest = _seen_update_ids[0]
        _seen_update_ids_set.discard(oldest)
    _seen_update_ids.append(update_id)
    _seen_update_ids_set.add(update_id)
    return False


def reset_seen_updates() -> None:
    """Forget every processed ``update_id``.

    Process-local state outlives a single poll, which is the whole point in
    production and a hazard anywhere a fresh poller is expected: two runs in one
    process (the test suite, a REPL) would silently drop the second one's
    updates as duplicates. The suite calls this between tests.
    """
    _seen_update_ids.clear()
    _seen_update_ids_set.clear()


def reset_conversation_state() -> None:
    """Forget every chat's in-memory conversation state, hooks included.

    Mirrors :func:`reset_seen_updates`: the poller is meant to be one
    long-lived process, so ``_CHAT_HISTORY`` / ``_CHAT_TURNS`` / ``_CHAT_TOPIC``
    are plain module-level dicts keyed by chat id, never reset, and so is any
    per-chat state an inbound hook keeps (the cv pack's held CV and armed
    base-CV request). A test suite reusing small chat ids across tests is not
    one long-lived process, so without this, a later test can silently inherit
    an earlier test's state. Call between tests, same as ``reset_seen_updates``.
    """
    _CHAT_HISTORY.clear()
    _CHAT_TURNS.clear()
    _CHAT_TOPIC.clear()
    inbound_hooks.reset_all()

# A chat turn is a short answer, not a long job. The actual per-turn cap now
# comes from the resolved effort profile (Plan 4, Stream 1 — see
# ``lhassa.notify.task_router``); this stays only as the retry floor below.
# A reasoning model (deepseek V4 Flash) spends its budget THINKING before it
# writes; when the cap is hit mid-thought the content comes back EMPTY with real
# tokens billed. One bounded retry with a bigger cap almost always recovers it.
_CHAT_MAX_TOKENS_RETRY = 3072
_EMPTY_REPLY = (
    "My reply came out empty, I overthought it. Ask me again (shorter works "
    "best) and I'll answer directly."
)

# Short fallback when the brain can't reach a model — sent verbatim to the chat.
_FALLBACK_REPLY = "I can't reach my model right now, give it a moment and ask me again."

# Marker for an episode whose model could not run (mirrors core/step + voice).
_DEFERRED_DECISION = "deferred: telegram needs judgment"

# Chat-output instruction layered on the persona (plain text for a chat bubble).
# The honesty block exists because this turn is TOOL-LESS: without it the model
# roleplays capabilities it lacks ("I'm searching…", "I'll update you") — promises
# nothing will ever keep. The [[research: …]] marker is the ONE real escape hatch:
# the poller mints a research_reply task from it (see _extract_research) and the
# heartbeat delivers actual results in a follow-up message.
_CHAT_INSTRUCTION = (
    "You are answering in a Telegram chat with your owner. Reply in short, natural "
    "messages. Lead with the answer and keep it brief. Never use an em dash; use a "
    "colon, a semicolon or a comma. Write the answer itself, never your reasoning "
    "about it.\n"
    "IMPORTANT: you have NO tools in this turn; no web access, no search, no "
    "files. Never claim to be searching or browsing, and never promise to report "
    "back on your own, you cannot. For questions that need LIVE or current "
    "information (news, prices, weather, releases), do not answer from memory: "
    "say in one short sentence that you'll look it up, and include this marker on "
    "its own line: [[research: <a concise web search query>]], the system runs "
    "the search and messages the results shortly. The query must be a CONCRETE "
    "search-engine query, never the user's vague phrase verbatim: name the topic, "
    "add specifics (product, place, month/year). 'dame las noticias de hoy' → "
    "[[research: technology news July 2026]], not [[research: noticias de hoy]]. "
    "At most one marker per reply.\n"
    "For an OPERATIONAL request that matches one of your registered actions "
    "(your live-configuration card lists them), include this marker on its own "
    "line: [[action: <name> key=value …]], the system mints a signed task, the "
    "allowlist and approval frontier apply, and the result arrives as a "
    "follow-up message. Never claim the action already ran, it runs after "
    "this reply. At most one action marker per reply. When the owner asks you "
    "to change one of your settings in plain language, that IS an operational "
    "request: [[action: set_config key=<setting> value=<new value>]], the "
    "editable keys are in your live-configuration card; quote a value that "
    'contains spaces (value="a, b").'
)

# The research marker the chat model may emit (see _CHAT_INSTRUCTION). Parsed
# defensively: first match wins, every occurrence is stripped from the visible text.
_RESEARCH_RE = re.compile(r"\[\[\s*research\s*:\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)

# Cap on a chat-born research query (mirrors actionpack._research._MAX_QUERY).
_RESEARCH_MAX_QUERY = 300

# Appended when a research task was actually minted — the promise the marker makes
# is only ever written by US, deterministically, after the mint succeeded.
_RESEARCH_ACK = "🔎 Looking it up, I'll message you shortly."

# The action marker (see _CHAT_INSTRUCTION): ``[[action: <name> key=value …]]``.
# Same defensive parsing as research: first match wins, all occurrences stripped.
_ACTION_MARKER_RE = re.compile(
    r"\[\[\s*action\s*:\s*([a-z0-9_]+)\s*(.*?)\s*\]\]", re.IGNORECASE | re.DOTALL
)
# Acks are written by US, deterministically, only after the mint succeeded —
# the marker's promise is never the model's to make (mirrors _RESEARCH_ACK).
_ACTION_ACK_RUN = "⚙️ Running {name}, the result follows shortly."
_ACTION_ACK_PARKED = "⏳ {name} needs your approval, see /approvals."

# The remember marker (offered by the periodic nudge in ``_brain_reply``): the
# model flags ONE durable owner fact; we persist it to the grande (deduped) and
# strip it from the visible reply. Same defensive parsing as the research marker.
_REMEMBER_MARKER_RE = re.compile(
    r"\[\[\s*remember\s*:\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL
)
_REMEMBER_MAX = 300

_MEMORY_NUDGE = (
    "If this conversation taught you a DURABLE fact about your owner (a "
    "preference, a project, a constraint, not small talk), append this marker "
    "at the very end: [[remember: <one concise fact>]]. At most one."
)

# ── rolling per-chat conversation history (process-memory only) ────────────────
# The poller serves a single owner chat, but the store is keyed by chat id anyway
# so isolated test configs never share state. Restarting the poller clears it —
# deliberate: the durable memory lives in the grande, not here.
_CHAT_HISTORY: dict = {}
_CHAT_TURNS: dict = {}

# Per-message cap when threading history back into the prompt (chars).
_HISTORY_MSG_CAP = 1500


def _history_key(config) -> str:
    return str(getattr(config, "telegram_chat_id", "") or "owner")


# ── sticky conversation topic (process-memory only, mirrors _CHAT_HISTORY) ──
# After ANY deterministic capability answers a message (the calendar
# short-circuit, an active ``apply_route``), the thread's TOPIC is recorded
# here — capability name + a small carried-over context — so a connector-led
# elliptical follow-up ("Y el sabado?") can be re-routed to THAT capability's
# own :attr:`lhassa.notify.capabilities.Capability.continue_followup` instead
# of falling to a tool-less think + the research bridge. TTL'd on read exactly
# like the chat history (a stale topic never leaks into an unrelated later
# message); restarting the poller clears it — deliberate, nothing durable
# lives here.
_CHAT_TOPIC: dict = {}
_CHAT_TOPIC_TTL_DEFAULT = 900.0  # 15 min


def _topic_key(config, chat: str = "") -> str:
    return str(chat) if chat else _history_key(config)


def _topic_set(
    config, chat: str, capability: str, context: Optional[dict] = None
) -> None:
    """Record ``capability`` as the chat's current sticky topic. Never raises."""
    try:
        _CHAT_TOPIC[_topic_key(config, chat)] = {
            "capability": capability,
            "context": dict(context or {}),
            "ts": time.time(),
        }
    except Exception:
        pass


def _topic_get(config, chat: str = "") -> Optional[dict]:
    """The chat's still-fresh sticky topic (``{capability, context, ts}``), or
    ``None`` if there is none / it expired. TTL-checked on read; never raises."""
    try:
        entry = _CHAT_TOPIC.get(_topic_key(config, chat))
        if not entry:
            return None
        ttl = float(
            getattr(config, "chat_topic_ttl", _CHAT_TOPIC_TTL_DEFAULT)
            or _CHAT_TOPIC_TTL_DEFAULT
        )
        if time.time() - entry.get("ts", 0) > ttl:
            return None
        return entry
    except Exception:
        return None


def _history_messages(config) -> list[dict]:
    """The still-fresh history as ``[{role, content}, …]``, oldest first.

    TTL is applied on read (an old conversation never leaks into a new one) and
    each message is capped so a pasted wall of text can't flood the prompt.
    """
    ttl = float(getattr(config, "telegram_history_ttl", 7200.0) or 7200.0)
    now = time.time()
    entries = [
        e for e in _CHAT_HISTORY.get(_history_key(config), []) if now - e["ts"] <= ttl
    ]
    return [
        {"role": e["role"], "content": str(e["content"])[:_HISTORY_MSG_CAP]}
        for e in entries
    ]


def _history_record(config, role: str, content: str) -> None:
    """Append one turn, trimming to the configured number of user+assistant pairs."""
    if not content:
        return
    turns = int(getattr(config, "telegram_history_turns", 8) or 8)
    key = _history_key(config)
    entries = _CHAT_HISTORY.setdefault(key, [])
    entries.append({"role": role, "content": content, "ts": time.time()})
    max_entries = max(2, turns * 2)
    if len(entries) > max_entries:
        del entries[: len(entries) - max_entries]


async def _apply_remember_marker(reply: str, *, instance_id: str) -> str:
    """Persist a ``[[remember: …]]`` fact to the grande (deduped); strip the marker.

    Best-effort on every branch — a memory failure never breaks the chat turn.
    Dedupe: a case-insensitive substring overlap with an existing fact (either
    direction) skips the write, so the model re-noticing the same thing doesn't
    pile up near-duplicates.
    """
    if not reply or "[[" not in reply:
        return reply
    match = _REMEMBER_MARKER_RE.search(reply)
    if match is None:
        return reply
    fact = " ".join(match.group(1).split())[:_REMEMBER_MAX]
    visible = _REMEMBER_MARKER_RE.sub("", reply).strip()
    if not fact:
        return visible or reply
    try:
        from lhassa.memory import facts as _facts

        existing = await _facts.load_facts(instance_id)
        low = fact.lower()
        for row in existing:
            other = str(getattr(row, "contenido", "") or "").lower()
            if other and (low in other or other in low):
                return visible  # already known — silently keep the clean reply
        await _facts.add_fact(
            instance_id,
            "dato",
            fact,
            tags=("owner", "chat"),
            procedencia="telegram-chat",
            confianza=0.7,
        )
        logger.info(f"Telegram inbound: remembered owner fact ({len(fact)} chars)")
    except Exception as exc:
        logger.warning(f"Telegram inbound: remember marker failed: {exc}")
    return visible

# Natural "remind me" trigger — routed to the /remind parser when a time phrase
# is present ("acuérdame de X a las 5pm"); time-less phrasings fall through.
_REMIND_NL_RE = re.compile(
    r"^(acu[ée]rdame|recu[ée]rdame|remind me)\b[,:]?\s*", re.IGNORECASE
)

# Deterministic remember command: "remember: X", "recuerda: X", "recordá: X".
# Handled BEFORE the watch-rules hook so it is $0 (no LLM, no task mint).
_REMEMBER_RE = re.compile(
    r'^(remember|recuerda|record[áa])[,:]?\s+(.+)',
    re.IGNORECASE | re.DOTALL,
)


def _enabled(config) -> bool:
    """True only when polling is switched on AND credentials are present."""
    return bool(getattr(config, "telegram_poll_enabled", False)) and telegram.is_configured(config)


# The fallback CHAIN builder now lives in ``lhassa.llm.router`` (the interface
# layer) so both this shell (Telegram) and the spine (``core/step.py``'s judgment
# path) can walk the SAME "owner's CLI subscriptions before cloud" chain on a
# ProviderNotConfigured/BudgetExceeded. Kept as a thin, same-signature re-export
# so existing imports/tests of ``_fallback_chain`` here are unaffected.
from lhassa.llm.router import fallback_chain as _fallback_chain  # noqa: E402


def _secondary_model(config):
    """The model the cloud fallback should use (cheap routine model)."""
    return getattr(config, "model_cheap", "") or getattr(config, "model", "") or None


async def _apply_research_marker(reply: str, *, instance_id: str, config) -> str:
    """Turn a ``[[research: …]]`` marker into a REAL minted task; strip it from view.

    The chat model may promise a lookup only through this marker (see
    ``_CHAT_INSTRUCTION``): the first marker's query is capped and minted as a
    ``research_reply`` task through ``core.dispatch.mint_task`` with the same
    HUMANO/telegram origin ``/goal`` uses — allowlist and approval frontier apply
    unchanged — and the next heartbeat runs it. Every marker occurrence is removed
    from the visible reply; the follow-up promise (``_RESEARCH_ACK``) is appended
    ONLY after the mint actually succeeded, so the owner is never promised a
    message that cannot arrive. Best-effort and never raises.
    """
    if not reply or "[[" not in reply:
        return reply
    match = _RESEARCH_RE.search(reply)
    if match is None:
        return reply
    query = " ".join(match.group(1).split())[:_RESEARCH_MAX_QUERY]
    visible = _RESEARCH_RE.sub("", reply).strip()

    minted = False
    if query:
        try:
            from datetime import datetime, timezone

            from lhassa.core import dispatch as _dispatch
            from lhassa.security.trust import Origin, TrustLevel

            params = {"query": query}
            task_id, estado = await _dispatch.mint_task(
                instance_id,
                "research_reply",
                params=params,
                origin=Origin(trust=TrustLevel.HUMANO, actor_id="telegram"),
                due_at=datetime.now(timezone.utc).isoformat(),
            )
            minted = bool(task_id)
            if task_id and estado == "pendiente":
                # No approval needed → run it NOW (fire-and-forget; the reply goes
                # out first, the results follow in seconds). A failure here leaves
                # the task pending, so the heartbeat still keeps the promise.
                asyncio.create_task(
                    _dispatch.run_task_now(
                        instance_id, task_id, "research_reply", params,
                        despertar_por="telegram",
                    )
                )
        except Exception as exc:  # a mint failure must not break the chat turn
            logger.warning(f"Telegram inbound: research mint failed: {exc}")

    if minted:
        return f"{visible}\n{_RESEARCH_ACK}".strip()
    if visible:
        return visible
    return (
        "I can't see the web from this chat turn, and the lookup path is "
        "unavailable right now, ask me again in a bit."
    )


def _parse_action_params(raw: str) -> dict:
    """Parse ``key=value key2="two words"`` marker params. Defensive: any
    unparseable tail yields the pairs parsed so far; bare tokens are ignored."""
    import shlex

    params: dict = {}
    try:
        tokens = shlex.split(raw or "")
    except ValueError:
        tokens = (raw or "").split()
    for tok in tokens:
        if "=" in tok:
            key, _, value = tok.partition("=")
            if key.strip():
                params[key.strip()] = value
    return params


async def _run_action_and_report(
    instance_id: str, task_id: str, name: str, params: dict, config
) -> None:
    """Run a chat-minted action NOW and message the outcome back. Never raises.

    Same early-runner semantics as the research bridge (``run_task_now`` — a
    failure leaves the task pending so the heartbeat retries); the follow-up
    note carries the action's own episode ``resultado`` so the owner sees WHAT
    happened, not just that something did. The note is a WORK PRODUCT, so it
    travels through :func:`lhassa.org.deliver.deliver` — Lhassa as the sender
    of record, one logged hop — never a surface-direct send; a deliver failure
    degrades to the plain channel so the owner is still told.
    """
    from lhassa.core import db as _db
    from lhassa.core import dispatch as _dispatch

    ok = await _dispatch.run_task_now(
        instance_id, task_id, name, params, despertar_por="telegram"
    )
    if ok:
        note = f"⚙️ {name}: done."
        with contextlib.suppress(Exception):
            recent = await _db.load_recent_episodes(instance_id, 5)
            resultado = next(
                (str(e.get("resultado") or "") for e in recent if e.get("accion") == name),
                "",
            ).strip()
            if resultado:
                note = f"⚙️ {name}: {resultado[:300]}"
    else:
        note = f"⚙️ {name}: didn't run, left pending for the next heartbeat."
    try:
        from lhassa.org.deliver import deliver as _deliver

        sent, _ = await _deliver(
            note, kind="action-result",
            metadata={"accion": name, "task_id": task_id},
            config=config,
        )
    except Exception as exc:
        logger.warning(f"Telegram inbound: deliver seam failed ({exc}); plain send")
        sent = False
    if not sent:
        with contextlib.suppress(Exception):
            from lhassa.notify import telegram as _telegram

            await _telegram.send(note, config=config)


async def _apply_action_marker(reply: str, *, instance_id: str, config) -> str:
    """Turn an ``[[action: …]]`` marker into a REAL minted task; strip it from view.

    Mirrors :func:`_apply_research_marker`: the first marker wins, every
    occurrence is stripped, and the ack line is appended ONLY after the mint
    actually succeeded. The action must exist in the ACTIONS registry; the mint
    itself walks the same signed-task allowlist + approval frontier as ``/goal``
    (HUMANO/telegram origin) — an unknown or disallowed action is refused with
    an honest line, never silently dropped. Best-effort and never raises.
    """
    if not reply or "[[" not in reply:
        return reply
    match = _ACTION_MARKER_RE.search(reply)
    if match is None:
        return reply
    name = match.group(1).lower()
    params = _parse_action_params(match.group(2))
    visible = _ACTION_MARKER_RE.sub("", reply).strip()

    ack = ""
    try:
        from datetime import datetime, timezone

        from lhassa import actionpack
        from lhassa.core import dispatch as _dispatch
        from lhassa.core.actions import ACTIONS
        from lhassa.security.trust import Origin, TrustLevel

        actionpack.register_all()  # idempotent — the poller may precede a beat
        if name not in ACTIONS:
            ack = f"(I don't have an action named {name!r}, see /capabilities.)"
        else:
            task_id, estado = await _dispatch.mint_task(
                instance_id,
                name,
                params=params,
                origin=Origin(trust=TrustLevel.HUMANO, actor_id="telegram"),
                due_at=datetime.now(timezone.utc).isoformat(),
            )
            if task_id and estado == "pendiente":
                asyncio.create_task(
                    _run_action_and_report(instance_id, task_id, name, params, config)
                )
                ack = _ACTION_ACK_RUN.format(name=name)
            elif task_id:
                ack = _ACTION_ACK_PARKED.format(name=name)
            else:
                ack = f"({name} was refused by the allowlist.)"
    except Exception as exc:  # a mint failure must not break the chat turn
        logger.warning(f"Telegram inbound: action mint failed: {exc}")

    if ack:
        return f"{visible}\n{ack}".strip()
    if visible:
        return visible
    return "That action path is unavailable right now, ask me again in a bit."


async def _answer_capability_shortcut(text: str, *, config):
    """Serve ``text`` through the first catalog capability whose always-on
    ``shortcut`` answers it ($0, deterministic; the calendar pack's
    ``calendar_view`` is the one today). Logs a ``despertar_por="telegram"``
    episode (``accion=<capability>``, the decision the shortcut names) and
    records the turn in chat history exactly like a normal reply.

    Returns ``(capability, reply)``, or ``None`` when no capability answers
    (including when no pack registered one), so the caller falls through to
    the unchanged path. Never raises.
    """
    from lhassa.notify import capabilities as _capabilities

    for cap in list(_capabilities.CAPABILITIES):
        if cap.shortcut is None:
            continue
        try:
            hit = await cap.shortcut(text, config=config)
        except Exception as exc:
            logger.warning(f"Telegram inbound: {cap.name} shortcut failed: {exc}")
            continue
        if not hit:
            continue
        reply, decision = hit
        if reply is None:
            continue

        try:
            from lhassa.core import db as _db_cap
            from lhassa.core.episode import Episode

            instance_id = getattr(config, "instance_id", None) or "inst_personal"
            await _db_cap.init_db()
            ep = Episode.begin(despertar_por="telegram", instance_id=instance_id)
            ep.finish(
                decision=decision,
                accion=cap.name,
                resultado=reply,
                costo_usd=0.0,
            )
            await ep.append()
        except Exception as exc:  # a logging failure must not swallow a good reply
            logger.warning(f"Telegram inbound: {cap.name} episode logging failed: {exc}")

        _history_record(config, "user", text)
        _history_record(config, "assistant", reply)
        return cap, reply
    return None


#: How much of the owner's message the episode keeps. Long enough to be worth
#: recalling, short enough that the log stays a log.
_QUESTION_CHARS = 400


def _asked(text: str) -> str:
    """The owner's message, as a suffix for the episode's ``decision``.

    The chat episode stored only the ANSWER: ``accion`` is the constant
    ``"responder"`` and the question went nowhere, so ``/recall`` and the
    episodic-recall memory layer could never find a turn by what was ASKED, on
    the one door the owner uses every day. The question cannot ride in
    ``accion`` (``core/feedback.py``, ``security/autonomy.py`` and
    ``agents/repetition.py`` all key on that exact constant to tell a chat
    answer from a real action) nor in ``resultado`` (the re-ask detector
    compares answer text for near-duplicates). ``decision`` is free-form,
    FTS5-indexed, and only ever read with ``startswith``, so a suffix is the
    one place this fits without disturbing a reader.

    Square brackets are dropped: ``decision`` also carries ``[routable:x]``
    markers that ``repetition.py`` finds with a plain regex, and a quoted
    message must not be able to forge one.
    """
    flat = " ".join((text or "").split())[:_QUESTION_CHARS]
    flat = flat.replace("[", "(").replace("]", ")")
    return f" | q: {flat}" if flat else ""


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
    from lhassa.llm.gateway import BudgetExceeded, KillSwitchActive
    from lhassa.llm.provider import ProviderNotConfigured
    from lhassa.memory import answer_cache
    from lhassa.memory import load as memory_load
    from lhassa.notify.intent import classify
    from lhassa.notify import task_router
    from lhassa.persona import load_identity

    instance_id = getattr(config, "instance_id", None) or "inst_personal"

    # Idempotent — a chat turn must work even if it wakes the brain first.
    await db.init_db()

    ep = Episode.begin(despertar_por="telegram", instance_id=instance_id)
    reply = _FALLBACK_REPLY
    try:
        # Plan 4, Stream 1 — the versatile brain's three $0 axes (task type,
        # working mode, effort level), resolved BEFORE any spend. Strips a
        # `--<level>` marker (e.g. `--deep`) from the text so it never reaches
        # the model, the cache key, or the semantic memory query.
        routing = task_router.route(text)
        text = routing.text

        # $0 intent classification BEFORE any provider/spend is resolved (doc/concepts.md:
        # "thinking is the EXCEPTION"). Only a "cacheable" (stable, non-time-sensitive)
        # question is even eligible for the answer cache; live_data/judgment/command
        # are unaffected and fall straight through to the unchanged think below.
        intent = classify(text)
        cache_enabled = bool(getattr(config, "answer_cache_enabled", True))
        cache_stats: dict = {}
        if intent == "cacheable" and cache_enabled:
            threshold = float(getattr(config, "answer_cache_threshold", 0.90) or 0.90)
            hit = await answer_cache.lookup(
                instance_id, text, threshold=threshold, config=config,
                stats=cache_stats,
            )
            if hit is not None:
                cached_answer, similarity = hit
                # A cache hit is still part of the conversation — record it in the
                # rolling history exactly like a normal turn.
                _history_record(config, "user", text)
                _history_record(config, "assistant", cached_answer)
                ep.finish(
                    decision=(
                        f"{task_router.describe(routing)} | "
                        f"cache-hit: served past answer (sim={similarity:.2f})"
                        f"{_asked(text)}"
                    ),
                    accion="responder",
                    resultado=cached_answer,
                    costo_usd=0.0,
                )
                return cached_answer

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
        # The self-card: live config + execution-modality taxonomy, $0 and
        # deterministic — the model KNOWS what it runs on and how it executes
        # (and which action names the [[action: …]] marker may name).
        try:
            from lhassa.notify import selfcard as _selfcard

            parts.append(_selfcard.build(config))
        except Exception as exc:  # a broken card must not cost the reply
            logger.warning(f"Telegram inbound: selfcard degraded: {exc}")
        parts.append(_CHAT_INSTRUCTION)

        # Rolling conversation history + the periodic memory nudge. The counter
        # increments per USER turn; on the cadence the model is invited (once)
        # to flag a durable owner fact with [[remember: …]].
        history = _history_messages(config)
        hkey = _history_key(config)
        _CHAT_TURNS[hkey] = _CHAT_TURNS.get(hkey, 0) + 1
        nudge_every = int(getattr(config, "chat_memory_nudge_turns", 6) or 6)
        if nudge_every > 0 and _CHAT_TURNS[hkey] % nudge_every == 0:
            parts.append(_MEMORY_NUDGE)
        system = "\n\n".join(parts)
        messages = [*history, {"role": "user", "content": text}]

        # Try the routine provider (opencode/local — $0), then walk the configured
        # fallback CHAIN: the owner's CLI subscriptions (claude → codex) before the
        # cloud gateway (default). opencode boots a fresh runtime per call and flakes
        # under the long-running poller, so the chain backs it up — the owner always
        # gets an answer. The chain is the mockable ``_fallback_chain`` seam.
        #
        # ``telegram_provider`` pins the CHAT door's own provider (e.g. "default" —
        # the fast API gateway) without touching the heartbeat seat that flows and
        # hard work think with; empty/"auto" keeps the router's routine decision.
        attempts: list[tuple] = []
        pinned = (getattr(config, "telegram_provider", "") or "").strip().lower()
        primary = decision.provider
        if pinned and pinned != "auto":
            try:
                from lhassa.llm.provider import get_model_provider

                primary = get_model_provider(config, name=pinned)
            except Exception as exc:  # a bad pin degrades to the routine decision
                logger.warning(
                    f"Telegram inbound: telegram_provider {pinned!r} unresolvable "
                    f"({exc}) — using the routine decision."
                )
                primary = decision.provider
        if primary is not None:
            model = (
                decision.model
                if primary is decision.provider
                else router.pick_model(primary, hard=False, config=config)
            )
            attempts.append((primary, model))
        attempts.extend(_fallback_chain(config, primary))
        if not attempts:
            raise ProviderNotConfigured("router resolved no provider for telegram turn")

        last_exc = None
        for prov, mdl in attempts:
            try:
                result = await prov.complete(
                    messages,
                    model=mdl,
                    # The declared effort budget governs the actual request —
                    # "minimal" and "deep" ask for visibly different caps, not
                    # just a different label after the fact.
                    max_tokens=routing.effort.token_budget,
                    system=system,
                )
            except KillSwitchActive as exc:
                # Manual shutdown is global: unlike a spent budget or a flaky
                # seat, it must not advance to the next configured provider.
                last_exc = exc
                break
            except (ProviderNotConfigured, BudgetExceeded) as exc:
                last_exc = exc
                continue
            except Exception as exc:  # a flaky provider must not end the turn
                logger.warning(
                    f"Telegram inbound: provider {getattr(prov, 'name', '?')!r} failed: {exc}"
                )
                last_exc = exc
                continue
            reply = (result.text or "").strip()
            if not reply and (getattr(result, "output_tokens", 0) or 0) > 0:
                # The model ANSWERED but with empty content (reasoning ate the
                # cap — observed live with deepseek V4 Flash). Retry ONCE with
                # a larger cap on the same provider; "can't reach my model"
                # would be a lie here — the model was reached and billed.
                try:
                    result = await prov.complete(
                        messages,
                        model=mdl,
                        max_tokens=max(routing.effort.token_budget, _CHAT_MAX_TOKENS_RETRY),
                        system=system,
                    )
                    reply = (result.text or "").strip()
                except Exception as exc:
                    logger.warning(f"Telegram inbound: empty-reply retry failed: {exc}")
                reply = reply or _EMPTY_REPLY
            reply = reply or _FALLBACK_REPLY
            reply = await _apply_research_marker(
                reply, instance_id=instance_id, config=config
            )
            reply = await _apply_action_marker(
                reply, instance_id=instance_id, config=config
            )
            reply = await _apply_remember_marker(reply, instance_id=instance_id)
            # The owner's no-em-dash rule, ENFORCED rather than requested. The
            # chat instruction above already asks for it and a model still ships
            # one, which is exactly why persona.scrub_dashes exists and why the
            # notify and digest passes run it. Applied here, after the markers
            # are stripped, so the scrubbed text is also what gets cached and
            # threaded into history: the rule holds on the reply the owner
            # actually reads, not just on the one the model meant to write.
            reply = _persona.scrub_dashes(reply)
            # Record the turn AS THE OWNER SAW IT (markers already stripped).
            _history_record(config, "user", text)
            _history_record(config, "assistant", reply)
            # A "cacheable" question just got a real think — store it so the NEXT
            # semantically-similar ask can be served for $0. Best-effort: a store
            # failure must never break a reply that already succeeded.
            if intent == "cacheable" and cache_enabled:
                with contextlib.suppress(Exception):
                    await answer_cache.store(instance_id, text, reply, config=config)
            # Record the provider that ACTUALLY answered — the router's reason
            # predates the telegram_provider pin and the fallback walk, and an
            # episode label that names the wrong provider poisons every later
            # read (spend analysis, the repetition detector, /recall).
            actual = getattr(prov, "name", "?")
            # The self-routing consciousness's marker: this turn actually spent
            # (a real think, and — via the [[research: …]] marker above — maybe
            # a minted research task too) on a message a deterministic
            # capability ALSO recognises but no route is on for yet. Tagging it
            # here is the router-side half of the seam; the consciousness
            # (agents.repetition.detect_routable) reads it back with plain SQL,
            # never a Python import — agents/ stays shell-free.
            from lhassa.notify import capabilities as _capabilities

            ep.finish(
                decision=(
                    f"{task_router.describe(routing)} | "
                    f"think: provider={actual} model={mdl or 'provider-default'}"
                    f"{_capabilities.tag_intent(text)}"
                    # The near-miss diagnostic (plan 0.2): when a cacheable
                    # question MISSED, record how close the cache came — days of
                    # these decide whether the fix is the threshold, traffic, or
                    # the embedder.
                    + (
                        f" cache-miss best={cache_stats['best_similarity']:.2f}"
                        f" scanned={cache_stats['scanned']}"
                        if cache_stats and not cache_stats.get("hit")
                        else ""
                    )
                    + _asked(text)
                ),
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


# ── Watch rules (message-pattern task minting) ───────────────────────────────


def _load_raw_watch_rules(config) -> list[dict]:
    """Load the RAW watch-rules JSON (pattern as a string, not yet compiled).

    This is the single source of truth for the on-disk shape
    (``[{"pattern": "<regex>", "accion": "<str>", "params": {}}]``); both the
    read-only matching path below (:func:`_load_watch_rules`) and the
    ``/watch`` / ``/watches`` / ``/unwatch`` chat commands (:mod:`lhassa.notify.
    commands`) read and write through this pair so there is exactly one format.
    Never raises: a missing/malformed file degrades to ``[]``.
    """
    path_raw = getattr(config, "watch_rules_file", "") or ""
    if not path_raw:
        return []
    import pathlib as _pathlib

    try:
        text = _pathlib.Path(path_raw).read_text(encoding="utf-8")
        raw = _json.loads(text)
    except (OSError, _json.JSONDecodeError, Exception):
        return []

    if not isinstance(raw, list):
        return []

    rules: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pattern_src = str(entry.get("pattern") or "").strip()
        accion = str(entry.get("accion") or "").strip()
        if not pattern_src or not accion:
            continue
        params = entry.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        rules.append({"pattern": pattern_src, "accion": accion, "params": dict(params)})
    return rules


def save_watch_rules(config, rules: list[dict]) -> bool:
    """Persist RAW watch rules (the ``_load_raw_watch_rules`` shape) to disk.

    Creates parent directories as needed. Returns whether the write succeeded;
    never raises (a write failure is logged and swallowed — the caller reports it
    to the chat as a friendly failure, not a crash).
    """
    path_raw = getattr(config, "watch_rules_file", "") or ""
    if not path_raw:
        return False
    import pathlib as _pathlib

    try:
        path = _pathlib.Path(path_raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(rules, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning(f"Telegram inbound: could not save watch rules: {exc}")
        return False


def _load_watch_rules(config) -> list[dict]:
    """Load watch rules with COMPILED patterns, ready for matching. Never raises.

    Returns a list of validated rule dicts: {pattern: re.Pattern, accion: str,
    params: dict}. Missing/malformed file → empty list (feature silently off).
    Invalid regex entries are skipped (an entry written by ``/watch`` was already
    validated at write time, but a hand-edited file might not be).
    """
    rules: list[dict] = []
    for entry in _load_raw_watch_rules(config):
        try:
            pattern = re.compile(entry["pattern"], re.IGNORECASE)
        except re.error:
            logger.debug(
                f"Telegram inbound: skipping invalid rule regex {entry['pattern']!r}"
            )
            continue
        rules.append(
            {"pattern": pattern, "accion": entry["accion"], "params": dict(entry["params"])}
        )
    return rules


async def _try_mint_rule_task(text: str, rules: list[dict], *, config) -> Optional[str]:
    """Try matching ``text`` against watch rules; mint the first matching task.

    Returns a short confirmation string (task id) if a rule matched and the task
    was minted, else ``None``. Never raises.
    """
    if not rules:
        return None
    try:
        from lhassa.core import db as _db
        from lhassa.core import dispatch as _dispatch
        from lhassa.security.trust import Origin, TrustLevel

        instance_id = getattr(config, "instance_id", None) or "inst_personal"
        await _db.init_db()

        for rule in rules:
            if rule["pattern"].search(text):
                origin = Origin(trust=TrustLevel.HUMANO, actor_id="telegram-rule")
                task_id, estado = await _dispatch.mint_task(
                    instance_id,
                    rule["accion"],
                    params=dict(rule["params"]),
                    origin=origin,
                    judgment=True,  # rule-minted tasks are judgment (any accion)
                )
                if task_id:
                    return task_id
                break  # rule matched but mint refused — don't try fallback rules
    except Exception as exc:
        logger.debug(f"Telegram inbound: rule task mint failed: {exc}")
    return None


# "approve:<id>" / "deny:<id>" — the inline-keyboard callback-data encoding used by
# the monitor watcher's Approve/Deny buttons (see lhassa.org.watcher._approval_keyboard).
_CALLBACK_RE = re.compile(r"^(approve|deny):(.+)$")

# The hidden marker /code appends to a successful park reply (see
# lhassa.notify.commands._CODE_APPROVAL_MARKER) — parsed here to attach the same
# inline Approve/Deny keyboard the monitor watcher's alerts already use, then
# stripped so the owner never sees it.
_CODE_APPROVAL_RE = re.compile(r"\[\[\s*approval_id\s*:\s*(\S+?)\s*\]\]")

# Hard safety cap around an approved /code run: the CLI has its own timeout
# (LHASSA_AI_CLI_TIMEOUT, default 600s), this is just a belt-and-braces ceiling so
# a fire-and-forget launch can never hang the process forever.
_CODE_RUN_TIMEOUT = 1800.0  # 30 min

# Tail of a finished run's output included in the completion message.
_CODE_OUTPUT_TAIL = 500


def _code_approval_keyboard(ap_id: str) -> dict:
    """A single-approval inline Approve/Deny row — the same shape/encoding as
    :func:`lhassa.org.watcher._approval_keyboard`, built for exactly one id."""
    return {
        "inline_keyboard": [
            [
                {"text": f"✅ Approve {ap_id}", "callback_data": f"approve:{ap_id}"},
                {"text": f"❌ Deny {ap_id}", "callback_data": f"deny:{ap_id}"},
            ]
        ]
    }


async def _launch_code_run(approval_row: dict, *, config) -> None:
    """Launch an approved ``code_run`` approval's task. Fire-and-forget; never raises.

    Reuses the EXACT seam the dashboard's Run section launches through
    (:func:`lhassa.api.code_routes._stream_run` — it broadcasts ``agent_started``/
    ``agent_token``/``agent_done`` over ``/ws`` and persists the run through
    ``lhassa.state.state``), so a Telegram-launched run is indistinguishable from a
    dashboard one. Sends a "started" message immediately, awaits completion (capped
    by :data:`_CODE_RUN_TIMEOUT` on top of the CLI's own timeout), then sends a
    final status + output-tail message. Approving twice on a resolved id is a
    caller concern upstream (the callback only fires this on an actual resolve).
    """
    payload = approval_row.get("payload") or {}
    task = str(payload.get("task") or "").strip()
    if not task:
        with contextlib.suppress(Exception):
            await telegram.send(
                "That approval had no task text on it, nothing to run.", config=config
            )
        return

    try:
        import uuid

        from lhassa.agents import local_cli
        from lhassa.api.code_routes import _stream_run
        from lhassa.state import AgentRun, state

        cli = str(payload.get("cli") or "").strip() or local_cli.detect_ai_cli()
        if not local_cli.is_installed(cli):
            installed = ", ".join(local_cli.available_clis()) or "none installed"
            await telegram.send(
                f"Can't launch, the {cli!r} CLI isn't available ({installed}).",
                config=config,
            )
            return

        agent = local_cli.make_agent(cli)
        run_id = uuid.uuid4().hex[:8]
        run = AgentRun(id=run_id, name=f"ai-cli ({cli})", status="running", task=task, cli=cli)
        state.register_agent(run)

        with contextlib.suppress(Exception):
            await telegram.send(
                f"Started: run #{run_id} ({cli}), {task[:80]}", config=config
            )

        try:
            await asyncio.wait_for(
                _stream_run(agent, run_id, run, task), timeout=_CODE_RUN_TIMEOUT
            )
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                await telegram.send(
                    f"Run #{run_id} is still going after a while, check the "
                    "dashboard's Run section.",
                    config=config,
                )
            return

        tail = (run.output or "")[-_CODE_OUTPUT_TAIL:]
        message = f"Run #{run_id} {run.status}." + (f"\n{tail}" if tail else "")
        with contextlib.suppress(Exception):
            await telegram.send(message, config=config)
    except Exception as exc:  # a launch failure must never crash the poller
        logger.warning(f"Telegram inbound: code_run launch failed: {exc}")
        with contextlib.suppress(Exception):
            await telegram.send("I couldn't launch that coding run.", config=config)


async def _handle_callback_query(
    callback_query: dict, *, config, allowed_chat: str
) -> None:
    """Handle an inline-keyboard tap (Approve/Deny on a monitor alert). Never raises.

    Parses the ``"approve:<id>"`` / ``"deny:<id>"`` callback data, resolves the
    approval through the same path as the ``/approve`` / ``/deny`` chat commands
    (:mod:`lhassa.notify.commands`), and ALWAYS answers the callback query
    (``answerCallbackQuery``) so the button's client-side loading spinner clears —
    even on a malformed payload or an unauthorized tap. A bad/malformed
    ``callback_query`` is logged and swallowed; it never crashes the poll loop.
    """
    callback_id = str(callback_query.get("id") or "") if isinstance(callback_query, dict) else ""
    try:
        from lhassa.notify import telegram_api

        message = callback_query.get("message") or {}
        chat_id = (
            str((message.get("chat") or {}).get("id", "")).strip()
            if isinstance(message, dict)
            else ""
        )
        data = str(callback_query.get("data") or "")

        if chat_id and chat_id != allowed_chat:
            # Security: honor a tap from nobody but the owner's chat.
            logger.info(f"Telegram inbound: ignoring callback from chat {chat_id!r}")
            if callback_id:
                await telegram_api.answer_callback_query(callback_id, config=config)
            return

        m = _CALLBACK_RE.match(data)
        if not m:
            logger.debug(f"Telegram inbound: unrecognised callback data {data!r}")
            if callback_id:
                await telegram_api.answer_callback_query(
                    callback_id, text="Unrecognised action.", config=config
                )
            return

        action, ap_id = m.group(1), m.group(2)
        approved = action == "approve"

        from lhassa.core import db as _db
        from lhassa.security import approval as _approval

        instance_id = getattr(config, "instance_id", None) or "inst_personal"
        await _db.init_db()
        existing = await _db.get_approval(instance_id, ap_id)
        if existing is None:
            ack_text = f"No pending approval {ap_id}."
        else:
            try:
                await _approval.resolve(
                    instance_id,
                    ap_id,
                    approved=approved,
                    by_actor="telegram",
                    approver=_approval.TrustedApprover(
                        "owner", "telegram-owner"
                    ),
                )
            except _approval.ApprovalResolutionConflict:
                # A double-tap (or a race with another surface): the approval is
                # no longer pending — acknowledge honestly and launch NOTHING.
                existing = None
                ack_text = f"Approval {ap_id} was already resolved."
            except _approval.ApprovalAuthorizationError as exc:
                existing = None
                ack_text = f"Approval refused: {exc}"
            except KeyError:
                existing = None
                ack_text = f"No pending approval {ap_id}."
            else:
                ack_text = f"{'Approved' if approved else 'Denied'} {ap_id}."

        if callback_id:
            await telegram_api.answer_callback_query(
                callback_id, text=ack_text, config=config
            )
        with contextlib.suppress(Exception):
            await telegram.send(ack_text, config=config)

        # A resolved code_run approval LAUNCHES the run — Approve only; Deny is a
        # calm acknowledgment and nothing runs. Fire-and-forget so this handler
        # (and the poll loop) is never blocked on the CLI's runtime.
        if existing is not None and approved and existing.get("tipo") == "code_run":
            asyncio.ensure_future(_launch_code_run(existing, config=config))

        # An approved mint_task (e.g. a protected-root read) runs NOW and
        # reports back over the chat — resolve() just released the task to
        # 'pendiente', so this is the same early-runner+report the [[action:]]
        # marker uses; if it fails, the task stays pending for the heartbeat.
        if existing is not None and approved and existing.get("tipo") == "task":
            _pl = existing.get("payload") or {}
            if _pl.get("source") == "mint_task" and _pl.get("task_id") and _pl.get("accion"):
                asyncio.ensure_future(
                    _run_action_and_report(
                        instance_id,
                        str(_pl["task_id"]),
                        str(_pl["accion"]),
                        dict(_pl.get("params") or {}),
                        config,
                    )
                )

        # A resolved mint_route approval TURNS ON that capability's route — the
        # self-routing consciousness's approval->apply connection. Deterministic,
        # $0, DATA only (no code is generated or executed); see
        # lhassa.notify.capabilities.apply_approved_route.
        if existing is not None and approved and existing.get("tipo") == "mint_route":
            from lhassa.notify import capabilities as _capabilities

            _capabilities.apply_approved_route(
                existing.get("payload") or {}, config=config
            )
    except Exception as exc:  # a malformed callback_query must never crash the poller
        logger.warning(f"Telegram inbound: callback_query handling failed: {exc}")
        with contextlib.suppress(Exception):
            if callback_id:
                from lhassa.notify import telegram_api as _tapi

                await _tapi.answer_callback_query(callback_id, config=config)


async def _answer(text: str, *, config, chat_id: str = "") -> None:
    """Answer one message and reply with it. Never raises.

    Order of handling:
      1. Inbound text hooks (:mod:`lhassa.notify.inbound_hooks`): a pack that
         replies with more than a string, e.g. the cv pack's ``/cv`` and its
         held-CV follow-up. A hook that handles the message ends the turn.
      2. Watch-rule matching (mint a task + reply with the id).
      3. Slash command registry (LLM-free).
      4. Brain think (the existing plain-language path).
    """
    stripped = text.strip()
    if await inbound_hooks.run_text_hooks(text, config=config, chat_id=chat_id):
        return

    if not stripped.startswith("/") and getattr(config, "digest_personal_checkin", False):
        try:
            from lhassa.core import db as _db_prompt
            from lhassa.memory import checkins as _checkins

            instance_id = getattr(config, "instance_id", None) or "inst_personal"
            await _db_prompt.init_db()
            prompt = await _db_prompt.pending_owner_prompt(
                instance_id,
                kind="personal_checkin",
            )
            if prompt:
                item = await _checkins.record(
                    instance_id,
                    stripped,
                    slot=prompt.get("slot") or None,
                    source="telegram-checkin-reply",
                )
                await _db_prompt.consume_owner_prompt(
                    instance_id,
                    prompt["id"],
                    answer=stripped[:1000],
                )
                await telegram.send(f"Noted for the day: {item.id}", config=config)
                return
        except Exception as exc:
            logger.warning(f"Telegram inbound: check-in reply capture failed: {exc}")

    command_name, _ = commands.parse(stripped)

    # Deterministic "acuérdame/remind me" shortcut — the owner's natural phrase
    # routes straight to the /remind parser ($0, no LLM): "acuérdame de un
    # compromiso a las 5pm" just works. Only when a TIME phrase parses; a
    # time-less "acuérdame que…" falls through to the remember/fact paths below.
    if not stripped.startswith("/"):
        m_remind = _REMIND_NL_RE.match(stripped)
        if m_remind:
            rest = stripped[m_remind.end():].strip()
            if commands.parse_remind(rest) is not None:
                handler = commands.COMMANDS.get_or_none("remind")
                if handler is not None:
                    reply = await handler(rest, config=config)
                    await telegram.send(reply, config=config)
                    return

    # Deterministic capability shortcuts: "¿qué hay en mi calendario el
    # viernes?" / "what do I have tomorrow?" is answered straight from the
    # calendar pack's shortcut: a date parse + an event lookup + a template
    # reply, $0, NO model, NEVER the tool-less think below (and never the
    # [[research: ...]] bridge, since it runs before that think is even
    # attempted). Anything no shortcut answers falls through unchanged; with
    # no pack installed the catalog is empty and this is a no-op.
    if not stripped.startswith("/"):
        shortcut_hit = await _answer_capability_shortcut(stripped, config=config)
        if shortcut_hit is not None:
            from lhassa.notify import capabilities as _capabilities

            hit_cap, shortcut_reply = shortcut_hit
            # This turn IS the capability's topic now: a connector-led
            # follow-up ("Y el sabado?") should stay here rather than fall to
            # the think. The topic context (the calendar's DAY) lets a
            # bare-time follow-up ("¿y a las 6am?") resolve against the right
            # day instead of hallucinating.
            _topic_set(
                config,
                chat_id,
                hit_cap.name,
                _capabilities.topic_context(hit_cap, stripped),
            )
            await telegram.send(shortcut_reply, config=config)
            return

    # Deterministic "remember" shortcut — $0, no LLM, no task.
    # Handled before watch-rules so it short-circuits cleanly.
    if not stripped.startswith("/"):
        m = _REMEMBER_RE.match(stripped)
        if m:
            text_to_remember = m.group(2).strip()[:500]
            try:
                from lhassa.core import db as _db_remember
                from lhassa.memory.facts import add_fact as _add_fact_remember
                _instance_id = getattr(config, "instance_id", None) or "inst_personal"
                await _db_remember.init_db()
                _fact = await _add_fact_remember(
                    _instance_id,
                    tipo="dato",
                    contenido=text_to_remember,
                    procedencia="telegram-remember",
                    confianza=0.95,
                    tags=["telegram"],
                )
                await telegram.send(f"Remembered: {_fact.id}", config=config)
            except Exception as _exc:
                logger.warning(f"Telegram inbound: remember failed: {_exc}")
                with contextlib.suppress(Exception):
                    await telegram.send("Failed to remember that.", config=config)
            return

    # The self-routing consciousness: an APPROVED capability route is served
    # here for $0, before the command/brain fallback. A capability with an
    # always-on shortcut (calendar_view) is already served above regardless of
    # this table, so this mostly guards OTHER capabilities /
    # research mis-routes once an owner turns one on via an approved
    # "mint_route" proposal. See lhassa.notify.capabilities.
    if not stripped.startswith("/"):
        from lhassa.notify import capabilities as _capabilities

        matched_cap = _capabilities.match(stripped)
        routed_reply = await _capabilities.apply_route(stripped, config=config)
        if routed_reply is not None:
            if matched_cap:
                _topic_set(config, chat_id, matched_cap)
            await telegram.send(routed_reply, config=config)
            return

    # The follow-up thread-continuity hook — a connector-led elliptical
    # follow-up ("Y el sabado?", "and Saturday?") continues the STICKY topic
    # left by the last capability that answered above (calendar today, more
    # later), instead of falling through to a tool-less think + the research
    # bridge. $0, deterministic: only fires when (a) the message itself reads
    # as a follow-up (capabilities.is_followup), (b) a fresh topic exists, and
    # (c) that capability actually declares a continue_followup. Any "no"
    # (including the capability's own continuation declining with None —
    # e.g. no parseable time reference in the follow-up) degrades straight
    # through to the unchanged think below.
    if not stripped.startswith("/"):
        from lhassa.notify import capabilities as _capabilities

        if _capabilities.is_followup(stripped):
            topic = _topic_get(config, chat_id)
            if topic is not None:
                cap = _capabilities.get_capability(topic.get("capability", ""))
                if cap is not None and cap.continue_followup is not None:
                    try:
                        followup_reply = await cap.continue_followup(
                            stripped, topic.get("context") or {}, config=config
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Telegram inbound: thread-continue {cap.name!r} "
                            f"failed: {exc}"
                        )
                        followup_reply = None
                    if followup_reply is not None:
                        try:
                            from lhassa.core import db as _db_topic
                            from lhassa.core.episode import Episode as _TopicEpisode

                            instance_id = (
                                getattr(config, "instance_id", None) or "inst_personal"
                            )
                            await _db_topic.init_db()
                            ep = _TopicEpisode.begin(
                                despertar_por="telegram", instance_id=instance_id
                            )
                            ep.finish(
                                decision=f"thread-continue: {cap.name}",
                                accion=cap.name,
                                resultado=followup_reply,
                                costo_usd=0.0,
                            )
                            await ep.append()
                        except Exception as exc:
                            logger.warning(
                                "Telegram inbound: thread-continue episode "
                                f"logging failed: {exc}"
                            )
                        _history_record(config, "user", stripped)
                        _history_record(config, "assistant", followup_reply)
                        # Keep the sticky topic, refreshing its context if
                        # THIS follow-up moved it (the calendar day: "y el
                        # sábado?") so the next bare-time follow-up targets
                        # the right day.
                        new_ctx = dict(topic.get("context") or {})
                        moved = _capabilities.topic_context(cap, stripped)
                        new_ctx.update({k: v for k, v in moved.items() if v})
                        _topic_set(config, chat_id, cap.name, new_ctx)
                        await telegram.send(followup_reply, config=config)
                        return

    # Watch-rule matching: check rules BEFORE the command/brain fallback. A match
    # mints a task and confirms with the task id, then returns (does not fall through).
    if not stripped.startswith("/"):
        rules = _load_watch_rules(config)
        if rules:
            task_id = await _try_mint_rule_task(stripped, rules, config=config)
            if task_id:
                try:
                    await telegram.send(
                        f"Noted. Task {task_id} minted.",
                        config=config,
                    )
                except Exception as exc:
                    logger.warning(f"Telegram inbound: rule reply send failed: {exc}")
                return

    reply = None
    try:
        reply = await commands.dispatch(text, config=config)
    except Exception as exc:  # dispatch is defensive, but belt-and-braces
        logger.warning(f"Telegram inbound: command dispatch failed: {exc}")
        reply = None

    if reply is None:
        # Not a command → let the brain answer (the existing plain-language path).
        reply = await _brain_reply(text, config=config)
    elif command_name == "code":
        # A successful /code park carries a hidden marker with the fresh approval
        # id (see lhassa.notify.commands._CODE_APPROVAL_MARKER) — attach the same
        # inline Approve/Deny keyboard the monitor watcher's alerts already use,
        # strip the marker, and send directly (this reply needs reply_markup, which
        # the plain send() below does not attach).
        m = _CODE_APPROVAL_RE.search(reply)
        if m:
            ap_id = m.group(1)
            visible = _CODE_APPROVAL_RE.sub("", reply).strip()
            try:
                await telegram.send(
                    visible, config=config, reply_markup=_code_approval_keyboard(ap_id)
                )
            except Exception as exc:
                logger.warning(f"Telegram inbound: /code reply send failed: {exc}")
            return

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

    This is the OFFSET-ONLY view, kept for callers that only care about the
    cursor. It cannot distinguish a failed cycle from an idle one — both return
    ``offset`` unchanged — so a polling LOOP must use :func:`poll_cycle`, which
    reports success separately. See that function for why.
    """
    cycle_offset, _ok = await poll_cycle(config=config, offset=offset)
    return cycle_offset


async def poll_cycle(config=None, *, offset=None):
    """One ``getUpdates`` cycle as ``(next_offset, ok)``.

    ``ok`` is False when the cycle failed (transport error or a non-200), True
    otherwise — INCLUDING a clean long-poll that simply saw no updates.

    This distinction is the whole point. :func:`poll_once` returns ``offset``
    unchanged both when the network is down and when nothing happened, so a
    loop built on it cannot tell "broken" from "idle" and spins as fast as the
    failures come back. With DNS down that measured ~45 getUpdates attempts per
    second — a laptop burning CPU and battery and flooding the journal, while
    ``run_forever``'s exponential backoff sat unused because ``poll_once``
    never raised. Returning ``ok`` is what lets the loop actually back off.

    Never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    if not _enabled(config):
        return offset, True  # disabled is not a failure — nothing to back off from

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
        return offset, False

    if response.status_code != 200:
        logger.warning(
            f"Telegram inbound: getUpdates HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
        return offset, False

    try:
        data = response.json()
    except Exception:
        logger.warning("Telegram inbound: could not decode getUpdates JSON")
        return offset, False

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(f"Telegram inbound: getUpdates rejected: {str(data)[:200]}")
        return offset, False

    updates = data.get("result") or []
    new_offset = offset
    for update in updates:
        # Advance past every update we see, even malformed/ignored ones, so the
        # next cycle does not re-fetch them.
        try:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                new_offset = update_id + 1
                # See ``_mark_update_seen`` above: Telegram may resend an
                # update_id we already dispatched (offset ack race or a
                # process restart between processing and confirming). Any
                # dispatch path below — callback, marker-minting think,
                # /goal, /remind, /idea — would otherwise re-mint on a
                # replay, so the guard sits before every one of them.
                if _mark_update_seen(update_id):
                    logger.info(
                        f"Telegram inbound: skipping duplicate update_id={update_id}"
                    )
                    continue

            # An inline-keyboard button tap (Approve/Deny on a monitor alert).
            callback_query = update.get("callback_query")
            if isinstance(callback_query, dict):
                await _handle_callback_query(callback_query, config=config, allowed_chat=allowed_chat)
                continue

            message = update.get("message") or update.get("edited_message")
            if not isinstance(message, dict):
                continue

            chat_id = str((message.get("chat") or {}).get("id", "")).strip()
            if chat_id != allowed_chat:
                # Security: answer nobody but the owner.
                logger.info(f"Telegram inbound: ignoring message from chat {chat_id!r}")
                continue

            # An uploaded document goes to the inbound document hooks (the cv
            # pack's CV bridge, when installed); with no hook that takes it, the
            # document is skipped. A plain text message keeps the brain path.
            document = message.get("document")
            if isinstance(document, dict) and document.get("file_id"):
                caption = message.get("caption") or message.get("text")
                handled = await inbound_hooks.run_document_hooks(
                    document, caption, config=config, chat_id=chat_id
                )
                if not handled:
                    logger.info("Telegram inbound: no hook handled an uploaded document")
                continue

            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                continue

            await _answer(text.strip(), config=config, chat_id=chat_id)
        except Exception as exc:  # one bad update must not kill the loop
            logger.warning(f"Telegram inbound: skipping bad update: {exc}")
            continue

    return new_offset, True


async def run_forever(config=None) -> None:
    """Poll forever, calling :func:`poll_once` with exponential backoff on error.

    When polling is disabled or unconfigured this logs once and returns
    immediately — no network, no loop.
    """
    from lhassa import actionpack
    actionpack.register_all()

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

    # Best-effort: publish the bot's command menu once on start. Guarded so a slow or
    # failing setMyCommands can never block or crash the poller's startup path.
    with contextlib.suppress(Exception):
        from lhassa.notify import telegram_api

        # The menu IS the registry: every registered command (described first,
        # curated order — commands.menu_entries shares /help's one source), plus
        # /cv, the bridge command handled ahead of the registry in _answer, so
        # it is not in COMMANDS but very much answers.
        menu = [("cv", "Tailor your CV to a job posting")]
        menu.extend(commands.menu_entries())
        await telegram_api.set_my_commands(menu, config=config)

    offset = None
    backoff = _BACKOFF_START
    while True:
        try:
            new_offset, ok = await poll_cycle(config=config, offset=offset)
            if new_offset != offset:
                offset = new_offset
            if ok:
                backoff = _BACKOFF_START  # a clean cycle resets the backoff
            else:
                # poll_cycle already logged WHY. Without this sleep the loop
                # spins at the speed of the failure (measured ~45 attempts/s
                # with DNS down) — the backoff below existed but could never
                # fire, because poll_once swallows transport errors instead of
                # raising. Sleep BEFORE growing it so the first retry is quick.
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
        except Exception as exc:  # poll_cycle is defensive, but belt-and-braces
            logger.warning(f"Telegram inbound: poll cycle error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


if __name__ == "__main__":
    asyncio.run(run_forever())
