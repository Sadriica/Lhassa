# SPDX-License-Identifier: AGPL-3.0-or-later
"""Memory actions: add_fact, backup_memory, consolidate_memory."""

from __future__ import annotations

from lhassa.config import Config
from lhassa.core.dispatch import clean_tags
from lhassa.memory import backup as _backup
from lhassa.memory import chica as _chica
from lhassa.memory import facts as _facts


async def add_fact(params: dict) -> str:
    """Append a dato fact to the grande.

    tipo is FORCED to "dato" (a model/action may never write a directiva).
    confianza is fixed at 0.8. procedencia is "action:add_fact". Tags are
    validated via dispatch.clean_tags (public since Phase 3).

    params:
        contenido (required)  — the fact text.
        tags                  — list of tag strings (optional).
        instance_id           — defaults to Config.instance_id.

    Never raises.
    """
    try:
        contenido = str(params.get("contenido") or "").strip()
        if not contenido:
            return "add_fact: 'contenido' param is required"

        tags = clean_tags(params.get("tags"))
        instance_id = params.get("instance_id") or Config.instance_id

        fact = await _facts.add_fact(
            instance_id,
            "dato",
            contenido,
            pinned=False,
            tags=tags,
            procedencia="action:add_fact",
            confianza=0.8,
        )
        return f"add_fact: stored {fact.id} ({len(contenido)} chars)"

    except Exception as exc:
        return f"add_fact: error: {exc}"


async def backup_memory(params: dict) -> str:
    """Back up the grande (brain.db) and verify the backup.

    Uses ``memory.backup.backup()`` (WAL-safe online copy) then
    ``memory.backup.verify(path)`` (integrity_check + table presence). Never
    raises — a failed backup reports an error string.

    params: (none required; dest overrides the default backup path)

    Returns the backup path and verification status.
    """
    try:
        dest = params.get("dest") or None
        path = await _backup.backup(dest=dest)
        ok = await _backup.verify(path)
        verified = "verified ok" if ok else "VERIFICATION FAILED"
        return f"backup_memory: {path} — {verified}"

    except Exception as exc:
        return f"backup_memory: error: {exc}"


async def consolidate_memory(params: dict) -> str:
    """Run LLM-free chica consolidation (mechanical LRU eviction only).

    Calls ``memory.chica.consolidate`` — the purely mechanical, $0, no-LLM
    path. DOES NOT call the semantic/LLM variant (memory.consolidate). Pinned
    items and directivas are never evicted.

    params:
        instance_id  — defaults to Config.instance_id.
        max_items    — integer override (default is Config.chica_max_items).

    Never raises.
    """
    try:
        instance_id = params.get("instance_id") or Config.instance_id
        max_items_raw = params.get("max_items")
        max_items: int | None = None
        if max_items_raw is not None:
            try:
                max_items = int(max_items_raw)
            except (TypeError, ValueError):
                return "consolidate_memory: 'max_items' must be an integer"

        result = await _chica.consolidate(instance_id, max_items=max_items)
        evicted = result.get("evicted", 0)
        kept = result.get("kept", 0)
        return f"consolidate_memory: kept={kept} evicted={evicted}"

    except Exception as exc:
        return f"consolidate_memory: error: {exc}"


async def distill_memory(params: dict) -> str:
    """Extract durable facts from recent episodes via the cheap model.

    This is the ONE action in the memory actionpack that may spend tokens.
    Calls ``memory.consolidate.distill_episodes`` to distill the last
    ``since_hours`` of episodes into at most 10 novel dato facts.

    params:
        instance_id  — defaults to Config.instance_id.
        since_hours  — hours of episode history to consider (default 24).

    Never raises.
    """
    try:
        from lhassa.memory import consolidate as _consolidate

        instance_id = params.get("instance_id") or Config.instance_id
        since_hours_raw = params.get("since_hours")
        since_hours: float = 24.0
        if since_hours_raw is not None:
            try:
                since_hours = float(since_hours_raw)
            except (TypeError, ValueError):
                return "distill_memory: 'since_hours' must be a number"

        result = await _consolidate.distill_episodes(
            instance_id,
            since_hours=since_hours,
        )
        return f"distill_memory: {result}"

    except Exception as exc:
        return f"distill_memory: error: {exc}"
