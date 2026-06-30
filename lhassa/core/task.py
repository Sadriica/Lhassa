"""The signed ``Task`` — the unit of work the brain hands a machine (§2.5).

A ``Task`` is reconciliation made concrete: the brain mints it, signs it
(Ed25519, ``security.signing``), and the machine runs it only after its
allowlist gate (``security.allowlist``) accepts it. The signature binds the
*content* of the task; ``canonical_bytes()`` defines exactly which bytes are
signed and verified so the brain and the machine agree byte-for-byte.

Bridging ``core.db``: the DB stores the origin flat (``origen_trust`` +
``origen_actor``) per Apéndice A. ``to_db_kwargs()`` / ``from_db_row()`` flatten
and rebuild the ``Origin`` so this dataclass and the table stay in sync.
"""

import json
from dataclasses import dataclass, field

from lhassa.security.trust import Origin, TrustLevel


@dataclass
class Task:
    """A typed, signed work item (§2.5).

    ``accion`` is a CLOSED set ("sync_repo", ...). ``origen`` carries trust + the
    WHO. ``firma`` is the Ed25519 signature over ``canonical_bytes()`` and is the
    ONLY field not itself covered by the signature.
    """

    id: str
    instance_id: str
    accion: str
    objetivo: str
    params: dict = field(default_factory=dict)
    origen: Origin = field(default_factory=lambda: Origin(TrustLevel.INTERNO))
    estado: str = "pendiente"
    firma: str = ""
    creado_en: str = ""

    def canonical_bytes(self) -> bytes:
        """Deterministic bytes of the SIGNABLE fields (everything except ``firma``).

        These are the exact bytes that get signed and verified: a JSON object
        with sorted keys, compact separators, UTF-8, and the origin flattened the
        same way the DB stores it. Any change here must be matched on both the
        signing and verifying side or signatures will stop validating.
        """
        payload = {
            "id": self.id,
            "instance_id": self.instance_id,
            "accion": self.accion,
            "objetivo": self.objetivo,
            "params": self.params,
            "origen_trust": int(self.origen.trust),
            "origen_actor": self.origen.actor_id,
            "estado": self.estado,
            "creado_en": self.creado_en,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def to_db_kwargs(self) -> dict:
        """Flatten into keyword args for ``core.db.add_task`` (origin → two cols)."""
        return {
            "task_id": self.id,
            "instance_id": self.instance_id,
            "accion": self.accion,
            "objetivo": self.objetivo,
            "params": self.params,
            "origen_trust": int(self.origen.trust),
            "origen_actor": self.origen.actor_id,
            "estado": self.estado,
            "firma": self.firma,
        }

    @classmethod
    def from_db_row(cls, row: dict) -> "Task":
        """Rebuild a ``Task`` from a ``core.db`` row (rebuilds ``Origin``).

        ``params`` may already be decoded (``load_pending_tasks`` does) or a raw
        JSON string; both are handled.
        """
        params = row.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (ValueError, TypeError):
                params = {}
        trust_raw = row.get("origen_trust")
        trust = TrustLevel(trust_raw) if trust_raw is not None else TrustLevel.MUNDO
        return cls(
            id=row["id"],
            instance_id=row["instance_id"],
            accion=row["accion"],
            objetivo=row.get("objetivo") or "",
            params=params,
            origen=Origin(trust=trust, actor_id=row.get("origen_actor")),
            estado=row.get("estado") or "pendiente",
            firma=row.get("firma") or "",
            creado_en=row.get("creado_en") or "",
        )
