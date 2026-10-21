# SPDX-License-Identifier: AGPL-3.0-or-later
"""Multi-machine: check-in (not push) + report routes (§2.5).

The brain side of the "check-in, not push" loop. A thin local agent
(lhassa.remote.local_agent) on each machine POSTs /checkin with what it can do
and its current state; the brain reconciles real-vs-desired, mints + signs the
Tasks it must run, and hands them back. The machine runs each one ONLY after its
own allowlist gate (§2.6) accepts it, then POSTs the outcome to /report.

Mounted by the integrator with ``app.include_router(checkin_routes.router)``.
"""

from fastapi import APIRouter, HTTPException

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.episode import Episode
from lhassa.core.task import Task
from lhassa.security import signing
from lhassa.security.trust import Origin, TrustLevel

router = APIRouter()


def _origen_brain() -> Origin:
    """Phase-0 origin for a brain-minted Task: INTERNO trust, actor 'brain'."""
    return Origin(trust=TrustLevel.INTERNO, actor_id="brain")


def _task_to_json(task: Task) -> dict:
    """Serialize a Task for the wire, carrying both the human-readable ``origen``
    and the flat origin fields so the machine can rebuild it (``Task.from_db_row``)
    and re-derive ``canonical_bytes`` byte-for-byte to verify ``firma``."""
    return {
        "id": task.id,
        "instance_id": task.instance_id,
        "accion": task.accion,
        "objetivo": task.objetivo,
        "params": task.params,
        "origen": {"trust": int(task.origen.trust), "actor_id": task.origen.actor_id},
        "origen_trust": int(task.origen.trust),
        "origen_actor": task.origen.actor_id,
        "estado": task.estado,
        "firma": task.firma,
        "creado_en": task.creado_en,
    }


@router.post("/checkin")
async def checkin(payload: dict):
    """A machine checks in. Reconcile desired-vs-current, mint + sign the Tasks it
    must run, persist the new ones, record a check-in Episode, and return them.

    Body: ``{machine_id, capabilities: list[str], current_state: dict}``.
    Returns ``{"tasks": [task_as_json, ...]}``: each task carries its ``firma``
    and an ``origen`` (Phase 0: INTERNO, actor 'brain').
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="check-in payload must be a JSON object.")
    machine_id = (payload.get("machine_id") or "").strip()
    if not machine_id:
        raise HTTPException(status_code=400, detail="machine_id is required.")
    current_state = payload.get("current_state") or {}
    if not isinstance(current_state, dict):
        raise HTTPException(status_code=400, detail="current_state must be a JSON object.")

    instance_id = Config.instance_id
    await db.init_db()

    emitted: list[Task] = []
    new_count = 0

    # Pre-existing pending tasks for this instance (snapshot BEFORE we synthesize,
    # so reconciliation's freshly-persisted tasks aren't double-counted). Phase 0
    # keeps it simple; all pending tasks; a machine-targeting field comes later.
    for row in await db.load_pending_tasks(instance_id):
        task = Task.from_db_row(row)
        if not task.firma:
            task.firma = signing.sign_task(task)
        emitted.append(task)

    # Reconcile: a desired key whose value differs from current_state becomes a
    # signed sync_repo Task (Phase 0: desired_state holds repos; git pull is the
    # reconciliation step, §2.5).
    desired = await db.get_desired_state(instance_id, machine_id)
    for clave, valor in desired.items():
        if str(current_state.get(clave)) != str(valor):
            task = Task(
                id=db._new_id("t"),
                instance_id=instance_id,
                accion="sync_repo",
                objetivo=clave,
                params={
                    "repo_dir": clave,
                    "desired": valor,
                    "current": current_state.get(clave),
                },
                origen=_origen_brain(),
                estado="pendiente",
                creado_en=db._now(),
            )
            task.firma = signing.sign_task(task)
            await db.add_task(**task.to_db_kwargs())
            emitted.append(task)
            new_count += 1

    # Observability: explain what the check-in emitted and why.
    ep = Episode.begin(despertar_por=f"checkin:{machine_id}", instance_id=instance_id)
    ep.finish(
        decision="checkin",
        accion="checkin",
        resultado=(
            f"machine {machine_id} checked in; emitted {len(emitted)} task(s) "
            f"({new_count} new from reconciliation)"
        ),
    )
    await ep.append()

    return {"tasks": [_task_to_json(t) for t in emitted]}


# Outcomes that mean the task succeeded; everything else is a failure (with
# "rechazada" kept distinct: gate-denied, never executed).
_OK_OUTCOMES = {"hecha", "ok", "done", "success", "exitosa"}


@router.post("/report")
async def report(payload: dict):
    """A machine reports a task's outcome. Set its state, append an outcome
    Episode. Body: ``{task_id, outcome, detail}``. Returns ``{"ok": true}``.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="report payload must be a JSON object.")
    task_id = (payload.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required.")
    outcome = (payload.get("outcome") or "").strip()
    detail = payload.get("detail") or ""

    instance_id = Config.instance_id
    await db.init_db()

    if outcome == "rechazada":
        estado = "rechazada"
    elif outcome == "pendiente_aprobacion":
        # Parked at the approval frontier (§2.6): not executed, not failed. It
        # waits in the approvals queue for a human OK (do-and-sleep, §2.1).
        estado = "esperando_aprobacion"
    elif outcome in _OK_OUTCOMES:
        estado = "hecha"
    else:
        estado = "fallida"
    await db.set_task_state(instance_id, task_id, estado)

    ep = Episode.begin(despertar_por=f"report:{task_id}", instance_id=instance_id)
    ep.finish(
        decision="report",
        accion="report",
        resultado=f"task {task_id} → {outcome or 'sin outcome'}: {detail}",
    )
    await ep.append()

    return {"ok": True}
