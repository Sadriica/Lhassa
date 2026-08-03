# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure-stdlib ``.ics`` generation — the calendar file-I/O seam.

One tap on the file this module writes adds an event to ANY calendar
(institutional Outlook, Google, iPhone) with no API, no admin, no permissions —
a ``.ics`` is just a file, not an integration. This module is the leaf-infra
counterpart of :mod:`lhassa.cv.store`: it owns the RFC5545 rendering and the
on-disk write so paths and format details never scatter across the callers.

  * :func:`build_event` — a valid ``VCALENDAR``/``VEVENT`` string for a single
    event. Deterministic: the UID is derived from a seed (or ``summary`` +
    ``start_dt``) rather than a random/``now`` call, so the same inputs always
    produce the same file — no ``uuid4``/``datetime.now`` inside this module.
  * :func:`write_ics` — write the content to ``~/.lhassa/calendar/`` (see
    :mod:`lhassa.calendar.paths`) atomically, filename from a slug of the
    summary, returns the ``.ics`` path.

Layer: leaf infrastructure — depends on nothing but the standard library and
:mod:`lhassa.calendar.paths`; never imports a shell (``notify``/``org``/``api``/
...). See ``tests/test_architecture.py``'s ``calendar-infra-isolation`` rule.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from lhassa.calendar.paths import events_dir

_PRODID = "-//Lhassa//Calendar Events//EN"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# RFC5545 content lines must not exceed 75 octets; continuation lines start
# with a single leading space. Folding here is byte-safe (never splits a
# multi-byte UTF-8 codepoint).
_FOLD_LIMIT = 75


def _fold(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= _FOLD_LIMIT:
        return line
    parts: list[str] = []
    start = 0
    limit = _FOLD_LIMIT
    while start < len(encoded):
        chunk = encoded[start : start + limit]
        while chunk:
            try:
                text = chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        else:
            text = ""
        if not chunk:  # pragma: no cover - defensive, should not happen
            break
        parts.append(text)
        start += len(chunk)
        limit = _FOLD_LIMIT - 1  # continuation lines lose one octet to the leading space
    return "\r\n ".join(parts)


def _escape(text: str) -> str:
    """Escape commas/semicolons/backslashes/newlines per RFC5545 §3.3.11."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return text


def _fmt_utc(dt: datetime) -> str:
    """Render an (aware or naive) datetime as an RFC5545 UTC timestamp (``...Z``).

    A naive datetime is treated as already-UTC (never a local-clock guess); an
    aware one is converted. No ``datetime.now()`` call — always derived from
    the value the caller passed in, so the output is deterministic.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _stable_uid(summary: str, start_dt: datetime, uid: Optional[str]) -> str:
    """A stable UID: the given ``uid`` verbatim, else derived from
    ``summary`` + ``start_dt`` (deterministic — same inputs, same UID)."""
    if uid:
        return str(uid).strip() or "event@lhassa"
    seed = f"{summary}|{start_dt.isoformat()}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"{digest}@lhassa"


def build_event(
    summary: str,
    start_dt: datetime,
    *,
    end_dt: Optional[datetime] = None,
    description: str = "",
    location: str = "",
    uid: Optional[str] = None,
) -> str:
    """A valid ``VCALENDAR``/``VEVENT`` string for one event. Never raises.

    ``end_dt`` defaults to ``start_dt + 1h``. Times are emitted as UTC
    (``...Z``) from whatever ``start_dt``/``end_dt`` were passed in — this
    function makes no clock calls of its own, so it is fully deterministic
    given its inputs (a requirement of the ``do-and-sleep`` spine's $0,
    reproducible-artifact discipline). On any failure it logs and returns
    ``""`` — a caller degrades (no delivery) rather than crashing.
    """
    try:
        clean_summary = str(summary or "").strip() or "Reminder"
        end = end_dt if end_dt is not None else (start_dt + timedelta(hours=1))
        event_uid = _stable_uid(clean_summary, start_dt, uid)

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            f"PRODID:{_PRODID}",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            f"UID:{event_uid}",
            f"DTSTAMP:{_fmt_utc(start_dt)}",
            f"DTSTART:{_fmt_utc(start_dt)}",
            f"DTEND:{_fmt_utc(end)}",
            f"SUMMARY:{_escape(clean_summary)}",
        ]
        if description:
            lines.append(f"DESCRIPTION:{_escape(description)}")
        if location:
            lines.append(f"LOCATION:{_escape(location)}")
        lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")

        return "\r\n".join(_fold(line) for line in lines) + "\r\n"
    except Exception as exc:  # never raise — a bad input degrades to "" upstream
        logger.warning(f"calendar.ics: build_event failed: {exc}")
        return ""


def _slug(name: Optional[str]) -> str:
    """Slug a summary for a filename — same rule as :func:`lhassa.cv.store.slug_company`."""
    if not name:
        return "event"
    folded = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM.sub("-", folded.lower()).strip("-")
    return slug or "event"


def _short_id(seed: Optional[str] = None) -> str:
    """A short (~6-char) id — deterministic when ``seed`` is given, else derived
    from the content itself by the caller (see :func:`write_ics`)."""
    return hashlib.sha1(str(seed).encode("utf-8")).hexdigest()[:6]


def write_ics(content: str, *, name: str, run_id: Optional[str] = None) -> Path:
    """Write ``content`` to ``<calendar_dir>/{id}_{slug}.ics``. Returns the path.

    The write is ATOMIC (temp file in the same directory + ``os.replace``), so
    an interrupted write never leaves a half-written ``.ics``. The id is
    derived from ``run_id`` when given (stable per run); otherwise from the
    content itself (still deterministic — no random/``now`` call), so two
    calls with identical content land on the same filename.
    """
    slug = _slug(name)
    ident = _short_id(run_id if run_id else content)
    dest = events_dir() / f"{ident}_{slug}.ics"
    payload = content if isinstance(content, str) else str(content)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".ics-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return dest
