# SPDX-License-Identifier: AGPL-3.0-or-later
"""Standing brains (Wave 1); a project's review brains attach AUTOMATICALLY.

Two halves of one door, mirroring ``actionpack/_projects.py``:

- :func:`attach_project_template` is the fixed ACTION (``attach_project_template``):
  given a project id (and, optionally, an explicit template id), it instantiates
  the mapped :mod:`lhassa.org.templates` org template under Lhassa and records the
  attachment in ``project_brains`` (``core/db.py``'s ``insert_project_brains``;
  ``INSERT OR IGNORE``, so re-running it is a no-op: the row itself IS the
  idempotency). $0, deterministic, never raises; errors return an honest string.
- :func:`_from_projects_without_brains` is the $0 watchlist sense: every ACTIVE
  project whose ``kind`` maps to a template (``org.templates.kind_template_map``)
  and has NO matching ``project_brains`` row yet gets one ``attach_project_template``
  task minted for it, deduped via ``watch_state`` (the ``project_brains`` row makes
  a re-mint harmless anyway; the marker just keeps the sense from minting a second
  task on every beat before the first one runs).

**Brain-id collision rule.** ``org.templates.instantiate`` namespaces every brain id
under a caller-chosen ``namespace`` (default: the template id; the plain "apply a
template" behaviour used by the CLI/API/dashboard, unchanged). This sense passes
``namespace=f"{template_id}:{project_id}"``, so two projects that both attach
``code-review`` each get their OWN reviewer/security-reviewer brains
(``code-review:<project_id>:reviewer`` etc.) instead of sharing one global pair;
per-project isolation, no collision, and each project's brains can be told apart on
the chart.

**Approval-gate placement.** Whether an attach needs a human tap depends on the
template's risk (:func:`lhassa.org.templates.template_is_read_only`); but ``org``
is a SHELL and ``security`` is an INTERFACE layer that must never import a shell,
even lazily (the architecture guard walks every import regardless of nesting; see
``tests/test_architecture.py``). So the classification is computed HERE, where the
``org`` import is legal (both ``actionpack`` and ``org`` are shells), and stamped
onto the task's own ``params`` as ``read_only``: ``security/approval.py``'s
``_attach_template_needs_gate`` reads that pre-computed flag, no cross-layer import
needed. A read-only template's attach still goes through ``INTERNO`` origin (this is
an internal reflex, not world-derived content); the payload flag is what decides
approval, fail-closed when the classification could not be computed (``read_only``
missing/not ``True`` ⇒ gated).
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

_SENSE_REGISTERED = False


async def attach_project_template(params: dict) -> str:
    """Attach a project's standing review brains. params: project_id (required),
    template_id (optional; defaults from the project's kind via
    ``org.templates.kind_template_map``). Never raises.
    """
    try:
        if not isinstance(params, dict):
            params = {}
        from lhassa.config import Config
        from lhassa.core import db, projects
        from lhassa.org import templates as org_templates

        cfg = params.get("config") or Config
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"

        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            return "attach_project_template: no project_id given; pass project_id=<id>."
        project = await projects.get_project(instance_id, project_id)
        if project is None:
            return f"attach_project_template: unknown project {project_id!r}."

        template_id = str(params.get("template_id") or "").strip()
        if not template_id:
            template_id = org_templates.kind_template_map().get(project["kind"], "")
        if not template_id:
            return (
                "attach_project_template: no default template for kind "
                f"{project['kind']!r}; pass template_id=... explicitly."
            )
        try:
            org_templates.get_template(template_id)
        except org_templates.TemplateError as exc:
            return f"attach_project_template: refused; {exc}"

        existing = await db.list_project_brains(instance_id, project_id)
        if any(row.get("template_id") == template_id for row in existing):
            return (
                f"attach_project_template: {template_id!r} already attached to "
                f"{project['name']} ({project_id}); no-op."
            )

        # Per-project namespace: isolated brains per project, never shared/collide.
        namespace = f"{template_id}:{project_id}"
        result = org_templates.instantiate(template_id, config=cfg, namespace=namespace)

        await db.insert_project_brains(
            {
                "project_id": project_id,
                "instance_id": instance_id,
                "template_id": template_id,
                "status": "attached",
                "instantiated_at": db._now(),
            }
        )
        created = len(result.get("created") or [])
        skipped = len(result.get("skipped") or [])
        return (
            f"attach_project_template: {template_id!r} attached to {project['name']} "
            f"({project_id}); {created} brain(s) created, {skipped} reused."
        )
    except Exception as exc:
        return f"attach_project_template errored: {exc}"


async def _from_projects_without_brains(
    instance_id: str, out: list, peek: bool = False
) -> None:
    """Watchlist sense: an active project of a mapped kind with no standing brains
    yet → mint one ``attach_project_template`` task for it.

    Emits no triggers (``out`` untouched): the reflex mints a task, it does not act
    directly. $0 and never raises.
    """
    if peek:
        return
    try:
        from lhassa.core import db, projects
        from lhassa.org import templates as org_templates

        kind_map = org_templates.kind_template_map()
        if not kind_map:
            return

        active = await projects.list_projects(instance_id, status="active")
        for project in active:
            template_id = kind_map.get(project.get("kind") or "")
            if not template_id:
                continue
            project_id = project["id"]

            existing = await db.list_project_brains(instance_id, project_id)
            if any(row.get("template_id") == template_id for row in existing):
                continue  # already attached; the row itself is the idempotency

            marker = f"standing_brains:{project_id}:{template_id}"
            if await db.get_watch_state(instance_id, marker):
                continue  # already minted once; never re-minted (see module docstring)

            from lhassa import actionpack
            from lhassa.core import dispatch
            from lhassa.security.trust import Origin, TrustLevel

            actionpack.register_all()  # the mint refuses unregistered actions

            read_only = org_templates.template_is_read_only(template_id)
            task_id, estado = await dispatch.mint_task(
                instance_id,
                "attach_project_template",
                objetivo=f"{project['name']} ({project.get('kind')})",
                params={
                    "project_id": project_id,
                    "template_id": template_id,
                    # Stamped here (see module docstring): the ONE risk classification
                    # computed where the org import is legal; security/approval.py
                    # reads this flag instead of importing org.templates itself.
                    "read_only": read_only,
                },
                origin=Origin(trust=TrustLevel.INTERNO, actor_id="standing-brains"),
                due_at=datetime.now(timezone.utc).isoformat(),
            )
            if task_id:
                await db.set_watch_state(instance_id, marker, f"minted:{task_id}")
                logger.info(
                    f"standing brains: proposed {template_id} for project "
                    f"{project_id} ({estado})"
                )
    except Exception as exc:  # a sense must never break the beat
        logger.warning(f"standing brains sense degraded: {exc}")


def register_sense() -> None:
    """Register the detector with the watchlist. Idempotent."""
    global _SENSE_REGISTERED
    if _SENSE_REGISTERED:
        return
    _SENSE_REGISTERED = True
    from lhassa.core.watchlist import register_check

    register_check(_from_projects_without_brains)
