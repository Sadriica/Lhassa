# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plug the calendar pack into core's registries. Idempotent.

Called once by :mod:`lhassa.calendar.pack`. Lives under ``surfaces/`` because
it composes shells (the notify command registry, the capability catalog, the
reminder-extras seam); the API router and CLI sub-app stay lazy factories in
``pack.py``.
"""

from __future__ import annotations

_DONE = False


def register() -> None:
    """Register actions, preconditions, chat commands, the ``calendar_view``
    capability, the ``/remind`` .ics extra and the agenda event source."""
    global _DONE
    if _DONE:
        return
    _DONE = True

    from lhassa.calendar import world_facts
    from lhassa.calendar.agenda_source import agenda_events
    from lhassa.calendar.surfaces import actions, capability
    from lhassa.calendar.surfaces import commands as _commands  # noqa: F401 (registers /calendar /calendars /event)
    from lhassa.core.actions import _REGISTRY, register_action, register_precondition
    from lhassa.notify.commands import register_reminder_extra
    from lhassa.personal.sources import register_event_source

    def _reg(name: str, fn) -> None:  # noqa: ANN001
        if _REGISTRY.get_or_none(name) is None:
            register_action(name, fn)

    # Master calendar reconcile ($0 set diff, no model).
    _reg("calendar_sync", actions.calendar_sync)
    register_precondition("calendar_sync", actions.calendar_sync_possible)
    # Calendar-derived world model ($0, deterministic; courses/commitments/
    # rhythm bootstrapped from the calendar feed into durable facts).
    _reg("derive_world_facts", world_facts.derive_world_facts)

    # Policy rows (deny-by-default holds without them). calendar_sync is a
    # deterministic $0 diff that delivers an .ics per unsynced event and never
    # writes to any calendar; derive_world_facts reads the calendar and writes
    # facts. Neither sends anything external, so no approval hop.
    from lhassa.security.allowlist import WILDCARD, ActionSpec, register_action_spec
    from lhassa.security.trust import TrustLevel

    for name in ("calendar_sync", "derive_world_facts"):
        register_action_spec(
            name,
            ActionSpec(objetivos=WILDCARD, min_trust=TrustLevel.INTERNO, requires_approval=False),
        )

    capability.register()
    register_reminder_extra("calendar_event", _commands.deliver_calendar_event)
    register_event_source("calendar", agenda_events)
