"""Artifacts — the durable record of a delivered WORK PRODUCT (§org: "Lhassa is
the sender of record").

A leaf brain in the hierarchy doesn't just *think*, it sometimes *produces* — a
tailored CV (the ``.tex`` source alongside the compiled ``.pdf``), a report, any
file the owner ends up receiving. This module is the thin, append-only CRUD over
the ``artifacts`` table (schema + raw row helpers live in :mod:`lhassa.core.db`,
same pattern as ``lhassa.memory.facts`` over ``facts``): one row per artifact,
its files as a JSON list of paths (so a CV's ``.tex`` and its compiled ``.pdf``
travel together), a free-form JSON ``metadata`` dict, the ``origin_department``
that produced it, and an optional ``episode_id`` linking back to the hop that
delivered it.

Append-only: registering an artifact never overwrites a prior one — a
re-delivery (or a revision) is simply a new row. Imports **only**
:mod:`lhassa.core.db` — no shells, so no architecture-guard exception is needed
(this stays in the ``core`` spine layer).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from lhassa.core import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def register(
    instance_id: str,
    *,
    kind: str,
    paths: list,
    metadata: Optional[dict] = None,
    origin_department: str = "",
    episode_id: Optional[str] = None,
) -> str:
    """Register one artifact in the grande. Returns its id (``art_...``).

    ``paths`` is a list of file paths (strings or :class:`~pathlib.Path`,
    stringified here) that belong together — e.g. a CV's ``.tex`` source AND its
    compiled ``.pdf``. ``metadata`` is a free-form dict (e.g. ``{"company": ...,
    "role": ..., "run_id": ...}``). Append-only: this always inserts a NEW row.
    """
    artifact_id = db._new_id("art")
    await db.init_db()
    await db.insert_artifact(
        {
            "id": artifact_id,
            "instance_id": instance_id,
            "kind": kind,
            "paths": [str(p) for p in (paths or [])],
            "metadata": dict(metadata or {}),
            "origin_department": origin_department,
            "episode_id": episode_id,
            "creado_en": _now(),
        }
    )
    return artifact_id


async def get(instance_id: str, artifact_id: str) -> Optional[dict]:
    """Return one artifact (paths/metadata decoded), or ``None`` if absent."""
    return await db.get_artifact(instance_id, artifact_id)


async def list_artifacts(instance_id: str, limit: int = 50) -> list[dict]:
    """Return artifacts for ``instance_id``, newest first (cap ``limit``)."""
    return await db.list_artifacts(instance_id, limit=limit)
