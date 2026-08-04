# SPDX-License-Identifier: AGPL-3.0-or-later
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
  - ``asserts`` (DIRECTED, subject → object) — OPT-IN (``config.kg_triples_enabled``,
    ``LHASSA_KG_TRIPLES``, off by default), fed by a cheap-model pass over the
    SAME new rows the regex pass already gathered (see :func:`sync`). The edge
    schema has no predicate column and this module may not alter it, so the
    predicate rides inside the edge's own ``source`` string, appended after the
    usual provenance: ``"fact:<id>|pred:<verb>"`` / ``"episode:<id>|pred:<verb>"``.
    It is a schema-free, honestly-documented hack rather than a real column —
    a reader that wants the predicate splits on ``"|pred:"``.

:func:`sync` is the $0 feeder: it walks facts + recent episodes SINCE the last
sync (a ``watch_state`` marker, ``kg_sync:last`` — the same house dedup pattern
as the watchlist senses), extracts terms, upserts entities and edges, and
advances the marker. It NEVER calls a model and NEVER raises — any failure
degrades to a zeroed summary. It is also a no-op (zeroed summary, nothing
scanned) when ``config.memory_graph_enabled`` is off, matching the flag's role
as the single on/off switch for the whole feature. With ``kg_triples_enabled``
on, a SECOND pass (still inside the same try/except) asks the routine model
(mirrors :mod:`lhassa.memory.consolidate`'s cheap-model pattern: ``llm.router``
picks the provider/model, ``BudgetExceeded``/``ProviderNotConfigured`` degrade
to skipping that batch, never raising) for grounded subject/predicate/object
triples over the same new facts/episodes, batched at
:data:`_TRIPLE_BATCH_SIZE` items per completion. Extracted triples become
``asserts`` edges between two ``kind="term"`` entities; the regex-derived
edges from pass one always stand regardless of what the model pass does.

:func:`resolve` is the entity-resolution pass (also opt-in via
``kg_triples_enabled`` for its model-assisted half): a deterministic prefilter
finds candidate duplicate pairs among same-kind entities, EXACT normalized
matches merge immediately with no model, and the remaining fuzzy candidates
are only merged when a routine-model judgment call says they are the same
entity — never guessed. See its docstring for the exact prefilter rule.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from itertools import combinations

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.llm import router as _router
from lhassa.llm.gateway import BudgetExceeded
from lhassa.llm.provider import ProviderNotConfigured
from lhassa.memory import graph as _graph
from lhassa.memory.facts import load_facts, resolve as resolve_facts

# ── the closed edge-kind vocabulary (mirrors org/relationships.py) ──────────
CO_OCCURS = "co_occurs"
MENTIONS = "mentions"
DERIVED_FROM = "derived_from"
ASSERTS = "asserts"

EDGE_KINDS: tuple[str, ...] = (CO_OCCURS, MENTIONS, DERIVED_FROM, ASSERTS)

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

#: Cheap-model triple extraction (kg_triples_enabled) — one completion per
#: batch of up to this many source items (facts + episodes combined).
_TRIPLE_BATCH_SIZE = 20
_TRIPLE_MAX_TOKENS = 800

#: Entity-resolution (resolve()) — one bounded completion judging up to this
#: many fuzzy candidate pairs.
_RESOLVE_MAX_TOKENS = 600
#: A pair is a fuzzy CANDIDATE (not auto-merged, needs a model or a human) when
#: their normalized names' difflib ratio reaches this.
_RESOLVE_RATIO_THRESHOLD = 0.6
#: ...or when they share a normalized token at least this long (a surname,
#: a distinctive word) — catches "Buzz Aldrin"/"Edwin Aldrin" even though
#: their overall ratio is unremarkable.
_RESOLVE_SHARED_TOKEN_MIN_LEN = 3


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
    all_facts = resolve_facts(await load_facts(instance_id))
    if not marker:
        return all_facts
    return [f for f in all_facts if (f.creado_en or "") > marker]


async def _new_episodes_since(instance_id: str, marker: str) -> list[dict]:
    episodes = await db.load_recent_episodes(instance_id, _EPISODE_SCAN)
    if not marker:
        return episodes
    return [ep for ep in episodes if (ep.get("creado_en") or "") > marker]


