# SPDX-License-Identifier: AGPL-3.0-or-later
"""A crash-safe JSON store for NAMED calendar feeds — ``~/.lhassa/calendars.json``.

An owner can have several calendars (institutional, personal, a project one) —
each subscribed with a *name* so ``/today`` can show which calendar an event
came from (``📅 15:00 Algorithms 201 · Work``). This module is the small,
self-contained store for that list, mirroring :mod:`lhassa.flows.store` /
:mod:`lhassa.org.topology`'s "JSON file under ``~/.lhassa``" pattern: a list of
``{"name": str, "url": str}`` dicts at ``config.calendars_file``
(``LHASSA_CALENDARS_FILE``), written ATOMICALLY (temp file + ``os.replace``) so
an interrupted write can never corrupt the file.

The pre-existing single-feed shortcut (``config.calendar_ics_url`` /
``LHASSA_CALENDAR_ICS_URL``) keeps working unchanged: :func:`all_feeds` merges it
in as an implicit feed named ``"default"`` whenever it is set and its URL isn't
already present under a stored name — so someone using only the env var sees no
difference, and someone who has ALSO added a store entry for that same URL never
sees it duplicated.

Leaf infrastructure: depends on nothing but the standard library (+ a lazy,
local import of :mod:`lhassa.config`, itself leaf) — never imports a shell (see
``tests/test_architecture.py``'s ``calendar-infra-isolation`` rule). Every
function here is defensive: a missing/corrupt file degrades to ``[]`` rather
than raising.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from loguru import logger

_DEFAULT_NAME = "default"


def _resolve_config(config=None):
    if config is not None:
        return config
    from lhassa.config import Config

    return Config()


def _calendars_path(config=None) -> Path:
    cfg = _resolve_config(config)
    raw = getattr(cfg, "calendars_file", "") or str(Path.home() / ".lhassa" / "calendars.json")
    return Path(raw).expanduser()


def _read_list(config=None) -> list[dict]:
    """The raw list of ``{"name", "url"}`` dicts; ``[]`` for a missing/corrupt file."""
    path = _calendars_path(config)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("calendar: could not read {} ({}); treating as empty", path, exc)
        return []
    if not isinstance(raw, list):
        logger.warning("calendar: {} is not a JSON list; treating as empty", path)
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name") and item.get("url"):
            entry = {"name": str(item["name"]), "url": str(item["url"])}
            if item.get("primary"):
                entry["primary"] = True
            out.append(entry)
    return out


def _write_list(items: list[dict], config=None) -> bool:
    """Atomically replace the calendars file with ``items``. Never raises."""
    path = _calendars_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(items, ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".calendars-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        return True
    except OSError as exc:
        logger.warning("calendar: could not save {} ({})", path, exc)
        return False


def load_calendars(config=None) -> list[dict]:
    """Every stored named calendar (``[]`` if the store is absent/corrupt)."""
    return _read_list(config)


def list_calendars(config=None) -> list[dict]:
    """Alias for :func:`load_calendars` — reads better at a call site that lists."""
    return _read_list(config)


def add_calendar(name: str, url: str, config=None) -> bool:
    """Add (or replace, by name) a named calendar feed. ``False`` on any trouble.

    Validates ``name`` is non-empty and ``url`` starts with ``http`` (the only
    sanity check this leaf layer owns — no network is touched). Dedupes by
    ``name``: adding an existing name REPLACES its url rather than duplicating.
    """
    name = (name or "").strip()
    url = (url or "").strip()
    if not name or not url:
        return False
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    try:
        items = _read_list(config)
        was_primary = any(it["name"] == name and it.get("primary") for it in items)
        items = [it for it in items if it["name"] != name]
        new_entry = {"name": name, "url": url}
        if was_primary:
            new_entry["primary"] = True
        items.append(new_entry)
        return _write_list(items, config)
    except Exception as exc:  # pragma: no cover — defensive, mirrors flows.store
        logger.warning("calendar: add_calendar({!r}) failed: {}", name, exc)
        return False


def remove_calendar(name: str, config=None) -> bool:
    """Remove the named calendar; ``True`` iff it existed and was removed."""
    name = (name or "").strip()
    if not name:
        return False
    try:
        items = _read_list(config)
        remaining = [it for it in items if it["name"] != name]
        if len(remaining) == len(items):
            return False
        return _write_list(remaining, config)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("calendar: remove_calendar({!r}) failed: {}", name, exc)
        return False


def all_feeds(config=None) -> list[dict]:
    """Stored calendars PLUS the ``LHASSA_CALENDAR_ICS_URL`` shortcut, merged.

    The env-var shortcut is added as ``{"name": "default", "url": ...}`` only
    when it is set AND its url isn't already present under some stored name
    (so subscribing the same feed twice never shows it twice). Never raises.
    """
    try:
        items = list(_read_list(config))
    except Exception:  # pragma: no cover — defensive
        items = []

    try:
        cfg = _resolve_config(config)
        shortcut = (getattr(cfg, "calendar_ics_url", "") or "").strip()
    except Exception:
        shortcut = ""

    if shortcut and not any(it.get("url") == shortcut for it in items):
        name = _DEFAULT_NAME
        # Avoid clashing with a stored calendar that happens to be named "default".
        existing_names = {it["name"] for it in items}
        suffix = 2
        while name in existing_names:
            name = f"{_DEFAULT_NAME}{suffix}"
            suffix += 1
        items.append({"name": name, "url": shortcut})

    return items


def set_primary(name: str, config=None) -> bool:
    """Mark the named calendar PRIMARY (the master); clears it on every other
    stored calendar so exactly one ``primary: true`` survives. ``False`` if
    ``name`` isn't a stored calendar, or on any I/O trouble. Never raises.

    Only applies to STORED entries — the ``LHASSA_CALENDAR_ICS_URL`` shortcut
    (merged in by :func:`all_feeds` under the implicit ``"default"`` name) is
    never itself a candidate; an owner who wants that feed to be primary must
    ``/calendar add`` it under a real name first.
    """
    name = (name or "").strip()
    if not name:
        return False
    try:
        items = _read_list(config)
        if not any(it["name"] == name for it in items):
            return False
        out: list[dict] = []
        for it in items:
            entry = {"name": it["name"], "url": it["url"]}
            if it["name"] == name:
                entry["primary"] = True
            out.append(entry)
        return _write_list(out, config)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("calendar: set_primary({!r}) failed: {}", name, exc)
        return False


def get_primary(config=None) -> "str | None":
    """The primary calendar's name, or ``None`` if none is set. Never raises."""
    try:
        for it in _read_list(config):
            if it.get("primary"):
                return it["name"]
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("calendar: get_primary failed: {}", exc)
    return None


__all__ = [
    "add_calendar",
    "all_feeds",
    "get_primary",
    "list_calendars",
    "load_calendars",
    "remove_calendar",
    "set_primary",
]
