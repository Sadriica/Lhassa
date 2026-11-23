# SPDX-License-Identifier: AGPL-3.0-or-later
"""create_project; the deterministic action + the $0 project-detector sense.

Two halves of one door:

- :func:`create_project_action` is the fixed ACTION (``create_project``):
  the owner's explicit alta via CLI/chat-marker/approval resolves through it.
  $0, deterministic, never raises; errors return an honest string.
- :func:`register_sense` registers a watchlist check that notices a repo listed
  in ``Config.auto_sync_repos`` with NO matching project row and PROPOSES the
  project: it mints a ``create_project`` task with a **MUNDO** origin; the
  trust model itself parks the approval ("world-derived content is never
  trusted to act on its own"), so detection can never silently create anything.
  De-duplicated per path via ``watch_state``; a denial is respected the same
  way (the marker stays set).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_SENSE_REGISTERED = False


async def create_project_action(params: dict) -> str:
    """Create a project. params: name (required unless path given), kind, path.

    A missing name derives from the path's basename. Never raises.
    """
    try:
        if not isinstance(params, dict):
            params = {}
        from lhassa.config import Config
        from lhassa.core import projects

        cfg = params.get("config") or Config
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        path = str(params.get("path") or "").strip() or None
        name = str(params.get("name") or "").strip()
        if not name and path:
            name = Path(path).name
        if not name:
            return "create_project: no name given; pass name=<name> or path=<dir>."
        kind = str(params.get("kind") or "generic").strip() or "generic"

        row = await projects.create_project(
            instance_id, name, kind=kind, path=path,
        )
        where = f" at {row['path']}" if row.get("path") else ""
        return f"create_project: {row['name']} ({row['kind']}) created{where}; id {row['id']}"
    except ValueError as exc:
        return f"create_project: refused; {exc}"
    except Exception as exc:
        return f"create_project errored: {exc}"


async def _from_unregistered_repos(
    instance_id: str, out: list, peek: bool = False
) -> None:
    """Watchlist sense: a configured repo with no project row → PROPOSE one.

    Emits no triggers (``out`` untouched): the proposal is a minted
    ``create_project`` task whose MUNDO origin parks it at the approval
    frontier; the owner's tap is the creation. $0 and never raises.
    """
    if peek:
        return
    try:
        from lhassa.config import Config
        from lhassa.core import db, projects

        raw = (getattr(Config, "auto_sync_repos", "") or "").strip()
        if not raw:
            return
        for entry in raw.split(","):
            path = entry.strip()
            if not path:
                continue
            marker = f"project_sense:{path}"
            if await db.get_watch_state(instance_id, marker):
                continue
            resolved = await projects.resolve_project(instance_id, path)
            if resolved is not None:
                await db.set_watch_state(instance_id, marker, "known")
                continue

            from datetime import datetime, timezone

            from lhassa import actionpack
            from lhassa.core import dispatch
            from lhassa.security.trust import Origin, TrustLevel

            actionpack.register_all()  # the mint refuses unregistered actions

            task_id, estado = await dispatch.mint_task(
                instance_id,
                "create_project",
                objetivo=path,
                params={"path": path, "kind": "code", "name": Path(path).name},
                origin=Origin(trust=TrustLevel.MUNDO, actor_id="project-sense"),
                due_at=datetime.now(timezone.utc).isoformat(),
            )
            if task_id:
                await db.set_watch_state(instance_id, marker, f"proposed:{task_id}")
                logger.info(f"project sense: proposed project for {path} ({estado})")
    except Exception as exc:  # a sense must never break the beat
        logger.warning(f"project sense degraded: {exc}")


def register_sense() -> None:
    """Register the detector with the watchlist. Idempotent."""
    global _SENSE_REGISTERED
    if _SENSE_REGISTERED:
        return
    _SENSE_REGISTERED = True
    from lhassa.core.watchlist import register_check

    register_check(_from_unregistered_repos)
