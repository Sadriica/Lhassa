# SPDX-License-Identifier: AGPL-3.0-or-later
"""The commons; the ONE read-only cross-cutting source of truth (Fase 7).

Policies, the org chart, shared data: one place every brain reads, that only the
**general** (the sole curator) may write. There are no per-department copies that
could drift; there is a single shared row per key (the brief: "una sola fuente de
verdad transversal; común de solo-lectura, no memorias que se sincronizan").

  - ``get`` / ``get_all``: readable by ALL brains (no role gate on reads).
  - ``set``: curator-only: it enforces, through :mod:`lhassa.org.rbac`, that the
    writer's role is ``general``. A department write raises ``PermissionError``.

Backed by the instance-scoped ``org_commons`` table.
"""

from typing import Any, Optional

from lhassa.config import Config
from lhassa.core import db
from lhassa.org import rbac


async def get(key: str, *, instance_id: str = Config.instance_id) -> Optional[Any]:
    """Read one commons value (decoded), or ``None`` if absent. Open to all brains."""
    row = await db.get_commons(instance_id, key)
    return row["valor"] if row is not None else None


async def get_all(*, instance_id: str = Config.instance_id) -> dict[str, Any]:
    """Read the whole commons as ``{key: value}`` (values decoded). Open to all brains."""
    return await db.get_all_commons(instance_id)


async def set(
    key: str,
    value: Any,
    *,
    by_role: str,
    by_actor: str,
    instance_id: str = Config.instance_id,
) -> None:
    """Write/update one commons entry. CURATOR ONLY (the general).

    Enforces, via RBAC, that ``by_role`` may write the commons; only ``general``
    holds that permission, so a department write raises ``PermissionError`` and
    nothing is persisted. ``by_actor`` is recorded as the curator (audit trail).
    A single shared row is upserted; never a per-department copy.
    """
    rbac.require(by_role, "write", "commons")
    await db.set_commons(instance_id, key, value, curator=by_actor)