def _extract_json_array(text: str) -> list:
    """Best-effort parse of a JSON array out of a model answer. Never raises.

    Copied (not imported) from :func:`lhassa.memory.consolidate._extract_json_array`
    — same module layer (``memory`` -> ``memory``), but kept local so this
    module has no dependency on another module's private helper. Handles a
    bare array, a ```json fenced block, or an array embedded in prose.
    """
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, TypeError):
        pass
    fenced = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, list):
                return parsed
        except (ValueError, TypeError):
            pass
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


# ── C1: opt-in cheap-model triple extraction (kg_triples_enabled) ────────────


def _triple_source_items(new_facts: list, new_episodes: list) -> list[dict]:
    """Reuse the SAME incremental rows the regex pass already gathered — one
    ``{"source", "text"}`` item per fact/episode with non-empty text."""
    items: list[dict] = []
    for f in new_facts:
        text = (f.contenido or "").strip()
        if text:
            items.append({"source": f"fact:{f.id}", "text": text})
    for ep in new_episodes:
        text = _graph._episode_text(ep).strip()
        if text:
            items.append({"source": f"episode:{ep.get('id')}", "text": text})
    return items


def _triple_messages(items: list[dict]) -> list[dict]:
    lines = "\n".join(f"- {it['source']}: {it['text']}" for it in items)
    prompt = (
        "Extract simple subject-predicate-object facts explicitly stated in the "
        "texts below, each labeled `- <source_id>: <text>`. Do NOT infer or "
        "guess — only facts the text actually says. Return ONLY a JSON array of "
        'the form [{"subject": "...", "predicate": "...", "object": "...", '
        '"source": "<source_id>"}], reusing EXACTLY one of the given <source_id> '
        "values for each item's \"source\" field. If nothing can be extracted, "
        "return [].\n\nTexts:\n" + lines
    )
    return [{"role": "user", "content": prompt}]


def _parse_triples(
    text: str, source_texts: dict[str, str], *, dropped: list[str] | None = None
) -> list[dict]:
    """Parse+validate the model's triple array.

    Every field must be non-empty, ``source`` must be one of the ids actually
    offered in the prompt (a hallucinated source is dropped, not trusted), and
    subject != object. Beyond that, the GROUNDING guard: subject AND object
    must each appear (case/accent-insensitive substring) somewhere in the cited
    source's own text — a triple naming something the source never said is a
    model hallucination of CONTENT, not just of the source id, and is dropped
    the same way. Dropped triples are counted into ``dropped`` (honest toward
    the log) but never surfaced to the caller — the model never learns which
    ones were rejected.
    """
    out: list[dict] = []
    for raw in _extract_json_array(text):
        if not isinstance(raw, dict):
            continue
        subject = str(raw.get("subject") or "").strip()
        predicate = str(raw.get("predicate") or "").strip()
        obj = str(raw.get("object") or "").strip()
        source = str(raw.get("source") or "").strip()
        if not subject or not predicate or not obj or source not in source_texts:
            if dropped is not None:
                dropped.append(source or "<missing-source>")
            continue
        if subject.casefold() == obj.casefold():
            continue  # would be a self-edge
        source_norm = _normalize_name(source_texts[source])
        if _normalize_name(subject) not in source_norm or _normalize_name(obj) not in source_norm:
            if dropped is not None:
                dropped.append(source)
            continue  # ungrounded — subject/object never actually appear in the source
        out.append({"subject": subject, "predicate": predicate, "object": obj, "source": source})
    return out


