# SPDX-License-Identifier: AGPL-3.0-or-later
"""Calendar-event file-I/O seam; a downloadable ``.ics`` in, a tappable file out.

One tap on the ``.ics`` this package writes adds an event to ANY calendar
(institutional Outlook, Google, iPhone) with no API, no admin, no permissions.
This package is the small, self-contained module that owns those files, same
pattern as :mod:`lhassa.cv`:

  * :mod:`lhassa.calendar.paths`: ``calendar_dir`` / ``events_dir`` (the one
    knob, ``LHASSA_CALENDAR_DIR``, default ``~/.lhassa/calendar``).
  * :mod:`lhassa.calendar.ics`  : ``build_event`` / ``write_ics``.

Layer: leaf infrastructure; depends on nothing but the standard library and
never imports an outer shell (see ``tests/test_architecture.py``'s
``calendar-infra-isolation`` rule). A shell (``lhassa.notify.commands``) calls
this package and then delivers the resulting file via
:func:`lhassa.org.deliver.deliver`: this package itself never sends anything.
"""

from lhassa.calendar.ics import build_event, write_ics
from lhassa.calendar.paths import calendar_dir, events_dir

__all__ = [
    "build_event",
    "calendar_dir",
    "events_dir",
    "write_ics",
]
