# SPDX-License-Identifier: AGPL-3.0-or-later
"""ingest_knowledge — the deterministic action fronting document perception.

Runs :func:`lhassa.memory.knowledge.ingest` and renders a one-line summary.
Read-only scan of the owner's configured knowledge roots + a local write to the
grande — no external send, no HITL-worthy surface (mirrors ``distill_memory`` /
``calendar_sync``, both also ``requires_approval=False``). A clean no-op when
no roots are configured, so it is safe to schedule unconditionally (see the
``maintenance`` seed). Never raises.
"""

from __future__ import annotations


async def ingest_knowledge(params: dict) -> str:
    """Index the owner's configured knowledge roots. Never raises.

    params:
        config — optional Config override (tests).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        config = params.get("config")

        from lhassa.config import Config
        from lhassa.memory import knowledge

        cfg = config or Config
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"

        if not (getattr(cfg, "knowledge_roots", "") or "").strip():
            return "ingest_knowledge: no knowledge roots set"

        summary = await knowledge.ingest(instance_id, config=cfg)
        return (
            f"ingest_knowledge: indexed {summary['files_indexed']} files, "
            f"{summary['chunks']} chunks across {summary['roots']} root(s); "
            f"skipped {summary['skipped_secrets']} secret-shaped file(s)"
        )
    except Exception as exc:
        return f"ingest_knowledge: {type(exc).__name__}: {exc}"
