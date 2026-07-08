"""Path resolution for the calendar seam — the single place ``.ics`` files live.

Mirrors :mod:`lhassa.cv.paths`: one directory (``LHASSA_CALENDAR_DIR``, default
``~/.lhassa/calendar``), re-read at CALL time so a test or a freshly-set env var
is picked up without a reimport.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_DEFAULT_CALENDAR_DIR = str(Path.home() / ".lhassa" / "calendar")


def calendar_dir() -> Path:
    """The calendar base directory, ``~/.lhassa/calendar`` by default
    (``LHASSA_CALENDAR_DIR``)."""
    raw = (os.getenv("LHASSA_CALENDAR_DIR", "") or "").strip() or _DEFAULT_CALENDAR_DIR
    return Path(raw).expanduser()


def events_dir() -> Path:
    """Where generated ``.ics`` files land. Created on demand."""
    d = calendar_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def feed_cache_path(url: str) -> Path:
    """The PER-FEED cache path for a subscribed .ics ``url``.

    Multiple named calendars share ``calendar_dir()`` but must never clobber
    each other's cache, so the filename is keyed by a short hash of the url
    (never the url itself — it's a secret) rather than a single shared name.
    """
    digest = hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()[:16]
    return calendar_dir() / f"feed_cache_{digest}.ics"