async def _run_triple_extraction(
    instance_id: str,
    items: list[dict],
    *,
    config,
    provider,
    model,
    completion_fn,
    entity_ids: dict,
    touched_names: set,
) -> int:
    """Batch ``items`` and ask the routine model for grounded triples.

    Mirrors :mod:`lhassa.memory.consolidate`'s cheap-model call pattern:
    ``llm.router`` resolves the provider/model (unless injected for tests),
    ``BudgetExceeded``/``ProviderNotConfigured`` (and any other failure)
    degrade to skipping — the regex-derived edges :func:`sync` already wrote
    stand regardless. Returns the number of ``asserts`` edges written.
    """
    if not items:
        return 0
    try:
        resolved_provider = provider or _router.pick_provider(
            sensitive=False, hard=False, config=config
        )
        resolved_model = model or _router.pick_model(resolved_provider, hard=False, config=config)
    except (BudgetExceeded, ProviderNotConfigured):
        return 0
    except Exception:
        return 0

    edges_written = 0
    for start in range(0, len(items), _TRIPLE_BATCH_SIZE):
        batch = items[start : start + _TRIPLE_BATCH_SIZE]
        try:
            opts = {"completion_fn": completion_fn} if completion_fn is not None else {}
            completion = await resolved_provider.complete(
                _triple_messages(batch), model=resolved_model, max_tokens=_TRIPLE_MAX_TOKENS, **opts,
            )
        except (BudgetExceeded, ProviderNotConfigured):
            continue  # this batch degrades; a later batch may still succeed
        except Exception:
            continue

        source_texts = {it["source"]: it["text"] for it in batch}
        dropped: list[str] = []
        triples = _parse_triples(completion.text, source_texts, dropped=dropped)
        if dropped:
            logger.debug(
                f"kg._run_triple_extraction: dropped {len(dropped)} ungrounded/"
                f"hallucinated triple(s) (sources={dropped})"
            )
        when = db._now()
        for t in triples:
            try:
                subj_id = await upsert_entity(instance_id, t["subject"], kind=ENTITY_TERM, when=when)
                obj_id = await upsert_entity(instance_id, t["object"], kind=ENTITY_TERM, when=when)
                entity_ids[t["subject"]] = subj_id
                entity_ids[t["object"]] = obj_id
                touched_names.add(t["subject"])
                touched_names.add(t["object"])
                source = f"{t['source']}|pred:{t['predicate']}"
                await add_edge(instance_id, subj_id, obj_id, ASSERTS, source=source, when=when)
                edges_written += 1
            except KGValidationError:
                continue
            except Exception:
                continue
    return edges_written


async def sync(
    instance_id: str | None = None,
    *,
    config=None,
    provider=None,
    model=None,
    completion_fn=None,
) -> dict:
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

    With ``config.kg_triples_enabled`` on, a THIRD pass (:func:`_run_triple_extraction`)
    asks the routine model for grounded subject/predicate/object triples over the
    same new facts/episodes and writes them as ``asserts`` edges — see the module
    docstring. Off (the default), this pass never runs and behavior is
    byte-identical to before it existed. ``provider``/``model``/``completion_fn``
    are test-injection seams for that pass only; the regex passes above never
    touch a provider.

    Returns ``{"entities": n, "edges": n, "scanned": n}`` — ``entities`` counts
    distinct names touched this pass (existing entities re-touched count too,
    same as the db upsert semantics), ``edges`` counts edge-write operations
    (weight accumulations included, ``asserts`` edges from the triples pass
    included too), ``scanned`` counts new fact+episode rows considered. A
    disabled ``memory_graph_enabled`` flag, or any internal failure, degrades to
    a zeroed summary — this never raises.
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

        # Optional third pass — cheap-model triple extraction (see docstring).
        # Runs INSIDE the same try/except as everything above: any failure
        # here degrades this pass alone, never the regex edges already written.
        if getattr(config, "kg_triples_enabled", False):
            triple_items = _triple_source_items(new_facts, new_episodes)
            edges += await _run_triple_extraction(
                instance_id,
                triple_items,
                config=config,
                provider=provider,
                model=model,
                completion_fn=completion_fn,
                entity_ids=entity_ids,
                touched_names=touched_names,
            )

        # Advance the marker to the latest creado_en actually processed.
        timestamps = [w for _, _, w, _ in text_terms_by_fact] + [w for _, w, _ in episode_terms_list]
        new_marker = max(timestamps) if timestamps else marker
        if new_marker and new_marker > marker:
            await db.set_watch_state(instance_id, _SYNC_MARKER_KEY, new_marker)

        return {"entities": len(touched_names), "edges": edges, "scanned": scanned}
    except Exception:
        return zero


