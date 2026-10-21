# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chat door's in-memory conversation state (process memory only).

Two per-chat stores, both keyed by chat id so isolated test configs never
share state, and both cleared by a restart on purpose (the durable memory lives
in the grande, not here):

* the rolling **history** (``{role, content, ts}`` entries, TTL'd on read,
  trimmed to ``telegram_history_turns`` pairs) plus the per-chat user-turn
  counter the memory nudge rides on;
* the sticky **topic**: after a deterministic capability answers a message (a
  shortcut, an active route), the thread's topic is recorded here (capability
  name + a small carried-over context), so a connector-led elliptical
  follow-up ("Y el sabado?") can be re-routed to that capability's own
  :attr:`lhassa.notify.capabilities.Capability.continue_followup` instead of
  falling to a tool-less think. TTL'd on read exactly like the history.
"""

from __future__ import annotations

import time
from typing import Optional

from lhassa.notify import inbound_hooks

__all__ = [
    "reset_conversation_state",
    "history_key",
    "history_messages",
    "history_record",
    "topic_get",
    "topic_set",
]

_CHAT_HISTORY: dict = {}
_CHAT_TURNS: dict = {}

# Per-message cap when threading history back into the prompt (chars).
_HISTORY_MSG_CAP = 1500

_CHAT_TOPIC: dict = {}
_CHAT_TOPIC_TTL_DEFAULT = 900.0  # 15 min


def reset_conversation_state() -> None:
    """Forget every chat's in-memory conversation state, hooks included.

    The door is meant to live in one long-running process, so these dicts are
    never reset in production, and neither is the per-chat state an inbound
    hook keeps (the cv pack's held CV and armed base-CV request). A test suite
    reusing small chat ids across tests is not one long-lived process: without
    this, a later test can silently inherit an earlier test's state.
    """
    _CHAT_HISTORY.clear()
    _CHAT_TURNS.clear()
    _CHAT_TOPIC.clear()
    inbound_hooks.reset_all()


def history_key(config) -> str:
    return str(getattr(config, "telegram_chat_id", "") or "owner")


def _topic_key(config, chat: str = "") -> str:
    return str(chat) if chat else history_key(config)


def topic_set(config, chat: str, capability: str, context: Optional[dict] = None) -> None:
    """Record ``capability`` as the chat's current sticky topic. Never raises."""
    try:
        _CHAT_TOPIC[_topic_key(config, chat)] = {
            "capability": capability,
            "context": dict(context or {}),
            "ts": time.time(),
        }
    except Exception:
        pass


def topic_get(config, chat: str = "") -> Optional[dict]:
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


def history_messages(config) -> list[dict]:
    """The still-fresh history as ``[{role, content}, ...]``, oldest first.

    TTL is applied on read (an old conversation never leaks into a new one) and
    each message is capped so a pasted wall of text can't flood the prompt.
    """
    ttl = float(getattr(config, "telegram_history_ttl", 7200.0) or 7200.0)
    now = time.time()
    entries = [
        e for e in _CHAT_HISTORY.get(history_key(config), []) if now - e["ts"] <= ttl
    ]
    return [
        {"role": e["role"], "content": str(e["content"])[:_HISTORY_MSG_CAP]}
        for e in entries
    ]


def history_record(config, role: str, content: str) -> None:
    """Append one turn, trimming to the configured number of user+assistant pairs."""
    if not content:
        return
    turns = int(getattr(config, "telegram_history_turns", 8) or 8)
    key = history_key(config)
    entries = _CHAT_HISTORY.setdefault(key, [])
    entries.append({"role": role, "content": content, "ts": time.time()})
    max_entries = max(2, turns * 2)
    if len(entries) > max_entries:
        del entries[: len(entries) - max_entries]


def bump_turns(config) -> int:
    """Count one more USER turn for this chat; return the new total."""
    key = history_key(config)
    _CHAT_TURNS[key] = _CHAT_TURNS.get(key, 0) + 1
    return _CHAT_TURNS[key]
