# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three reply markers a chat think may emit, made real.

The chat think is TOOL-LESS; these markers are its only escape hatches, and
each promise is written by US, deterministically, only after the real thing
happened:

* ``[[research: <query>]]`` mints a ``research_reply`` task (the next
  heartbeat, or :func:`lhassa.core.dispatch.run_task_now` right away, runs one
  web search and messages the results);
* ``[[action: <name> key=value ...]]`` mints a registered action through the
  signed-task allowlist and approval frontier, and a no-approval action runs
  now and reports its episode ``resultado`` back;
* ``[[remember: <fact>]]`` persists one durable owner fact to the grande.

Every function here strips its marker from the visible reply and never raises.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Optional

from loguru import logger

from lhassa.chat.outbound import resolve_sender, say
from lhassa.chat.types import CHAT_SOURCE, Sender

# The research marker the chat model may emit (see think._CHAT_INSTRUCTION).
# Parsed defensively: first match wins, every occurrence is stripped.
_RESEARCH_RE = re.compile(r"\[\[\s*research\s*:\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)

# Cap on a chat-born research query (mirrors actionpack._research._MAX_QUERY).
_RESEARCH_MAX_QUERY = 300

# Appended when a research task was actually minted: the promise the marker
# makes is only ever written by US, deterministically, after the mint succeeded.
_RESEARCH_ACK = "🔎 Looking it up, I'll message you shortly."

# The action marker: ``[[action: <name> key=value ...]]``. Same defensive
# parsing as research: first match wins, all occurrences stripped.
_ACTION_MARKER_RE = re.compile(
    r"\[\[\s*action\s*:\s*([a-z0-9_]+)\s*(.*?)\s*\]\]", re.IGNORECASE | re.DOTALL
)
# Acks are written by US, deterministically, only after the mint succeeded;
# the marker's promise is never the model's to make (mirrors _RESEARCH_ACK).
_ACTION_ACK_RUN = "⚙️ Running {name}, the result follows shortly."
_ACTION_ACK_PARKED = "⏳ {name} needs your approval, see /approvals."

# The remember marker (offered by the periodic nudge in the think): the model
# flags ONE durable owner fact; we persist it (deduped) and strip it.
_REMEMBER_MARKER_RE = re.compile(
    r"\[\[\s*remember\s*:\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL
)
_REMEMBER_MAX = 300


async def apply_remember_marker(reply: str, *, instance_id: str) -> str:
    """Persist a ``[[remember: ...]]`` fact to the grande (deduped); strip the marker.

    Best-effort on every branch: a memory failure never breaks the chat turn.
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
                return visible  # already known: silently keep the clean reply
        await _facts.add_fact(
            instance_id,
            "dato",
            fact,
            tags=("owner", "chat"),
            procedencia="telegram-chat",
            confianza=0.7,
        )
        logger.info(f"chat: remembered owner fact ({len(fact)} chars)")
    except Exception as exc:
        logger.warning(f"chat: remember marker failed: {exc}")
    return visible


async def apply_research_marker(reply: str, *, instance_id: str, config) -> str:
    """Turn a ``[[research: ...]]`` marker into a REAL minted task; strip it from view.

    The first marker's query is capped and minted as a ``research_reply`` task
    through ``core.dispatch.mint_task`` with the same HUMANO/telegram origin
    ``/goal`` uses (allowlist and approval frontier apply unchanged). Every
    marker occurrence is removed from the visible reply; the follow-up promise
    (``_RESEARCH_ACK``) is appended ONLY after the mint actually succeeded, so
    the owner is never promised a message that cannot arrive. Never raises.
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
                origin=Origin(trust=TrustLevel.HUMANO, actor_id=CHAT_SOURCE),
                due_at=datetime.now(timezone.utc).isoformat(),
            )
            minted = bool(task_id)
            if task_id and estado == "pendiente":
                # No approval needed: run it NOW (fire-and-forget; the reply goes
                # out first, the results follow in seconds). A failure here leaves
                # the task pending, so the heartbeat still keeps the promise.
                asyncio.create_task(
                    _dispatch.run_task_now(
                        instance_id, task_id, "research_reply", params,
                        despertar_por=CHAT_SOURCE,
                    )
                )
        except Exception as exc:  # a mint failure must not break the chat turn
            logger.warning(f"chat: research mint failed: {exc}")

    if minted:
        return f"{visible}\n{_RESEARCH_ACK}".strip()
    if visible:
        return visible
    return (
        "I can't see the web from this chat turn, and the lookup path is "
        "unavailable right now, ask me again in a bit."
    )