# ── C2: entity resolution ─────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    """Casefold + accent-strip a name for comparison — NOT persisted, only
    used as the resolve() comparison key."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold().strip()


def _is_fuzzy_candidate(name_a: str, name_b: str) -> bool:
    """Prefilter rule for a NON-exact candidate pair (documented on :func:`resolve`).

    A candidate when the normalized names share a token at least
    :data:`_RESOLVE_SHARED_TOKEN_MIN_LEN` chars long (catches a shared surname,
    e.g. "buzz aldrin" / "edwin aldrin") OR their overall
    ``difflib.SequenceMatcher`` ratio reaches :data:`_RESOLVE_RATIO_THRESHOLD`
    (catches near-identical single-token names / typos). Deliberately
    permissive — a false-positive candidate only costs an extra line in the
    model prompt (or is silently skipped with no model); it is never
    auto-merged without either an exact match or an explicit "same" judgment.
    """
    if not name_a or not name_b:
        return False
    tokens_a, tokens_b = set(name_a.split()), set(name_b.split())
    shared = {t for t in (tokens_a & tokens_b) if len(t) >= _RESOLVE_SHARED_TOKEN_MIN_LEN}
    if shared:
        return True
    return difflib.SequenceMatcher(None, name_a, name_b).ratio() >= _RESOLVE_RATIO_THRESHOLD


def _candidate_pairs(entities: list[dict], max_pairs: int) -> list[tuple[dict, dict]]:
    """Deterministic prefilter: same-``kind`` entity pairs worth a merge
    decision. Exact-normalized pairs sort first (they need no model at all),
    then fuzzy candidates (see :func:`_is_fuzzy_candidate`), capped at
    ``max_pairs`` total.
    """
    by_kind: dict[str, list[dict]] = {}
    for e in entities:
        by_kind.setdefault(e.get("kind") or ENTITY_TERM, []).append(e)

    exact: list[tuple[dict, dict]] = []
    fuzzy: list[tuple[dict, dict]] = []
    for group in by_kind.values():
        for a, b in combinations(group, 2):
            na, nb = _normalize_name(a.get("name") or ""), _normalize_name(b.get("name") or "")
            if not na or not nb:
                continue
            if na == nb:
                exact.append((a, b))
            elif _is_fuzzy_candidate(na, nb):
                fuzzy.append((a, b))

    return (exact + fuzzy)[:max_pairs]


def _resolve_messages(pairs: list[tuple[dict, dict]]) -> list[dict]:
    lines = [
        f'- a="{a.get("id")}" ({a.get("name")!r}, kind={a.get("kind")}) vs '
        f'b="{b.get("id")}" ({b.get("name")!r}, kind={b.get("kind")})'
        for a, b in pairs
    ]
    prompt = (
        "For each candidate pair below, judge whether the two names refer to "
        "the SAME real-world entity. Return ONLY a JSON array of the form "
        '[{"a": "<id>", "b": "<id>", "same": true|false}] — one entry per pair, '
        "reusing the exact ids given (do not invent new ones).\n\nPairs:\n"
        + "\n".join(lines)
    )
    return [{"role": "user", "content": prompt}]


def _parse_judgments(text: str) -> list[dict]:
    out: list[dict] = []
    for raw in _extract_json_array(text):
        if isinstance(raw, dict) and raw.get("a") and raw.get("b"):
            out.append(raw)
    return out


