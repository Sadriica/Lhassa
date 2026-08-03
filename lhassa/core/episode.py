# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The structured Episode — observability from the first commit (§2.10).

An Episode records *why* the brain did something: what woke it, what memory it
loaded, the options it weighed, the decision and action it took, the result and
its cost. Episodes are a *view* over the grande (the append-only log), not a
separate system; they are written once and never mutated.

Lifecycle:
    ep = Episode.begin(despertar_por="pc_reporto", instance_id=cfg.instance_id)
    ...  # do the work
    ep.finish(decision="sync_repo", accion="git pull", resultado="ff a def456")
    await ep.append()
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from lhassa.core import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Episode:
    id: str
    instance_id: str
    despertar_por: str
    memoria_cargada: list[str] = field(default_factory=list)
    opciones: list[str] = field(default_factory=list)
    decision: str = ""
    accion: str = ""
    resultado: str = ""
    costo_usd: float = 0.0
    raw: str = ""
    creado_en: str = ""

    @classmethod
    def begin(cls, despertar_por: str, instance_id: str) -> "Episode":
        """Open an episode for a trigger, stamping a fresh id and UTC timestamp."""
        return cls(
            id=db._new_id("ep"),
            instance_id=instance_id,
            despertar_por=despertar_por,
            creado_en=_now(),
        )

    def finish(
        self,
        *,
        decision: str = "",
        accion: str = "",
        resultado: str = "",
        costo_usd: float = 0.0,
    ) -> "Episode":
        """Fill in the outcome of the episode. Returns self for chaining."""
        self.decision = decision
        self.accion = accion
        self.resultado = resultado
        self.costo_usd = costo_usd
        return self

    async def append(self) -> str:
        """Persist this episode to the grande (append-only). Returns its id."""
        return await db.append_episode(self)
