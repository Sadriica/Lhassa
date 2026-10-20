# SPDX-License-Identifier: AGPL-3.0-or-later
"""Calendar-event file-I/O seam; a downloadable ``.ics`` in, a tappable file out.

Ships as the ``lhassa-calendar`` capability pack (``packages/lhassa-calendar``);
core never imports it. :mod:`lhassa.calendar.pack` wires it into core's
registries, and :mod:`lhassa.calendar.surfaces` holds its shell slice (API
router, ``lhassa calendar`` CLI, chat commands, actions).

One tap on the ``.ics`` this package writes adds an event to ANY calendar
(institutional Outlook, Google, iPhone) with no API, no admin, no permissions.
This package is the small, self-contained module that owns those files, same
pattern as :mod:`lhassa.cv`:

  * :mod:`lhassa.calendar.paths`: ``calendar_dir`` / ``events_dir`` (the one
    knob, ``LHASSA_CALENDAR_DIR``, default ``~/.lhassa/calendar``).
  * :mod:`lhassa.calendar.ics`  : ``build_event`` / ``write_ics``.

Layer: leaf infrastructure; outside ``surfaces/`` it never imports an outer
shell (see ``tests/test_architecture.py``'s ``calendar-infra-isolation`` rule).
The pack's own surfaces deliver the resulting files via
:func:`lhassa.org.deliver.deliver`: the leaf modules never send anything.
"""

from lhassa.calendar.ics import build_event, write_ics
from lhassa.calendar.paths import calendar_dir, events_dir

__all__ = [
    "build_event",
    "calendar_dir",
    "events_dir",
    "write_ics",
]
