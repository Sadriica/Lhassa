"""The PERSISTED knowledge graph (Wave 3, "LightRAG-lite") — entities + typed
edges written to ``kg_entities``/``kg_edges`` (migration 6, ``core/db.py``), fed
$0 from the SAME deterministic extraction :mod:`lhassa.memory.graph` already
uses for its on-the-fly read-model. Where ``memory/graph.py`` recomputes
co-occurrence at query time from raw facts/episodes, this module accumulates it
durably so 1-hop expansion (:mod:`lhassa.memory.graph_retrieve`) doesn't have to
re-scan the whole grande on every retrieval.

Storage is dumb (``core/db.py``'s ``upsert_kg_entity`` / ``upsert_kg_edge`` /
``kg_neighbors``); this module owns the VALIDATION discipline, mirroring
:mod:`lhassa.org.relationships`: a closed edge-kind vocabulary
(:data:`EDGE_KINDS`), no self-edges, and canonical (sorted) endpoint order for
the one symmetric kind so A↔B never duplicates as two rows.

Entity **kind** (the ``kg_entities.kind`` column) is the hook that gives
``mentions`` real meaning without a second table:

  - ``tag``  — an entity that came from a fact's own ``tags`` list. The
    strongest signal (a human tagged it) — mirrors ``memory/graph.py``'s
    ``entities()``, where a tag source "wins" over a text source.
  - ``term`` — an entity pulled out of free text via
    :func:`lhassa.memory.graph.extract_terms` (capitalized/quoted terms).

**Edge kinds**:

  - ``co_occurs`` (SYMMETRIC) — two entities appeared in the same fact or the
    same episode. Weight accumulates once per co-occurring pair per source row
    (the db helper adds on conflict), so a pair seen in 5 facts ends up weight
    5 — a cheap, durable "how related are these" signal.
  - ``mentions`` (DIRECTED, term → tag) — a text-extracted TERM entity and a
    TAG entity appeared in the *same fact*. This is the minimal-but-defensible
    design point of this module: a generic term↔term co-occurrence is already
    ``co_occurs``, so ``mentions`` is reserved for the one relationship that
    carries EXTRA structure — "this free-text term showed up under a fact the
    owner explicitly tagged X" — rather than being a second, redundant
    co-occurrence edge. Episodes have no tags, so episodes never produce
    ``mentions`` edges, only ``co_occurs`` ones.
  - ``derived_from`` (DIRECTED) — reserved for future provenance edges (e.g. a
    distilled/consolidated fact derived from a raw episode). Part of the closed
    vocabulary so :func:`add_edge` already validates it, but :func:`sync` does
    not emit it yet — no current feeder needs it.

:func:`sync` is the $0 feeder: it walks facts + recent episodes SINCE the last
sync (a ``watch_state`` marker, ``kg_sync:last`` — the same house dedup pattern
as the watchlist senses), extracts terms, upserts entities and edges, and
advances the marker. It NEVER calls a model and NEVER raises — any failure
degrades to a zeroed summary. It is also a no-op (zeroed summary, nothing
scanned) when ``config.memory_graph_enabled`` is off, matching the flag's role
as the single on/off switch for the whole feature.
"""

from __future__ import annotations

from itertools import combinations

from lhassa.config import Config
from lhassa.core import db
from lhassa.memory import graph as _graph
from lhassa.memory.facts import load_facts, resolve

# ── the closed edge-kind vocabulary (mirrors org/relationships.py) ──────────
CO_OCCURS = "co_occurs"
MENTIONS = "mentions"
DERIVED_FROM = "derived_from"

EDGE_KINDS: tuple[str, ...] = (CO_OCCURS, MENTIONS, DERIVED_FROM)

#: Which kinds are SYMMETRIC — stored canonically (ids sorted) so A↔B never
#: duplicates as a second row, mirrors ``org/relationships.py:SYMMETRIC_KINDS``.
SYMMETRIC_KINDS: frozenset[str] = frozenset({CO_OCCURS})

# ── entity kinds ──────────────────────────────────────────────────────────────
ENTITY_TAG = "tag"
ENTITY_TERM = "term"

#: Marker key for the incremental feeder (scoped per-instance by watch_state's
#: own instance_id column, so no instance suffix needed here).
_SYNC_MARKER_KEY = "kg_sync:last"

