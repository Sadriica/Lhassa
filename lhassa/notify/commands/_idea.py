# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/idea``: hand an idea to the geshe's aide department.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

from loguru import logger

from ._registry import register_command
from ._shared import _config


@register_command("idea")
async def _cmd_idea(args: str, *, config=None) -> str:
    """``/idea <text or link>``: hand an idea to the geshe's aid department.

    Persists a signed ``research_intake`` task before acknowledging it.  The
    immediate runner is only an optimization; the heartbeat owns retries.
    """
    idea = (args or "").strip()
    if not idea:
        return (
            "Usage: /idea <the idea, claim or link>; first I'll capture its "
            "sources and evaluate its theory, value, evidence and testability. "
            "Only ideas that earn applied/rigorous investment continue into a "
            "full corpus."
        )
    try:
        import asyncio as _asyncio
        from datetime import datetime as _datetime, timezone as _timezone

        from lhassa.core import dispatch as _dispatch
        from lhassa.security.trust import Origin as _Origin, TrustLevel as _TrustLevel

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        params = {"idea": idea}
        task_id, estado = await _dispatch.mint_task(
            instance_id,
            "research_intake",
            params=params,
            origin=_Origin(trust=_TrustLevel.HUMANO, actor_id="telegram"),
            due_at=_datetime.now(_timezone.utc).isoformat(),
        )
        if not task_id:
            return "I couldn't persist that investigation just now. Try /idea again."
        if estado == "pendiente":
            _asyncio.create_task(
                _dispatch.run_task_now(
                    instance_id,
                    task_id,
                    "research_intake",
                    params,
                    despertar_por="telegram",
                )
            )
    except Exception as exc:
        logger.warning(f"commands: /idea mint failed: {exc}")
        return "I couldn't persist that investigation just now. Try /idea again."
    return (
        "🧭 Idea queued durably for the geshe's aides: source custody → "
        "theory/rubric screening → investment decision. Every stage is logged; "
        "only worthwhile ideas continue into full research and a versioned corpus."
    )
