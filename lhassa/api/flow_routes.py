"""FastAPI routes for work-flows — the backend for the (later) canvas frontend.

A thin shell over :mod:`lhassa.flows`: it validates request bodies and turns store /
model errors into 4xx (never a 500), so a malformed flow from the canvas is a clean
``400`` with a message, not a stack trace.

Routes (all under ``/api/flows``)::

    GET    /api/flows            → list every flow            → 200 [flow, ...]
    GET    /api/flows/{id}       → one flow                   → 200 flow | 404
    POST   /api/flows            → create from a flow dict     → 201 flow | 400 | 409
    PUT    /api/flows/{id}       → replace a flow              → 200 flow | 400
    PATCH  /api/flows/{id}       → merge fields into a flow    → 200 flow | 400 | 404
    DELETE /api/flows/{id}       → delete a flow              → 200 {deleted} | 404
    POST   /api/flows/{id}/run   → execute → the run record    → 200 run | 404

The integrator wires this in with ``app.include_router(flow_routes.router)``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from loguru import logger

import lhassa.config as lhassa_config
from lhassa.core import db
from lhassa.flows.model import Flow, FlowValidationError
from lhassa.flows.store import FlowStore
from lhassa.org.delegate import delegate_flow

router = APIRouter(prefix="/api/flows", tags=["flows"])


def _store() -> FlowStore:
    """A fresh store per request (reads ``config.flows_file`` each time)."""
    return FlowStore()


def _parse(body: Any) -> Flow:
    """Parse + validate a request body into a Flow, else raise a 400."""
    try:
        flow = Flow.from_dict(body)
        flow.validate()
    except FlowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # any malformed body → 4xx, never a 500
        raise HTTPException(status_code=400, detail=f"invalid flow: {exc}") from exc
    return flow


@router.get("")
async def list_flows() -> list[dict]:
    """Every stored flow."""
    return [f.to_dict() for f in _store().list_flows()]


@router.get("/{flow_id}")
async def get_flow(flow_id: str) -> dict:
    """One flow by id (404 if missing)."""
    flow = _store().get(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"flow {flow_id!r} not found")
    return flow.to_dict()


@router.post("", status_code=201)
async def create_flow(body: dict = Body(...)) -> dict:
    """Create a flow from a flow dict (409 if the id is taken)."""
    flow = _parse(body)
    try:
        return _store().create(flow).to_dict()
    except FlowValidationError as exc:
        # An already-taken id is a conflict, not a malformed body.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{flow_id}")
async def replace_flow(flow_id: str, body: dict = Body(...)) -> dict:
    """Replace (or create) the flow at ``flow_id`` with the given dict."""
    data = dict(body or {})
    data["id"] = flow_id  # the path id is authoritative
    flow = _parse(data)
    return _store().save(flow).to_dict()


@router.patch("/{flow_id}")
async def update_flow(flow_id: str, body: dict = Body(...)) -> dict:
    """Merge ``body`` fields into the existing flow (404 if it does not exist)."""
    store = _store()
    existing = store.get(flow_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"flow {flow_id!r} not found")
    merged = existing.to_dict()
    for key in ("name", "description", "nodes", "edges"):
        if key in (body or {}):
            merged[key] = body[key]
    merged["id"] = flow_id
    flow = _parse(merged)
    return store.save(flow).to_dict()


@router.delete("/{flow_id}")
async def delete_flow(flow_id: str) -> dict:
    """Delete a flow (404 if it does not exist)."""
    if not _store().delete(flow_id):
        raise HTTPException(status_code=404, detail=f"flow {flow_id!r} not found")
    return {"deleted": flow_id}


@router.post("/{flow_id}/run")
async def run_flow_endpoint(flow_id: str, body: dict | None = Body(default=None)) -> dict:
    """Execute a stored flow (offline/stubbed unless ``LHASSA_ORG_LIVE``).

    The optional body may carry ``{"inputs": {...}}`` to seed the trigger (a bare
    ``{"input": "<text>"}`` is accepted as a convenience and wrapped as
    ``{"summary": "<text>"}``) and an optional ``"company"`` string (the CV-export
    override — override → context → ``"untitled"``). Returns the structured run
    record from :func:`lhassa.flows.engine.run_flow`.

    The run goes through the deterministic org DELEGATION seam
    (:func:`lhassa.org.delegate.delegate_flow`): the flow flows down the command chain
    (Lhassa → owning department → flow) with an audit-trail episode at the hop, at $0
    extra. The response is the same run record (with an added ``delegation`` field).
    """
    flow = _store().get(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"flow {flow_id!r} not found")
    body_dict = body if isinstance(body, dict) else {}
    inputs = body_dict.get("inputs")
    if inputs is None and "input" in body_dict:
        # Convenience: a bare {"input": "<text>"} seeds the {input} placeholder.
        inputs = {"summary": body_dict.get("input")}
    company = body_dict.get("company")
    record = await delegate_flow(flow, inputs=inputs, company=company)
    await _persist_flow_run(record, trigger=_trigger_from_inputs(inputs))
    return record


def _trigger_from_inputs(inputs: dict | None) -> str:
    if isinstance(inputs, dict) and inputs.get("trigger"):
        return str(inputs.get("trigger"))
    return "manual"


def _short(value, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = str(value.get("summary") or value.get("output") or value)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text[:limit]


async def _persist_flow_run(record: dict, *, trigger: str = "manual") -> None:
    """Persist the flow run for analytics, best-effort and never blocking."""
    if not isinstance(record, dict):
        return
    try:
        await db.init_db()
        instance_id = lhassa_config.Config.instance_id
        run_id = record.get("id") or record.get("run_id")
        if not run_id:
            return
        await db.record_run({
            "id": run_id,
            "instance_id": instance_id,
            "kind": "flow",
            "entity_id": record.get("flow_id"),
            "status": record.get("status") or "completed",
            "trigger": trigger,
            "summary": _short(record.get("output")),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "duration_ms": record.get("duration_ms") or 0,
            "cost_usd": record.get("cost_usd") or 0.0,
            "raw": record,
        })
        for idx, step in enumerate(record.get("steps") or []):
            if not isinstance(step, dict):
                continue
            kind = step.get("kind") or ""
            entity_id = step.get("brain") if kind == "task" else step.get("node")
            result = step.get("result")
            await db.record_run_step({
                "instance_id": instance_id,
                "run_id": run_id,
                "idx": idx,
                "kind": kind,
                "entity_id": entity_id,
                "status": step.get("status") or ("completed" if step.get("ok", True) else "failed"),
                "started_at": step.get("started_at"),
                "finished_at": step.get("finished_at"),
                "duration_ms": step.get("duration_ms") or 0,
                "cost_usd": step.get("cost_usd") or (
                    result.get("cost_usd") if isinstance(result, dict) else 0.0
                ),
                "input_summary": _short(step.get("inputs") or step.get("goal")),
                "output_summary": _short(result or step.get("output") or step.get("payload")),
                "error": step.get("error"),
                "raw": step,
            })
    except Exception as exc:
        logger.debug("flows: analytics persistence skipped: {}", exc)
