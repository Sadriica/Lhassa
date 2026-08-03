# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Typed facts in the grande — the durable "hechos" that pass through the owner (§2.3).

Two flavors:
  - ``dato``      — a durable fact about the world/owner ("le gusta el chocolate
                    con menta"). Changes WHAT the brain knows.
  - ``directiva`` — a standing instruction ("responder conciso por defecto").
                    Changes the HOW; loaded always as identity.

Storage discipline (la grande, Apéndice A): facts are **append-only**. A fact is
never destructively overwritten; superseding a fact means inserting a NEW row
(newer ``actualizado_en``, possibly higher ``confianza``). Reading back a single
"current" view is the job of :func:`resolve`, which collapses contradictions.

Every fact carries ``instance_id`` and every query filters by it (isolation from
day one). This module is the high-level API over the low-level row helpers in
``lhassa.core.db``.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from lhassa.config import Config
from lhassa.core import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Fact:
    """One typed fact (Apéndice A row, hydrated)."""

    id: str
    instance_id: str
    tipo: str  # "dato" | "directiva"
    contenido: str
    pinned: bool = False
    tags: list[str] = field(default_factory=list)
    procedencia: str = ""
    confianza: float = 1.0
    creado_en: str = ""
    actualizado_en: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "Fact":
        """Build a Fact from a decoded db row (tags already a list)."""
        return cls(
            id=row["id"],
            instance_id=row["instance_id"],
            tipo=row["tipo"],
            contenido=row["contenido"],
            pinned=bool(row.get("pinned")),
            tags=list(row.get("tags") or []),
            procedencia=row.get("procedencia") or "",
            confianza=float(row.get("confianza", 1.0)),
            creado_en=row.get("creado_en") or "",
            actualizado_en=row.get("actualizado_en") or "",
        )


def _resolve_instance(instance_id: str | None) -> str:
    return instance_id or Config.instance_id


async def add_fact(
    instance_id: str | None,
    tipo: str,
    contenido: str,
    *,
    pinned: bool = False,
    tags=(),
    procedencia: str = "",
    confianza: float = 1.0,
) -> Fact:
    """
    Append a new fact to the grande and return it. Append-only: this always
    inserts a fresh row (to supersede an earlier fact, add a new one with a newer
    timestamp / higher confianza — :func:`resolve` will pick the winner).
    """
    if tipo not in ("dato", "directiva"):
        raise ValueError(f"tipo must be 'dato' or 'directiva', got {tipo!r}")
    instance_id = _resolve_instance(instance_id)
    now = _now()
    fact = Fact(
        id=db._new_id("f"),
        instance_id=instance_id,
        tipo=tipo,
        contenido=contenido,
        pinned=pinned,
        tags=list(tags or []),
        procedencia=procedencia,
        confianza=float(confianza),
        creado_en=now,
        actualizado_en=now,
    )
    await db.insert_fact(
        {
            "id": fact.id,
            "instance_id": fact.instance_id,
            "tipo": fact.tipo,
            "contenido": fact.contenido,
            "pinned": fact.pinned,
            "tags": fact.tags,
            "procedencia": fact.procedencia,
            "confianza": fact.confianza,
            "creado_en": fact.creado_en,
            "actualizado_en": fact.actualizado_en,
        }
    )
    # Embed on write — best-effort; any embedder failure NEVER breaks the fact write.
    # The fact is already committed to the DB; embedding is a best-effort bonus.
    try:
        from lhassa.memory.embeddings import get_embedder
        embedder = get_embedder(Config)
        vecs = embedder.embed([fact.contenido])
        await db.upsert_fact_embedding(instance_id, fact.id, vecs[0], model=embedder.name)
    except Exception:
        pass  # embedding failure is silent — the fact was already written
    return fact


async def load_facts(
    instance_id: str | None = None,
    *,
    tipo: str | None = None,
    pinned: bool | None = None,
) -> list[Fact]:
    """Load facts for an instance, optionally filtered by tipo / pinned."""
    instance_id = _resolve_instance(instance_id)
    rows = await db.query_facts(instance_id, tipo=tipo, pinned=pinned)
    return [Fact.from_row(r) for r in rows]


async def load_by_tags(instance_id: str | None, tags) -> list[Fact]:
    """Load facts whose tags intersect ``tags`` (exact membership, NOT semantic)."""
    instance_id = _resolve_instance(instance_id)
    rows = await db.query_facts_by_tags(instance_id, list(tags or []))
    return [Fact.from_row(r) for r in rows]


def _conflict_key(fact: Fact) -> str:
    """
    The conflict key: two facts contradict when this matches. We start simple —
    the normalized (stripped, case-folded) ``contenido`` — so superseding facts
    are written verbatim with the same subject. (Tag-keyed / subject-extracted
    conflicts are a Phase-2 refinement.)
    """
    return fact.contenido.strip().casefold()


def resolve(facts: list[Fact]) -> list[Fact]:
    """
    Collapse contradictions to one winner per conflict key.

    Conflict key: normalized exact ``contenido`` (see :func:`_conflict_key`).
    Winner: the **most recent AND highest confianza** — we rank conflicting facts
    by ``(actualizado_en, confianza)`` and keep the maximum (recency leads, with
    confianza as the tiebreaker), per the brief's contradiction rule.

    Order is preserved by first appearance of each conflict key, so the result is
    deterministic.
    """
    winners: dict[str, Fact] = {}
    order: list[str] = []
    for fact in facts:
        key = _conflict_key(fact)
        if key not in winners:
            winners[key] = fact
            order.append(key)
            continue
        incumbent = winners[key]
        challenger_rank = (fact.actualizado_en, fact.confianza)
        incumbent_rank = (incumbent.actualizado_en, incumbent.confianza)
        if challenger_rank > incumbent_rank:
            winners[key] = fact
    return [winners[key] for key in order]
