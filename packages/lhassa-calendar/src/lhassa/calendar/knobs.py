# SPDX-License-Identifier: AGPL-3.0-or-later
"""The calendar pack's own ``Config`` knobs (imported by the package itself).

Declared through :func:`lhassa.config.register_config_knob` so core's class
body knows nothing about calendars. Importing any ``lhassa.calendar`` module
declares them, so ``Config.calendar_*`` exists wherever the pack is used.
"""

from __future__ import annotations

from pathlib import Path

from lhassa.config import register_config_knob

# A published calendar's secret ICS URL; subscribing makes /today and the
# briefing show real events. Read-only; needs no app registration. SECRET.
register_config_knob("calendar_ics_url", "LHASSA_CALENDAR_ICS_URL", "")
# How long a fetched feed is cached before re-download (seconds; 1h default).
register_config_knob("calendar_feed_ttl", "LHASSA_CALENDAR_FEED_TTL", 3600.0, float)
# Multiple NAMED calendars: a JSON list of {"name", "url"} at this path (see
# store.py). LHASSA_CALENDAR_ICS_URL stays a shortcut merged in as "default".
register_config_knob(
    "calendars_file",
    "LHASSA_CALENDARS_FILE",
    str(Path.home() / ".lhassa" / "calendars.json"),
)