async def resolve(
    instance_id: str | None = None,
    *,
    config=None,
    max_pairs: int = 20,
    provider=None,
    model=None,
    completion_fn=None,
) -> dict:
    """Merge duplicate entities: deterministic prefilter + optional model judgment.

    1. **Prefilter** (:func:`_candidate_pairs`) — among same-``kind`` entities,
       normalize each name (casefold + accent-strip) and pair up: EXACT
       normalized matches, and FUZZY candidates (a shared ≥3-char token, e.g. a
       surname, OR a ``difflib`` ratio ≥0.6). Capped at ``max_pairs`` total.
    2. **Exact pairs merge immediately** — no model needed, they are trivially
       safe (:func:`lhassa.core.db.merge_kg_entities`, keeping the earlier-seen
       entity of the two).
    3. **Fuzzy pairs** only merge WITH a routine model available (also gated on
       ``config.kg_triples_enabled`` — the same "cheap model may run" flag the
       triples pass uses) AND that model's judgment for that specific pair says
       ``"same": true``. With no model (flag off, or the provider unavailable —
       ``BudgetExceeded``/``ProviderNotConfigured`` degrade the same way as
       :func:`_run_triple_extraction`), fuzzy candidates are left untouched —
       never guessed.

    ``provider``/``model``/``completion_fn`` are test-injection seams (mirrors
    :func:`_run_triple_extraction`) — when omitted, the routine model is
    resolved through ``llm.router`` exactly like the triples pass does.

    Returns ``{"merged": n, "candidates": n, "skipped": n}`` — ``candidates`` is
    every pair the prefilter found (exact + fuzzy), ``merged`` is how many pairs
    were actually merged, ``skipped`` is fuzzy candidates that were NOT merged
    (no model, model unavailable, or judged not-same). Never raises.
    """
    zero = {"merged": 0, "candidates": 0, "skipped": 0}
    try:
        config = _resolve_config(config)
        instance_id = _resolve_instance(instance_id, config)

        entities = await db.list_kg_entities(instance_id, limit=500)
        if len(entities) < 2:
            return zero

        pairs = _candidate_pairs(entities, max_pairs)
        if not pairs:
            return zero

        exact_pairs: list[tuple[dict, dict]] = []
        fuzzy_pairs: list[tuple[dict, dict]] = []
        for a, b in pairs:
            na, nb = _normalize_name(a.get("name") or ""), _normalize_name(b.get("name") or "")
            (exact_pairs if na == nb else fuzzy_pairs).append((a, b))

        dropped: set = set()
        merged = 0

        async def _merge_pair(a: dict, b: dict) -> bool:
            if a["id"] in dropped or b["id"] in dropped:
                return False
            keep, drop = (a, b) if (a.get("first_seen") or "") <= (b.get("first_seen") or "") else (b, a)
            await db.merge_kg_entities(instance_id, keep["id"], drop["id"])
            dropped.add(drop["id"])
            return True

        for a, b in exact_pairs:
            if await _merge_pair(a, b):
                merged += 1

        remaining_fuzzy = [
            (a, b) for a, b in fuzzy_pairs if a["id"] not in dropped and b["id"] not in dropped
        ]
        judged_same = 0
        if remaining_fuzzy and getattr(config, "kg_triples_enabled", False):
            resolved_provider = provider
            resolved_model = model
            if resolved_provider is None:
                try:
                    resolved_provider = _router.pick_provider(sensitive=False, hard=False, config=config)
                    resolved_model = resolved_model or _router.pick_model(
                        resolved_provider, hard=False, config=config
                    )
                except (BudgetExceeded, ProviderNotConfigured):
                    resolved_provider = None
                except Exception:
                    resolved_provider = None

            if resolved_provider is not None:
                try:
                    opts = {"completion_fn": completion_fn} if completion_fn is not None else {}
                    completion = await resolved_provider.complete(
                        _resolve_messages(remaining_fuzzy),
                        model=resolved_model,
                        max_tokens=_RESOLVE_MAX_TOKENS,
                        **opts,
                    )
                    judgments = _parse_judgments(completion.text)
                except (BudgetExceeded, ProviderNotConfigured):
                    judgments = []
                except Exception:
                    judgments = []

                by_id = {e["id"]: e for e in entities}
                for j in judgments:
                    if not j.get("same"):
                        continue
                    a = by_id.get(j.get("a"))
                    b = by_id.get(j.get("b"))
                    if not a or not b:
                        continue
                    if await _merge_pair(a, b):
                        merged += 1
                        judged_same += 1

        skipped = len(fuzzy_pairs) - judged_same
        return {"merged": merged, "candidates": len(pairs), "skipped": skipped}
    except Exception:
        return zero