#: Bounded recent-episode scan per sync pass (mirrors memory/graph.py's
#: ``_recent_episodes`` default of 200 — kept a bit wider since this feeder
#: also filters by the marker, so most of the scan is usually a cheap no-op).
_EPISODE_SCAN = 500


class KGValidationError(ValueError):
    """Raised on an invalid graph op (unknown kind, self-edge).

    A :class:`ValueError` subclass so callers can catch one exception type,
    mirroring :class:`lhassa.org.relationships.RelationshipError`.
    """


def _resolve_config(config):
    return config or Config


def _resolve_instance(instance_id: str | None, config) -> str:
    config = _resolve_config(config)
    return instance_id or config.instance_id


def _canonical(from_id: str, to_id: str, kind: str) -> tuple[str, str]:
    """Order the endpoints for storage — sorted for a symmetric kind."""
    if kind in SYMMETRIC_KINDS and to_id < from_id:
        return to_id, from_id
    return from_id, to_id


async def upsert_entity(
    instance_id: str, name: str, *, kind: str = ENTITY_TERM, when: str | None = None
) -> str:
    """Insert-or-touch one entity by ``(instance_id, name)``. Returns its id.

    Thin wrapper over ``db.upsert_kg_entity`` — a fresh id is minted only when
    the name doesn't already exist (the db helper looks it up first).
    """
    when = when or db._now()
    row = {
        "id": db._new_id("kg"),
        "instance_id": instance_id,
        "name": name,
        "kind": kind,
        "first_seen": when,
        "last_seen": when,
    }
    return await db.upsert_kg_entity(row)


async def add_edge(
    instance_id: str,
    from_id: str,
    to_id: str,
    kind: str,
    *,
    weight: float = 1.0,
    source: str | None = None,
    when: str | None = None,
) -> None:
    """Validate then persist one edge (or accumulate its weight on conflict).

    Raises :class:`KGValidationError` for an unknown ``kind`` or a self-edge
    (``from_id == to_id``) — the same discipline as
    ``org/relationships.py``'s mutating ops, so a bad call fails loudly and
    NEVER corrupts storage. Callers that walk untrusted/derived input (like
    :func:`sync`) should call this inside their own try/except — this
    function itself does not swallow errors, it IS the validation gate.
    """
    if kind not in EDGE_KINDS:
        raise KGValidationError(f"unknown edge kind: {kind!r}")
    if from_id == to_id:
        raise KGValidationError(f"self-edge refused: {from_id!r}")
    a, b = _canonical(from_id, to_id, kind)
    when = when or db._now()
    await db.upsert_kg_edge(
        {
            "instance_id": instance_id,
            "from_id": a,
            "to_id": b,
            "kind": kind,
            "weight": weight,
            "source": source,
            "created_at": when,
            "updated_at": when,
        }
    )


async def _new_facts_since(instance_id: str, marker: str) -> list:
    all_facts = resolve(await load_facts(instance_id))
    if not marker:
        return all_facts
    return [f for f in all_facts if (f.creado_en or "") > marker]


async def _new_episodes_since(instance_id: str, marker: str) -> list[dict]:
    episodes = await db.load_recent_episodes(instance_id, _EPISODE_SCAN)
    if not marker:
        return episodes
    return [ep for ep in episodes if (ep.get("creado_en") or "") > marker]


