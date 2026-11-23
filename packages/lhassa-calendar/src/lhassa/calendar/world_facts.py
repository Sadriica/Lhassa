# SPDX-License-Identifier: AGPL-3.0-or-later
"""``derive_world_facts``: persist the calendar-derived world model (an action).

The persisting half of the calendar-derived world model: calls
:func:`lhassa.calendar.derive.derive_world` (leaf, $0, deterministic; read
+ analysis only, never writes) then persists each NEW candidate to the
grande via :mod:`lhassa.memory.facts`. Dedup reuses the SAME idiom the
Telegram ``[[remember: …]]`` marker uses
(:func:`lhassa.chat.markers.apply_remember_marker`): a
case-insensitive substring overlap with an existing fact, either direction,
skips the write; so re-running this action as the calendar reshuffles week
to week never piles up near-duplicate facts.

Fits the actionpack calling convention: a plain ``(params) -> str`` callable,
never raises, degrades to "nothing to derive" when there's no calendar/no
events at all; so it's safe to schedule unconditionally (see the
``maintenance`` seed).
"""

from __future__ import annotations

from loguru import logger

from lhassa.config import Config


def _overlaps(existing_lower: list[str], contenido: str) -> bool:
    low = contenido.strip().lower()
    if not low:
        return True
    return any(low in other or other in low for other in existing_lower if other)


async def derive_world_facts(params: dict) -> str:
    """Derive the owner's world model from their calendar and persist new facts.

    params:
        instance_id ; defaults to Config.instance_id.
        days        ; look-ahead window in days (default 45).
        config      ; an injectable Config (tests only); defaults to Config().

    Returns ``"derive_world_facts: derived N world facts, M new"`` (M <= N
    after dedup), or ``"nothing to derive"`` when the calendar has no events
    at all. Never raises.
    """
    try:
        params = params or {}
        config = params.get("config") or Config()
        instance_id = params.get("instance_id") or getattr(
            config, "instance_id", None
        ) or Config.instance_id
        days = int(params.get("days") or 45)

        from lhassa.calendar.derive import derive_world

        candidates = await derive_world(instance_id, config=config, days=days)
        if not candidates:
            return "nothing to derive"

        from lhassa.memory import facts as _facts

        existing = await _facts.load_facts(instance_id)
        existing_lower = [str(getattr(f, "contenido", "") or "").lower() for f in existing]

        new_count = 0
        for candidate in candidates:
            contenido = str(candidate.get("contenido") or "").strip()
            if not contenido or _overlaps(existing_lower, contenido):
                continue
            await _facts.add_fact(
                instance_id,
                "dato",
                contenido,
                tags=candidate.get("tags") or ["calendar"],
                procedencia=candidate.get("procedencia") or "calendar-derived",
                confianza=float(candidate.get("confianza") or 0.6),
            )
            existing_lower.append(contenido.lower())
            new_count += 1

        return f"derive_world_facts: derived {len(candidates)} world facts, {new_count} new"
    except Exception as exc:
        logger.warning("derive_world_facts: error: {}", exc)
        return f"derive_world_facts: error: {exc}"
