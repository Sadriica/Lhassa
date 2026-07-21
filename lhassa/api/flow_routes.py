"""FastAPI routes for work-flows — the backend for the (later) canvas frontend.

A thin shell over :mod:`lhassa.flows`: it validates request bodies and turns store /
model errors into 4xx (never a 500), so a malformed flow from the canvas is a clean
``400`` with a message, not a stack trace.

Routes (all under ``/api/flows``)::

    GET    /api/flows                        → list every flow          → 200 [flow, ...]
    GET    /api/flows/templates              → the flow-templates gallery → 200 [template, ...]
    POST   /api/flows/templates/{name}/apply → instantiate a template   → 201 flow | 400
    GET    /api/flows/{id}                   → one flow                 → 200 flow | 404
    POST   /api/flows                        → create from a flow dict   → 201 flow | 400 | 409
    PUT    /api/flows/{id}                   → replace a flow            → 200 flow | 400
    PATCH  /api/flows/{id}                   → merge fields into a flow  → 200 flow | 400 | 404
    DELETE /api/flows/{id}                   → delete a flow            → 200 {deleted} | 404
    POST   /api/flows/{id}/run               → execute → the run record  → 200 run | 404

NOTE on ordering: the literal-path ``/api/flows/templates`` routes are declared
BEFORE ``/api/flows/{flow_id}`` so FastAPI/Starlette (which matches routes in
declaration order) resolves ``GET /api/flows/templates`` to the gallery, never to
``get_flow(flow_id="templates")``.

The integrator wires this in with ``app.include_router(flow_routes.router)``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from lhassa.flows import templates as flow_templates
from lhassa.flows.model import Flow, FlowValidationError
from lhassa.flows.projection import project_flow_run, trigger_from_inputs
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


# ── flow-templates gallery (declared BEFORE /{flow_id} — see module docstring) ─────


@router.get("/templates")
async def list_flow_templates() -> list[dict]:
    """The flow-templates gallery: name + description + what each one teaches."""
    return flow_templates.list_templates()


@router.post("/templates/{name}/apply", status_code=201)
async def apply_flow_template(name: str) -> dict:
    """Instantiate template ``name`` into the store under a fresh id (never clobbers)."""
    try:
        flow = flow_templates.apply_template(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return flow.to_dict()


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
    idempotency_key = body_dict.get("idempotency_key")
    if idempotency_key is not None and not isinstance(idempotency_key, str):
        raise HTTPException(status_code=400, detail="'idempotency_key' must be a string")
    record = await delegate_flow(
        flow,
        inputs=inputs,
        company=company,
        durable=True,
        idempotency_key=idempotency_key or None,
    )
    await project_flow_run(record, trigger=trigger_from_inputs(inputs))
    return record
