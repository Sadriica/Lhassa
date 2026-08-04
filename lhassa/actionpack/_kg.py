# SPDX-License-Identifier: AGPL-3.0-or-later
"""kg_sync / kg_resolve — the deterministic actions fronting the persisted
knowledge graph.

``kg_sync`` runs :func:`lhassa.memory.kg.sync` and renders a one-line summary.
Read-only scan of facts/episodes since the last sync + a local write to the
grande's ``kg_entities``/``kg_edges`` tables — no external send, no
HITL-worthy surface (mirrors ``ingest_knowledge``/``distill_memory``, both
also ``requires_approval=False``). A clean no-op when
``config.memory_graph_enabled`` is off (the default), so it is safe to
schedule unconditionally. Never raises.

``kg_resolve`` runs :func:`lhassa.memory.kg.resolve` (entity deduplication —
see its docstring for the deterministic prefilter + optional-model merge
rule) and renders a one-line summary. Same profile as ``kg_sync``: a local
write only (merges entities/repoints edges within this instance's own
``kg_entities``/``kg_edges``), no external send, ``requires_approval=False``.
Never raises.
"""

from __future__ import annotations


def kg_possible(params: dict) -> str:
    """Blocked while the persisted knowledge graph is off — both kg actions no-op."""
    try:
        from lhassa.config import Config

        cfg = (params or {}).get("config") or Config
        enabled = getattr(cfg, "memory_graph_enabled", False)
        return "" if enabled else "knowledge graph disabled (LHASSA_MEMORY_GRAPH)"
    except Exception:
        return ""


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


async def kg_resolve(params: dict) -> str:
    """Merge duplicate knowledge-graph entities. Never raises.

    params:
        config    — optional Config override (tests).
        max_pairs — optional cap on candidate pairs judged this pass
                    (defaults to ``kg.resolve``'s own default, 20).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        config = params.get("config")
        max_pairs = params.get("max_pairs")

        from lhassa.config import Config
        from lhassa.memory import kg

        cfg = config or Config
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"

        if not getattr(cfg, "memory_graph_enabled", False):
            return "kg_resolve: memory graph disabled (LHASSA_MEMORY_GRAPH)"

        kwargs = {"config": cfg}
        if max_pairs is not None:
            kwargs["max_pairs"] = int(max_pairs)

        summary = await kg.resolve(instance_id, **kwargs)
        return (
            f"kg_resolve: merged {summary['merged']} of {summary['candidates']} "
            f"candidate(s), {summary['skipped']} skipped"
        )
    except Exception as exc:
        return f"kg_resolve: {type(exc).__name__}: {exc}"
