# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Seed the grande with the default facts (Apéndice B) for an instance.

Two datos and two directivas, with the pinned/tags shown in the brief. Idempotent
by ``contenido``: calling it repeatedly never duplicates a seed fact (the grande
is append-only, so we only insert what is missing).
"""

from lhassa.config import Config
from lhassa.memory.facts import add_fact, load_facts

# Apéndice B — the four seed facts (tipo, contenido, pinned, tags).
SEED_FACTS = [
    ("dato", "le gusta el chocolate con menta", False, ["gustos"]),
    ("dato", "prefiere herramientas de código libre", True, ["tooling"]),
    ("directiva", "responder conciso por defecto", True, ["estilo"]),
    ("directiva", "optimizar; muy sensible a cualquier delay", True, ["rendimiento"]),
]


async def seed_default_facts(instance_id: str | None = None) -> int:
    """
    Insert any missing Apéndice B seed facts for ``instance_id``. Safe to call
    repeatedly. Returns the number of facts actually inserted this call.
    """
    instance_id = instance_id or Config.instance_id
    existing = {f.contenido for f in await load_facts(instance_id)}
    inserted = 0
    for tipo, contenido, pinned, tags in SEED_FACTS:
        if contenido in existing:
            continue
        await add_fact(
            instance_id,
            tipo,
            contenido,
            pinned=pinned,
            tags=tags,
            procedencia="seed",
        )
        inserted += 1
    return inserted
