"""kg_sync — the deterministic action fronting the persisted knowledge graph.

Runs :func:`lhassa.memory.kg.sync` and renders a one-line summary. Read-only
scan of facts/episodes since the last sync + a local write to the grande's
``kg_entities``/``kg_edges`` tables — no external send, no HITL-worthy surface
(mirrors ``ingest_knowledge``/``distill_memory``, both also
``requires_approval=False``). A clean no-op when ``config.memory_graph_enabled``
is off (the default), so it is safe to schedule unconditionally. Never raises.
"""

from __future__ import annotations


async def kg_sync(params: dict) -> str:
    """Feed the persisted knowledge graph from facts+episodes. Never raises.

    params:
        config — optional Config override (tests).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        config = params.get("config")

        from lhassa.config import Config
        from lhassa.memory import kg

        cfg = config or Config
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"

        if not getattr(cfg, "memory_graph_enabled", False):
            return "kg_sync: memory graph disabled (LHASSA_MEMORY_GRAPH)"

        summary = await kg.sync(instance_id, config=cfg)
        return (
            f"kg_sync: touched {summary['entities']} entities, "
            f"{summary['edges']} edges from {summary['scanned']} new row(s)"
        )
    except Exception as exc:
        return f"kg_sync: {type(exc).__name__}: {exc}"
