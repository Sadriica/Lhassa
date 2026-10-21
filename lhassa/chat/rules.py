# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watch rules: message patterns that mint a task instead of a think.

The on-disk shape is ``[{"pattern": "<regex>", "accion": "<str>", "params": {}}]``
at ``config.watch_rules_file``. The chat engine matches every plain message
against the compiled rules before it falls back to a think; the ``/watch``,
``/watches`` and ``/unwatch`` chat commands (:mod:`lhassa.notify.commands`)
read and write the same file through :func:`load_raw_watch_rules` /
:func:`save_watch_rules`, so there is exactly one format.
"""

from __future__ import annotations

import json as _json
import re
from typing import Optional

from loguru import logger

def load_raw_watch_rules(config) -> list[dict]:
    """Load the RAW watch-rules JSON (pattern as a string, not yet compiled).

    This is the single source of truth for the on-disk shape
    (``[{"pattern": "<regex>", "accion": "<str>", "params": {}}]``); both the
    read-only matching path below (:func:`load_watch_rules`) and the
    ``/watch`` / ``/watches`` / ``/unwatch`` chat commands (:mod:`lhassa.notify.
    commands`) read and write through this pair so there is exactly one format.
    Never raises: a missing/malformed file degrades to ``[]``.
    """
    path_raw = getattr(config, "watch_rules_file", "") or ""
    if not path_raw:
        return []
    import pathlib as _pathlib

    try:
        text = _pathlib.Path(path_raw).read_text(encoding="utf-8")
        raw = _json.loads(text)
    except (OSError, _json.JSONDecodeError, Exception):
        return []

    if not isinstance(raw, list):
        return []

    rules: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pattern_src = str(entry.get("pattern") or "").strip()
        accion = str(entry.get("accion") or "").strip()
        if not pattern_src or not accion:
            continue
        params = entry.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        rules.append({"pattern": pattern_src, "accion": accion, "params": dict(params)})
    return rules


def save_watch_rules(config, rules: list[dict]) -> bool:
    """Persist RAW watch rules (the :func:`load_raw_watch_rules` shape) to disk.

    Creates parent directories as needed. Returns whether the write succeeded;
    never raises (a write failure is logged and swallowed; the caller reports it
    to the chat as a friendly failure, not a crash).
    """
    path_raw = getattr(config, "watch_rules_file", "") or ""
    if not path_raw:
        return False
    import pathlib as _pathlib

    try:
        path = _pathlib.Path(path_raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(rules, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning(f"chat: could not save watch rules: {exc}")
        return False


def load_watch_rules(config) -> list[dict]:
    """Load watch rules with COMPILED patterns, ready for matching. Never raises.

    Returns a list of validated rule dicts: {pattern: re.Pattern, accion: str,
    params: dict}. Missing/malformed file → empty list (feature silently off).
    Invalid regex entries are skipped (an entry written by ``/watch`` was already
    validated at write time, but a hand-edited file might not be).
    """
    rules: list[dict] = []
    for entry in load_raw_watch_rules(config):
        try:
            pattern = re.compile(entry["pattern"], re.IGNORECASE)
        except re.error:
            logger.debug(
                f"chat: skipping invalid rule regex {entry['pattern']!r}"
            )
            continue
        rules.append(
            {"pattern": pattern, "accion": entry["accion"], "params": dict(entry["params"])}
        )
    return rules


async def try_mint_rule_task(text: str, rules: list[dict], *, config) -> Optional[str]:
    """Try matching ``text`` against watch rules; mint the first matching task.

    Returns a short confirmation string (task id) if a rule matched and the task
    was minted, else ``None``. Never raises.
    """
    if not rules:
        return None
    try:
        from lhassa.core import db as _db
        from lhassa.core import dispatch as _dispatch
        from lhassa.security.trust import Origin, TrustLevel

        instance_id = getattr(config, "instance_id", None) or "inst_personal"
        await _db.init_db()

        for rule in rules:
            if rule["pattern"].search(text):
                origin = Origin(trust=TrustLevel.HUMANO, actor_id="telegram-rule")
                task_id, estado = await _dispatch.mint_task(
                    instance_id,
                    rule["accion"],
                    params=dict(rule["params"]),
                    origin=origin,
                    judgment=True,  # rule-minted tasks are judgment (any accion)
                )
                if task_id:
                    return task_id
                break  # rule matched but mint refused: don't try fallback rules
    except Exception as exc:
        logger.debug(f"chat: rule task mint failed: {exc}")
    return None

