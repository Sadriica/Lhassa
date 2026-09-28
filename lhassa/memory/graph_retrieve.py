# SPDX-License-Identifier: AGPL-3.0-or-later
"""Graph-augmented retrieval; the "LightRAG-lite" read seam over
``memory/kg.py``'s persisted graph, layered on top of the EXISTING semantic
(layer-4) retrieval :mod:`lhassa.memory.load` already does.

:func:`retrieve` is vector-first, graph-augmented, exactly like the wider
LightRAG-style pattern this wave is named after, but kept deliberately small:

  1. **Seed**: the layer-4 semantic search over fact embeddings (reused as-is
     from ``memory/load.py``'s ``_semantic_layer``, so no second embedding
     index) PLUS a direct name match of the query's terms against stored
     ``kg_entities`` (the graph's own vocabulary).
  2. **Expand 1-hop**: ``db.kg_neighbors`` over the seed entity ids, heaviest
     edge first.
  3. **Map back to facts**: each neighbor entity is resolved to the facts
     whose tags/content mention its name (a bounded scan over this instance's
     resolved facts; there is no free-text index on ``kg_entities`` to query
     against, so this is a linear ``in`` check, not SQL LIKE).
  4. **Merge + rank**: semantic-seed facts first (they already carry the
     embedding's own similarity order), then direct entity-name-match facts,
     then hop-derived facts ordered by the ORIGINATING EDGE'S weight
     (heaviest co-occurrence/mention first); a stronger edge is a more
     reliable hop. Dedup by fact id keeps only the first (highest-ranked)
     citation of a given fact. Capped at ``limit``.

Each result is ``{"text": str, "source": "fact:<id>", "via": str}``: ``via``
is ``"semantic"`` for an embedding hit, ``"entity:<name>"`` for a direct
name-match seed, or ``"<seed> —<edge_kind>→ <neighbor> (<provenance>)"`` for a
1-hop graph-derived fact (e.g.
``"budget —co_occurs→ credit-card (fact:f_123)"``); the trailing
``(<provenance>)`` is the ORIGINATING EDGE's own ``source`` column (the
fact/episode it was learned from; omitted when the edge carries none), so a
caller can always show WHY a fact was pulled in AND what grounded that edge.

$0 beyond the one embedding call the semantic seed already makes (offline with
the default ``HashingEmbedder``). Fully defensive: any internal failure
degrades to ``[]``, never raises.
"""

from __future__ import annotations

from lhassa.config import Config
from lhassa.core import db
from lhassa.memory.facts import load_facts, resolve


def _resolve_config(config):
    return config or Config


def _query_terms(query: str) -> list[str]:
    return [t for t in query.casefold().split() if t]


def _entity_matches(name: str, query_cf: str, terms: list[str]) -> bool:
    name_cf = name.casefold()
    if name_cf in query_cf or query_cf in name_cf:
        return True
    return any(t in name_cf for t in terms)


def _facts_mentioning(all_facts, name: str):
    needle = name.casefold()
    out = []
    for f in all_facts:
        haystack = " ".join([*(f.tags or []), f.contenido or ""]).casefold()
        if needle in haystack:
            out.append(f)
    return out


async def retrieve(
    instance_id: str | None, query: str, *, limit: int = 5, config=None
) -> list[dict]:
    """Vector-seeded, 1-hop graph-expanded fact retrieval. See module docstring.

    An empty/blank query, or any internal failure, degrades to ``[]``: never
    raises, never calls a model beyond the embedder the semantic seed uses.
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        config = _resolve_config(config)
        instance_id = instance_id or config.instance_id
        limit = max(1, int(limit or 5))

        results: list[dict] = []
        seen_fact_ids: set = set()

        # ── (a) seed 1: semantic (layer-4 embedding) facts ──────────────────
        try:
            from lhassa.memory.load import _semantic_layer

            semantic_facts = await _semantic_layer(
                instance_id, query, config, exclude_ids=set(), topk=limit
            )
        except Exception:
            semantic_facts = []
        for f in semantic_facts:
            if f.id in seen_fact_ids:
                continue
            seen_fact_ids.add(f.id)
            results.append({"text": f.contenido, "source": f"fact:{f.id}", "via": "semantic"})

        # ── (a) seed 2: kg entity name match on query terms ─────────────────
        try:
            entities = await db.list_kg_entities(instance_id, limit=500)
        except Exception:
            entities = []
        query_cf = query.casefold()
        terms = _query_terms(query)
        seed_entities = sorted(
            (e for e in entities if _entity_matches(e.get("name") or "", query_cf, terms)),
            key=lambda e: e.get("name") or "",
        )

        try:
            all_facts = resolve(await load_facts(instance_id))
        except Exception:
            all_facts = []

        if len(results) < limit:
            for e in seed_entities:
                if len(results) >= limit:
                    break
                for f in _facts_mentioning(all_facts, e.get("name") or ""):
                    if len(results) >= limit:
                        break
                    if f.id in seen_fact_ids:
                        continue
                    seen_fact_ids.add(f.id)
                    results.append(
                        {"text": f.contenido, "source": f"fact:{f.id}", "via": f"entity:{e.get('name')}"}
                    )

        # ── (b) expand 1-hop from the seed entities ──────────────────────────
        seed_ids = [e.get("id") for e in seed_entities if e.get("id")]
        edges = []
        if seed_ids and len(results) < limit:
            try:
                edges = await db.kg_neighbors(instance_id, seed_ids, limit=50)
            except Exception:
                edges = []

        by_id = {e.get("id"): e.get("name") for e in entities}
        seed_id_set = set(seed_ids)
        hop_items = []
        for edge in edges:
            from_id, to_id = edge.get("from_id"), edge.get("to_id")
            if from_id in seed_id_set and to_id not in seed_id_set:
                seed_name, neighbor_id = by_id.get(from_id, from_id), to_id
            elif to_id in seed_id_set and from_id not in seed_id_set:
                seed_name, neighbor_id = by_id.get(to_id, to_id), from_id
            else:
                continue
            neighbor_name = by_id.get(neighbor_id, neighbor_id)
            if not neighbor_name:
                continue
            hop_items.append(
                (
                    float(edge.get("weight") or 0.0),
                    seed_name,
                    edge.get("kind") or "",
                    neighbor_name,
                    edge.get("source") or "",
                )
            )

        # ── (c)+(d) map neighbors back to facts, ranked by edge weight ──────
        hop_items.sort(key=lambda t: -t[0])
        for _weight, seed_name, kind, neighbor_name, edge_source in hop_items:
            if len(results) >= limit:
                break
            via = f"{seed_name} —{kind}→ {neighbor_name}"
            if edge_source:
                via = f"{via} ({edge_source})"
            for f in _facts_mentioning(all_facts, neighbor_name):
                if len(results) >= limit:
                    break
                if f.id in seen_fact_ids:
                    continue
                seen_fact_ids.add(f.id)
                results.append(
                    {
                        "text": f.contenido,
                        "source": f"fact:{f.id}",
                        "via": via,
                    }
                )

        return results[:limit]
    except Exception:
        return []
