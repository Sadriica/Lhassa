# SPDX-License-Identifier: AGPL-3.0-or-later
"""Org MANAGEMENT routes; create brains, skills, limits, hierarchy (additive).

The backend the dashboard org panel drives: read the chart + available skills,
create/update/remove brains, re-parent ("who commands whom"), and promote to root
("who commands everyone"), plus the use-case templates catalog. All instance-scoped
and DEFENSIVE; a bad body / invalid op returns a clear 4xx, never a 500, and never
corrupts the topology file (management validates BEFORE it persists).

The typed RELATIONSHIPS overlay (``/api/org/relationships*``) lives in its own
module (:mod:`lhassa.api.relationship_routes`), NOT here.

Mounted by the integrator with ``app.include_router(org_routes.router)``.
"""

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


def _org_4xx(exc: Exception) -> HTTPException:
    """Map a management/topology error to a 4xx (404 for a missing brain, else 400)."""
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'\""))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/org/topology")
async def get_org_topology():
    """The full org chart: nodes + hierarchy + skills + limits (``describe``)."""
    from lhassa.org import management

    return management.describe()


@router.get("/api/org/skills")
async def get_org_skills():
    """The skills that CAN be assigned; the specialist registry's domains."""
    from lhassa.org import management

    return management.available_skills()


@router.get("/api/org/route")
async def get_org_route(goal: str = Query(..., min_length=1)):
    """Preview the root→leaf routing path a goal would take (read-only, no LLM)."""
    from lhassa.org import topology as topo
    from lhassa.org.coordinator import route_in_topology

    topology = topo.load_topology()
    node = topology.root()
    path = [node.id]
    while not topology.is_leaf(node.id):
        child_id = route_in_topology(topology, node.id, goal)
        path.append(child_id)
        node = topology.node(child_id)
    leaf = topology.node(path[-1])
    return {
        "goal": goal,
        "path": path,
        "leaf": leaf.id,
        "department": leaf.department or leaf.id,
    }


@router.post("/api/org/brains")
async def create_org_brain(payload: dict):
    """Create a brain. Body: ``{id, role?, parent?, department?, skills?, limits?}``.

    Returns the updated chart (``describe``). A duplicate id / missing parent /
    cycle returns 400 with a reason; the file is never partially written.
    """
    from lhassa.org import management

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="brain payload must be a JSON object.")
    node_id = (payload.get("id") or "").strip()
    if not node_id:
        raise HTTPException(status_code=400, detail="'id' is required.")
    skills = payload.get("skills") or ()
    if not isinstance(skills, (list, tuple)):
        raise HTTPException(status_code=400, detail="'skills' must be an array.")
    limits = payload.get("limits") or {}
    if not isinstance(limits, dict):
        raise HTTPException(status_code=400, detail="'limits' must be an object.")
    try:
        management.add_brain(
            node_id,
            role=payload.get("role") or "department",
            parent=payload.get("parent") or None,
            department=payload.get("department") or None,
            skills=list(skills),
            limits=limits,
        )
    except Exception as exc:  # ManagementError/TopologyError (ValueError) → 4xx
        raise _org_4xx(exc) from exc
    return management.describe()


@router.patch("/api/org/brains/{brain_id}")
async def update_org_brain(brain_id: str, payload: dict):
    """Update a brain. Body (all optional): ``{skills?, limits?, role?, department?}``.

    A ``None``/absent field is left unchanged. Returns the updated chart.
    """
    from lhassa.org import management

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="update payload must be a JSON object.")
    skills = payload.get("skills")
    if skills is not None and not isinstance(skills, (list, tuple)):
        raise HTTPException(status_code=400, detail="'skills' must be an array.")
    limits = payload.get("limits")
    if limits is not None and not isinstance(limits, dict):
        raise HTTPException(status_code=400, detail="'limits' must be an object.")
    try:
        management.update_brain(
            brain_id,
            skills=list(skills) if skills is not None else None,
            limits=limits,
            role=payload.get("role"),
            department=payload.get("department"),
        )
    except Exception as exc:
        raise _org_4xx(exc) from exc
    return management.describe()


@router.delete("/api/org/brains/{brain_id}")
async def delete_org_brain(brain_id: str, force: bool = Query(default=False)):
    """Remove a brain (``?force=true`` removes its whole subtree). Returns the chart."""
    from lhassa.org import management

    try:
        management.remove_brain(brain_id, force=force)
    except Exception as exc:
        raise _org_4xx(exc) from exc
    return management.describe()


@router.post("/api/org/brains/{brain_id}/move")
async def move_org_brain(brain_id: str, payload: dict):
    """Re-parent a brain (who commands whom). Body: ``{parent}``. Returns the chart."""
    from lhassa.org import management

    if not isinstance(payload, dict) or not (payload.get("parent") or "").strip():
        raise HTTPException(status_code=400, detail="'parent' is required.")
    try:
        management.set_parent(brain_id, payload["parent"].strip())
    except Exception as exc:
        raise _org_4xx(exc) from exc
    return management.describe()


@router.post("/api/org/brains/{brain_id}/promote")
async def promote_org_brain(brain_id: str):
    """Promote a brain to root (it commands everyone). Returns the updated chart."""
    from lhassa.org import management

    try:
        management.set_root(brain_id)
    except Exception as exc:
        raise _org_4xx(exc) from exc
    return management.describe()


@router.get("/api/org/templates")
async def get_org_templates():
    """The use-case org templates catalog (Code Review, Software Factory, …)."""
    from lhassa.org import templates

    return templates.list_templates()


@router.post("/api/org/templates/{template_id}/apply")
async def apply_org_template(template_id: str):
    """Apply a template; create its brains under Lhassa. Returns the updated chart."""
    from lhassa.org import templates

    try:
        return templates.instantiate(template_id)
    except Exception as exc:  # TemplateError/ManagementError (ValueError) → 4xx
        raise _org_4xx(exc) from exc