def parse_action_params(raw: str) -> dict:
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


async def run_action_and_report(
    instance_id: str,
    task_id: str,
    name: str,
    params: dict,
    config,
    send: Optional[Sender] = None,
) -> None:
    """Run a chat-minted action NOW and message the outcome back. Never raises.

    Same early-runner semantics as the research bridge (``run_task_now``: a
    failure leaves the task pending so the heartbeat retries); the follow-up
    note carries the action's own episode ``resultado`` so the owner sees WHAT
    happened, not just that something did. The note is a WORK PRODUCT, so it
    travels through :func:`lhassa.org.deliver.deliver` (Lhassa as the sender
    of record, one logged hop), never a surface-direct send; a deliver failure
    degrades to the chat's own ``send`` so the owner is still told.
    """
    from lhassa.core import db as _db
    from lhassa.core import dispatch as _dispatch

    ok = await _dispatch.run_task_now(
        instance_id, task_id, name, params, despertar_por=CHAT_SOURCE
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
        logger.warning(f"chat: deliver seam failed ({exc}); plain send")
        sent = False
    if not sent:
        await say(resolve_sender(send, config), note)


async def apply_action_marker(
    reply: str, *, instance_id: str, config, send: Optional[Sender] = None
) -> str:
    """Turn an ``[[action: ...]]`` marker into a REAL minted task; strip it from view.

    Mirrors :func:`apply_research_marker`: the first marker wins, every
    occurrence is stripped, and the ack line is appended ONLY after the mint
    actually succeeded. The action must exist in the ACTIONS registry; the mint
    walks the same signed-task allowlist + approval frontier as ``/goal``
    (HUMANO/telegram origin): an unknown or disallowed action is refused with
    an honest line, never silently dropped. Never raises.
    """
    if not reply or "[[" not in reply:
        return reply
    match = _ACTION_MARKER_RE.search(reply)
    if match is None:
        return reply
    name = match.group(1).lower()
    params = parse_action_params(match.group(2))
    visible = _ACTION_MARKER_RE.sub("", reply).strip()

    ack = ""
    try:
        from datetime import datetime, timezone

        from lhassa import actionpack
        from lhassa.core import dispatch as _dispatch
        from lhassa.core.actions import ACTIONS
        from lhassa.security.trust import Origin, TrustLevel

        actionpack.register_all()  # idempotent: the door may precede a beat
        if name not in ACTIONS:
            ack = f"(I don't have an action named {name!r}, see /capabilities.)"
        else:
            task_id, estado = await _dispatch.mint_task(
                instance_id,
                name,
                params=params,
                origin=Origin(trust=TrustLevel.HUMANO, actor_id=CHAT_SOURCE),
                due_at=datetime.now(timezone.utc).isoformat(),
            )
            if task_id and estado == "pendiente":
                asyncio.create_task(
                    run_action_and_report(instance_id, task_id, name, params, config, send)
                )
                ack = _ACTION_ACK_RUN.format(name=name)
            elif task_id:
                ack = _ACTION_ACK_PARKED.format(name=name)
            else:
                ack = f"({name} was refused by the allowlist.)"
    except Exception as exc:  # a mint failure must not break the chat turn
        logger.warning(f"chat: action mint failed: {exc}")

    if ack:
        return f"{visible}\n{ack}".strip()
    if visible:
        return visible
    return "That action path is unavailable right now, ask me again in a bit."
