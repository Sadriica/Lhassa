# SPDX-License-Identifier: AGPL-3.0-or-later
"""Slash commands for the inbound Telegram chat; a modular command registry.

The owner's standing principle is that Lhassa must be **fully elastic and highly
modular**: every swap point sits behind an interface + a registry, and new
capabilities are **drop-in** (add a module, never edit core). Chat commands are
one such swap point: today ``/start`` + ``/help`` + ``/status``, tomorrow whatever
a plugin registers; each is just another entry in :data:`COMMANDS`.

This package (formerly one ~2200-line module) is split by domain, one submodule
per group of related commands; importing each submodule is what runs its
``@register_command`` decorators, exactly as importing the old flat module did:

* ``_registry``: :data:`COMMANDS`, :func:`register_command`, :func:`parse`,
  :func:`dispatch`: the registry + dispatch mechanism itself, no handlers;
* ``_shared``: small LLM-free helpers used by several command modules
  (``_oops``, ``_config``, ``_routine_provider_model``, ``_health_line``,
  ``_today_spend``, ``_approval_age``);
* ``_core``: ``/start`` ``/help`` ``/status`` ``/version`` ``/config`` ``/report``;
* ``_cv``: ``/cvnewbase`` ``/cvbase``;
* ``_code``: ``/code`` (an agentic coding run, gated behind approval);
* ``_approvals``: ``/approvals`` ``/approve`` ``/deny``;
* ``_watch``: ``/watch`` ``/watches`` ``/unwatch`` ``/goal``;
* ``_org``: ``/org`` ``/fleet`` ``/temple``;
* ``_capabilities``: ``/capabilities`` ``/tools`` ``/skills``;
* ``_memory``: ``/recall`` ``/memory`` ``/knowledge`` ``/checkin`` ``/today``
  ``/tomorrow``;
* ``_ai``: ``/ai`` ``/moa`` (the paid direct doors);
* ``_remind``: :func:`parse_remind`, ``/remind`` ``/event``;
* ``_calendar``: ``/calendar`` ``/calendars``;
* ``_idea``: ``/idea``.

This module defines (via re-export from the submodules above):

* :data:`COMMANDS`: the module-level :class:`~lhassa.registry.Registry` of command
  handlers, plus :func:`register_command`, a thin decorator/direct-call wrapper so a
  plugin can drop in a new command **without editing core**;
* :func:`parse`: split a raw message into ``(command | None, args)``. A message is
  a command iff it starts with ``/``; the leading ``/`` is stripped, the first token
  is the command name (Telegram's ``/cmd@BotName`` form is normalised to ``cmd``),
  and the remainder is ``args``. A non-slash message yields ``(None, text)``;
* :func:`dispatch`: run a known command and return its reply text; return a short
  "unknown command" hint for an unrecognised ``/command``; return ``None`` for a
  plain-language message so the caller can let the **brain** answer it.

A command handler is an ``async (args: str, *, config=None) -> str`` callable. Like
the rest of :mod:`lhassa.notify`, every handler is **fully defensive**: it wraps
its work and returns a friendly fallback rather than raising, so a command can never
crash the inbound poll loop. Nothing here calls an LLM: commands are answered
locally and cheaply.
"""

from __future__ import annotations

__all__ = [
    "COMMANDS",
    "register_command",
    "parse",
    "dispatch",
]

# The registry + dispatch mechanism.
from ._registry import COMMANDS, dispatch, parse, register_command  # noqa: E402,F401

# Shared helpers some outer modules reach into directly (e.g.
# lhassa.notify.digest imports _today_spend, lhassa.cli.capabilities imports
# _routine_provider_model).
from ._shared import (  # noqa: E402,F401
    _approval_age,
    _config,
    _health_line,
    _oops,
    _routine_provider_model,
    _today_spend,
)

# Import every command submodule so its @register_command decorators run;
# this is what actually populates COMMANDS, exactly as importing the old flat
# module did. Order doesn't matter (Registry rejects duplicate names).
from . import (  # noqa: E402,F401
    _ai,
    _approvals,
    _calendar,
    _capabilities,
    _code,
    _core,
    _cv,
    _idea,
    _memory,
    _org,
    _remind,
    _watch,
)

# Re-export the public/quasi-public API a few outer modules and tests reach
# into by name (kept identical to what the flat module exposed as attributes).
from ._ai import _moa_footer, _moa_label, _resolve_chat_provider  # noqa: E402,F401
from ._calendar import _calendar_sync_reply, _mask_url  # noqa: E402,F401
from ._capabilities import _flag  # noqa: E402,F401
from ._code import _CODE_APPROVAL_MARKER, _code_cwd  # noqa: E402,F401
from ._core import DESCRIPTIONS, _ledger_window_cost, menu_entries  # noqa: E402,F401
from ._memory import _render_personal_agenda  # noqa: E402,F401
from ._org import _cadence_label, _match_temple_action, _short_local_due, _temple_schedule  # noqa: E402,F401
from ._remind import _deliver_calendar_event, parse_remind  # noqa: E402,F401
from ._watch import _known_actions  # noqa: E402,F401