async def sync(instance_id: str | None = None, *, config=None) -> dict:
    """The $0 feeder: extract entities/edges from facts+episodes since the last
    sync, upsert them, and advance the ``kg_sync:last`` marker.

    Per fact: tag terms (``fact.tags``, entity kind ``"tag"``) and text terms
    (:func:`lhassa.memory.graph.extract_terms` over ``contenido``, entity kind
    ``"term"``) are combined into ``co_occurs`` edges (every pair, symmetric),
    plus one ``mentions`` edge per (text term → tag) pair in that fact — see
    the module docstring for why ``mentions`` is scoped to that one
    relationship. Per episode: only text terms (episodes carry no tags), so
    only ``co_occurs`` edges. Entities are upserted TEXT-terms-first,
    TAG-terms-second within a sync pass, so a name that is both a tag and a
    text term ends the pass with ``kind="tag"`` — the stronger signal wins,
    mirroring ``memory/graph.py``'s own "once tagged, stays tagged" rule.

    Returns ``{"entities": n, "edges": n, "scanned": n}`` — ``entities`` counts
    distinct names touched this pass (existing entities re-touched count too,
    same as the db upsert semantics), ``edges`` counts edge-write operations
    (weight accumulations included), ``scanned`` counts new fact+episode rows
    considered. A disabled ``memory_graph_enabled`` flag, or any internal
    failure, degrades to a zeroed summary — this never raises.
    """
    zero = {"entities": 0, "edges": 0, "scanned": 0}
    try:
        config = _resolve_config(config)
        if not getattr(config, "memory_graph_enabled", False):
            return zero
        instance_id = _resolve_instance(instance_id, config)

        marker = await db.get_watch_state(instance_id, _SYNC_MARKER_KEY) or ""
        new_facts = await _new_facts_since(instance_id, marker)
        new_episodes = await _new_episodes_since(instance_id, marker)
        scanned = len(new_facts) + len(new_episodes)
        if scanned == 0:
            return zero

        # Two passes so tag terms are upserted LAST (kind priority — see docstring).
        text_terms_by_fact: list[tuple[set, set, str, str]] = []  # (text, tag, when, source)
        for f in new_facts:
            text_terms = _graph.extract_terms(f.contenido)
            tag_terms = {t.strip() for t in (f.tags or []) if t and t.strip()}
            text_terms_by_fact.append((text_terms, tag_terms, f.creado_en or db._now(), f"fact:{f.id}"))

        episode_terms_list: list[tuple[set, str, str]] = []
        for ep in new_episodes:
            terms = _graph.episode_terms(ep)
            episode_terms_list.append((terms, ep.get("creado_en") or db._now(), f"episode:{ep.get('id')}"))

        touched_names: set[str] = set()
        entity_ids: dict[str, str] = {}

        async def _ensure(name: str, kind: str, when: str) -> str:
            eid = await upsert_entity(instance_id, name, kind=kind, when=when)
            entity_ids[name] = eid
            touched_names.add(name)
            return eid

        # Pass 1 — text terms (fact + episode), kind "term".
        for text_terms, _tags, when, _src in text_terms_by_fact:
            for name in text_terms:
                await _ensure(name, ENTITY_TERM, when)
        for terms, when, _src in episode_terms_list:
            for name in terms:
                await _ensure(name, ENTITY_TERM, when)

        # Pass 2 — tag terms, kind "tag" (overwrites any same-name term entity).
        for _text, tag_terms, when, _src in text_terms_by_fact:
            for name in tag_terms:
                await _ensure(name, ENTITY_TAG, when)

        edges = 0

        # co_occurs — every pair within a fact's combined term set, and within
        # an episode's text-term set.
        for text_terms, tag_terms, when, source in text_terms_by_fact:
            combined = sorted(text_terms | tag_terms)
            for a, b in combinations(combined, 2):
                try:
                    await add_edge(
                        instance_id, entity_ids[a], entity_ids[b], CO_OCCURS,
                        source=source, when=when,
                    )
                    edges += 1
                except KGValidationError:
                    continue
            # mentions — directed text term → tag, same fact only.
            for text_name in text_terms:
                for tag_name in tag_terms:
                    try:
                        await add_edge(
                            instance_id, entity_ids[text_name], entity_ids[tag_name],
                            MENTIONS, source=source, when=when,
                        )
                        edges += 1
                    except KGValidationError:
                        continue

        for terms, when, source in episode_terms_list:
            combined = sorted(terms)
            for a, b in combinations(combined, 2):
                try:
                    await add_edge(
                        instance_id, entity_ids[a], entity_ids[b], CO_OCCURS,
                        source=source, when=when,
                    )
                    edges += 1
                except KGValidationError:
                    continue

        # Advance the marker to the latest creado_en actually processed.
        timestamps = [w for _, _, w, _ in text_terms_by_fact] + [w for _, w, _ in episode_terms_list]
        new_marker = max(timestamps) if timestamps else marker
        if new_marker and new_marker > marker:
            await db.set_watch_state(instance_id, _SYNC_MARKER_KEY, new_marker)

        return {"entities": len(touched_names), "edges": edges, "scanned": scanned}
    except Exception:
        return zero
