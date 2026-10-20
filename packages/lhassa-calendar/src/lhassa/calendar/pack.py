# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for the calendar (``lhassa.plugins`` -> ``calendar``).

Importing this module registers the pack and everything it plugs in: the
``calendar_sync`` and ``derive_world_facts`` actions, the ``/calendar``
``/calendars`` ``/event`` chat commands, the ``calendar_view`` capability, the
``/remind`` .ics extra, the personal agenda's event source, the
``/api/calendar`` router and ``lhassa calendar``. Calendar config knobs
(``calendar_ics_url``, ``calendars_file``) and the action allowlist entries
stay in core; the pack owns no tables.
"""

from __future__ import annotations

from lhassa.extensions import register_api_router, register_cli_app, register_pack

register_pack(
    "calendar",
    "Calendar: ICS feeds, master-calendar reconcile, .ics events, $0 calendar queries",
    module=__name__,
)


def _api_router():
    from lhassa.calendar.surfaces.api import router

    return router


def _cli_app():
    from lhassa.calendar.surfaces.cli import calendar_app

    return calendar_app


register_api_router("calendar", _api_router)
register_cli_app("calendar", _cli_app)

from lhassa.calendar.surfaces.wiring import register as _register_surfaces  # noqa: E402

_register_surfaces()
