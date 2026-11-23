# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The knowledge graph; a deterministic READ-MODEL over what the grande already
stores. No new heavy storage, no model call: entities and edges are derived on
the fly from fact tags/contenido and the episode log (via the existing FTS5
recall index), so "what do I know about X?" is answered from stored facts
without ever spending a token.

Entity extraction (simple, documented, $0):
  - every fact **tag** is an entity (the strongest signal; a human tagged it).
  - a lightweight regex over fact ``contenido`` (and, for co-occurrence, episode
    text) pulls out capitalized-multiword terms ("Sezzle", "Ana Torres") and
    quoted terms ('"the Q3 report"'), lowercased tags aside.
  - honest limits: this is NOT NLP; no lemmatization, no coreference ("my
    sister" and "Ana" are not linked unless a fact ties them together), no
    disambiguation between two entities that share a surface form, and a
    capitalized common word at a sentence start ("The owner likes...") can
    slip in as a false positive. It is deliberately just useful, not perfect.

Edges are **co-occurrence**: two entities are "related" when they appear
together in the same fact or episode. There is no separate edge table; the
graph is computed at query time from the rows :func:`load_facts` /
:func:`search_episodes` / :func:`load_recent_episodes` already return.

Every function here is defensive: on any error (missing DB, bad state) it
degrades to an empty result; it never raises and never calls a model.
"""

from __future__ import annotations

import re
from collections import Counter

from lhassa.config import Config
from lhassa.core import db
from lhassa.memory.facts import Fact, load_facts, resolve

# A capitalized multiword term ("Sezzle", "Ana Torres", "San Francisco"); two+
# letters starting with an uppercase, optionally chained by a space into further
# capitalized words. Deliberately simple: no NLP, just a useful surface-form cue.
_CAP_TERM_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*\b")

# A quoted term: 'single' or "double" quoted spans of two+ chars.
_QUOTED_RE = re.compile(r"[\"“]([^\"”]{2,60})[\"”]|'([^']{2,60})'")

# Common leading words that are capitalized only by sentence position, not
# because they name an entity; filtered out so they don't pollute the graph.
_STOPWORDS = {
    "The", "A", "An", "It", "I", "This", "That", "These", "Those", "My", "Its",
    "Owner", "Lhassa",
}


def _extract_terms(text: str) -> set[str]:
    """Pull candidate entity terms out of one string of text. $0, regex-only."""
    if not text:
        return set()
    terms: set[str] = set()
    for m in _CAP_TERM_RE.finditer(text):
        term = m.group(0).strip()
        if term and term not in _STOPWORDS:
            terms.add(term)
    for m in _QUOTED_RE.finditer(text):
        term = (m.group(1) or m.group(2) or "").strip()
        if term:
            terms.add(term)
    return terms


def _fact_terms(fact: Fact) -> set[str]:
    """All entity terms a fact carries: its tags plus text-extracted terms."""
    terms = {t.strip() for t in (fact.tags or []) if t and t.strip()}
    terms |= _extract_terms(fact.contenido)
    return terms


def _episode_text(ep: dict) -> str:
    return " ".join(
        str(ep.get(k) or "") for k in ("decision", "accion", "resultado", "despertar_por")
    )


def _episode_terms(ep: dict) -> set[str]:
    return _extract_terms(_episode_text(ep))


def _matches(query: str, terms: set[str]) -> bool:
    """Case-insensitive: does ``query`` match (as a substring) any term?"""
    q = query.strip().casefold()
    if not q:
        return False
    return any(q in t.casefold() or t.casefold() in q for t in terms)


def _resolve_instance(instance_id: str | None, config) -> str:
    config = config or Config
    return instance_id or config.instance_id


async def _all_facts(instance_id: str) -> list[Fact]:
    try:
        return resolve(await load_facts(instance_id))
    except Exception:
        return []


async def _recent_episodes(instance_id: str, n: int = 200) -> list[dict]:
    try:
        return await db.load_recent_episodes(instance_id, n)
    except Exception:
        return []


async def what_do_i_know(
    instance_id: str | None,
    query: str,
    *,
    config=None,
    limit: int = 10,
) -> dict:
    """
    The flagship "answer from memory" primitive: given a term ("sezzle", "my
    sister"), return everything the grande knows about it, deterministically
    and at ``$0``.

    Returns::

        {
            "query": str,
            "facts": [Fact-as-dict, ...],       # facts mentioning the term
            "episodes": [row, ...],             # episodes mentioning it (FTS5 recall)
            "related_terms": [str, ...],        # co-occurring entities, most common first
            "first_seen": str | None,           # earliest creado_en across facts+episodes
            "last_seen": str | None,            # latest creado_en across facts+episodes
        }

    An empty/blank query, or any internal failure, degrades to an empty
    result; this never raises and never calls a model.
    """
    empty = {
        "query": query or "",
        "facts": [],
        "episodes": [],
        "related_terms": [],
        "first_seen": None,
        "last_seen": None,
    }
    query = (query or "").strip()
    if not query:
        return empty

    try:
        instance_id = _resolve_instance(instance_id, config)

        all_facts = await _all_facts(instance_id)
        matched_facts = [f for f in all_facts if _matches(query, _fact_terms(f))]

        try:
            episodes = await db.search_episodes(instance_id, query, limit)
        except Exception:
            episodes = []

        related = Counter()
        for f in matched_facts:
            for term in _fact_terms(f):
                if not _matches(query, {term}):
                    related[term] += 1
        # Episode snippets are shorter/noisier; still worth a co-occurrence pass.
        for ep in episodes:
            for term in _extract_terms(_episode_text(ep)):
                if not _matches(query, {term}):
                    related[term] += 1

        timestamps = [f.creado_en for f in matched_facts if f.creado_en]
        timestamps += [ep.get("creado_en") for ep in episodes if ep.get("creado_en")]
        timestamps = sorted(t for t in timestamps if t)

        return {
            "query": query,
            "facts": [
                {
                    "id": f.id,
                    "tipo": f.tipo,
                    "contenido": f.contenido,
                    "tags": f.tags,
                    "pinned": f.pinned,
                    "confianza": f.confianza,
                    "creado_en": f.creado_en,
                }
                for f in matched_facts[:limit]
            ],
            "episodes": episodes[:limit],
            "related_terms": [term for term, _ in related.most_common(limit)],
            "first_seen": timestamps[0] if timestamps else None,
            "last_seen": timestamps[-1] if timestamps else None,
        }
    except Exception:
        return empty


async def entities(instance_id: str | None = None, *, config=None) -> list[dict]:
    """
    The distinct entities the brain tracks; a map of what it knows about.

    Each entry: ``{"term": str, "mentions": int, "last_seen": str, "source":
    "tag" | "text"}``. Built from fact tags (source ``"tag"``) and recurring
    capitalized/quoted terms across facts + recent episodes (source
    ``"text"``, only kept if mentioned 2+ times; a single stray capitalized
    word is noise, not an entity worth surfacing). Sorted by mention count
    descending, then most-recently-seen. Never raises; degrades to ``[]``.
    """
    try:
        instance_id = _resolve_instance(instance_id, config)
        all_facts = await _all_facts(instance_id)
        episodes = await _recent_episodes(instance_id)

        mentions: Counter = Counter()
        last_seen: dict[str, str] = {}
        sources: dict[str, str] = {}

        def _bump(term: str, when: str, source: str) -> None:
            mentions[term] += 1
            if when and (term not in last_seen or when > last_seen[term]):
                last_seen[term] = when
            # A tag is the stronger signal; once tagged, stays tagged.
            if sources.get(term) != "tag":
                sources[term] = source

        for f in all_facts:
            for tag in (f.tags or []):
                tag = tag.strip()
                if tag:
                    _bump(tag, f.creado_en, "tag")
            for term in _extract_terms(f.contenido):
                _bump(term, f.creado_en, "text")

        for ep in episodes:
            for term in _episode_terms(ep):
                _bump(term, ep.get("creado_en") or "", "text")

        out = []
        for term, count in mentions.items():
            if sources.get(term) == "text" and count < 2:
                continue  # a single stray capitalized word is noise, not an entity
            out.append(
                {
                    "term": term,
                    "mentions": count,
                    "last_seen": last_seen.get(term) or "",
                    "source": sources.get(term, "text"),
                }
            )
        # Stable multi-key sort: last_seen (desc) breaks ties within the same
        # mention count, then mentions (desc) is the primary key.
        out.sort(key=lambda e: e["last_seen"], reverse=True)
        out.sort(key=lambda e: e["mentions"], reverse=True)
        return out
    except Exception:
        return []


async def timeline(instance_id: str | None, entity: str, *, config=None) -> list[dict]:
    """
    The story of one thing: every fact and episode mentioning ``entity``, in
    chronological order (oldest first).

    Each entry: ``{"kind": "fact" | "episode", "creado_en": str, "summary":
    str, "raw": dict}``. An empty/unknown entity degrades to ``[]``, never
    raises.
    """
    entity = (entity or "").strip()
    if not entity:
        return []
    try:
        instance_id = _resolve_instance(instance_id, config)
        all_facts = await _all_facts(instance_id)
        matched_facts = [f for f in all_facts if _matches(entity, _fact_terms(f))]

        episodes = await _recent_episodes(instance_id)
        matched_episodes = [ep for ep in episodes if _matches(entity, _episode_terms(ep))]
        # Also fold in FTS5 recall hits; they may reach further back than the
        # bounded "recent" window above.
        try:
            recall_hits = await db.search_episodes(instance_id, entity, 50)
        except Exception:
            recall_hits = []
        seen_ids = {ep.get("id") for ep in matched_episodes}
        for ep in recall_hits:
            if ep.get("id") not in seen_ids:
                matched_episodes.append(ep)
                seen_ids.add(ep.get("id"))

        items = [
            {
                "kind": "fact",
                "creado_en": f.creado_en,
                "summary": f.contenido,
                "raw": {
                    "id": f.id,
                    "tipo": f.tipo,
                    "contenido": f.contenido,
                    "tags": f.tags,
                    "pinned": f.pinned,
                    "confianza": f.confianza,
                    "creado_en": f.creado_en,
                },
            }
            for f in matched_facts
        ] + [
            {
                "kind": "episode",
                "creado_en": ep.get("creado_en") or "",
                "summary": ep.get("resultado") or ep.get("decision") or "",
                "raw": ep,
            }
            for ep in matched_episodes
        ]
        items.sort(key=lambda it: it["creado_en"] or "")
        return items
    except Exception:
        return []


# ── public extraction aliases (reused by memory/kg.py; behavior unchanged) ──
#
# The persisted knowledge graph (Wave 3, memory/kg.py) feeds off the SAME
# deterministic extraction this read-model already uses, so entity terms are
# consistent across both. Exposed here rather than duplicated.
extract_terms = _extract_terms
episode_text = _episode_text
episode_terms = _episode_terms
