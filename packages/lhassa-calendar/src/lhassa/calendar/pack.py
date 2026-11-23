# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for the calendar (``lhassa.plugins`` -> ``calendar``).

Importing this module registers the pack and everything it plugs in: the
``calendar_sync`` and ``derive_world_facts`` actions, the ``/calendar``
``/calendars`` ``/event`` chat commands, the ``calendar_view`` capability, the
``/remind`` .ics extra, the personal agenda's event source, the
``/api/calendar`` router and ``lhassa calendar``, its allowlist rows, and
the two calendar knobs in the settings panel (the ``Config`` knobs themselves
are declared in :mod:`lhassa.calendar.knobs`). The pack owns no tables.
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


def _register_settings() -> None:
    from lhassa.settings import RESTART_TELEGRAM, register_editable

    for entry in (
        {
            "key": "calendar_ics_url",
            "attr": "calendar_ics_url",
            "env_var": "LHASSA_CALENDAR_ICS_URL",
            "type": "str",
            "secret": True,
            "label": "Calendar ICS feed URL (read-only)",
            "help": (
                "A published calendar's secret .ics URL. Subscribing makes /today and "
                "the briefing show your real events; read-only, no admin/API. Get it "
                "from your calendar (Outlook: Settings → Calendar → Shared calendars → "
                "Publish → ICS link). Kept secret (it grants read access)."
            ),
            "group": "Surfaces",
            "default": "",
            "restart": RESTART_TELEGRAM,
            "pack": "calendar",
        },
        {
            "key": "calendar_feed_ttl",
            "attr": "calendar_feed_ttl",
            "env_var": "LHASSA_CALENDAR_FEED_TTL",
            "type": "float",
            "label": "Calendar feed cache (seconds)",
            "help": "How long a fetched calendar feed is cached before re-download (default 3600).",
            "group": "Surfaces",
            "default": 3600.0,
            "restart": RESTART_TELEGRAM,
            "pack": "calendar",
        },
    ):
        register_editable(entry)


_register_settings()

from lhassa.calendar.surfaces.wiring import register as _register_surfaces  # noqa: E402

_register_surfaces()
