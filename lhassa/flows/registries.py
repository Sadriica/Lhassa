# SPDX-License-Identifier: AGPL-3.0-or-later
"""De-Lhassa-ify seams: task routing, run context and output handling as registries.

The engine used to hard-wire three Lhassa-specific behaviours: org-topology task
routing, the CV's ``{base_cv}``/``{company}`` interpolation, and a ``tex`` output
type that shells out to :mod:`lhassa.cv`. None of that is intrinsic to a work-flow
graph — a deployment without the CV package should still be able to run flows.

This module turns each seam into a small :class:`lhassa.registry.Registry` whose
**default entry reproduces today's behaviour exactly**, so existing on-disk flows
(the CV pipeline, the research corpus) keep running byte-for-byte unchanged. A
flow opts into a non-default entry via ``config.router`` (task nodes) or
``config.context``/``run_flow(..., context=...)`` or ``config.type`` (output
nodes, already the existing key). An unregistered name degrades to the default —
or, for output handlers, to a plain log record — never a ``KeyError``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from lhassa.registry import Registry

# ── task routing ────────────────────────────────────────────────────────────
# (node, data, config) -> (department, pinned)
TaskRouter = Callable[..., "tuple[str, bool]"]
TASK_ROUTERS: Registry[TaskRouter] = Registry("task_router")

# ── run context (interpolation placeholders) ────────────────────────────────
# async (*, inputs, company_override, base_cv_override, config) -> dict
ContextProvider = Callable[..., Awaitable[dict]]
CONTEXT_PROVIDERS: Registry[ContextProvider] = Registry("context_provider")

# ── output handling ──────────────────────────────────────────────────────────
# (node, payload, *, company, run_id) -> record dict
OutputHandler = Callable[..., dict]
OUTPUT_HANDLERS: Registry[OutputHandler] = Registry("output_handler")

DEFAULT_TASK_ROUTER = "org_topology"
DEFAULT_CONTEXT_PROVIDER = "cv"


def _org_topology_router(node: Any, data: Any, config: Any) -> "tuple[str, bool]":
    """The org-topology task router — today's ``resolve_brain`` behaviour verbatim.

    HYBRID: a pinned ``config.brain`` wins (``pinned=True``). Otherwise the node's
    ``skill``/``domain`` (falling back to the prompt or the upstream data) auto-routes
    through the org topology to a leaf department, reusing the org's deterministic
    keyword routing. Any routing failure degrades to the skill text (or ``"general"``)
    so a task always has a target and never raises here.
    """
    cfg = node.config or {}
    pinned = (cfg.get("brain") or "").strip()
    if pinned:
        return pinned, True

    skill = (cfg.get("skill") or cfg.get("domain") or "").strip()
    routing_text = skill or str(cfg.get("prompt") or data or "").strip()
    try:
        from lhassa.org import topology as topo_mod
        from lhassa.org.coordinator import route_in_topology

        topology = topo_mod.load_topology(config)
        node_id = topology.root().id
        # Walk down one level at a time until a leaf — route_in_topology is single-hop.
        for _ in range(64):
            if topology.is_leaf(node_id):
                break
            node_id = route_in_topology(topology, node_id, routing_text)
        leaf = topology.node(node_id)
        return (leaf.department or leaf.id), False
    except Exception as exc:
        logger.debug("flows: auto-route degraded to skill text ({}): {}", routing_text, exc)
        return (skill or "general"), False


TASK_ROUTERS.register(DEFAULT_TASK_ROUTER, _org_topology_router)


def resolve_router(node: Any) -> TaskRouter:
    """Pick the task router for ``node``: ``config.router`` else the default.

    An unknown router name degrades to the default rather than raising — a
    misconfigured node still routes, it just doesn't get the custom behaviour.
    """
    name = ((node.config or {}).get("router") or DEFAULT_TASK_ROUTER).strip()
    router = TASK_ROUTERS.get_or_none(name)
    if router is None:
        if name != DEFAULT_TASK_ROUTER:
            logger.debug("flows: unknown task router {!r}, using default", name)
        router = TASK_ROUTERS.get(DEFAULT_TASK_ROUTER)
    return router


async def _cv_context_provider(
    *,
    inputs: Any,
    company_override: Optional[str],
    base_cv_override: Optional[str],
    config: Any,
) -> dict:
    """The CV context provider — today's base-CV-load + company-resolution verbatim.

    Supplies exactly the ``{base_cv}``/``{company}`` placeholders. ``base_cv_override``
    (non-``None``) wins verbatim (e.g. a CV a caller extracted from an uploaded
    document); ``None`` reads ``cv.load_base()`` from disk, degrading to ``""`` on
    any failure so a broken CV seam never crashes a run.
    """
    from lhassa.flows.engine import resolve_company

    if base_cv_override is None:
        try:
            from lhassa import cv

            base_cv = cv.load_base() or ""
        except Exception as exc:  # a broken CV seam must never crash a run
            logger.debug("flows: base CV unavailable ({})", exc)
            base_cv = ""
    else:
        base_cv = base_cv_override
    return {"base_cv": base_cv, "company": resolve_company(company_override, inputs)}


CONTEXT_PROVIDERS.register(DEFAULT_CONTEXT_PROVIDER, _cv_context_provider)


async def resolve_context(
    name: Optional[str],
    *,
    inputs: Any,
    company_override: Optional[str],
    base_cv_override: Optional[str],
    config: Any,
) -> dict:
    """Resolve ``{base_cv}``/``{company}`` (and any provider-added placeholders).

    An unknown provider name, or a provider that raises, degrades to the default
    CV provider's own degrade path (``base_cv=""``, best-effort company) — a run
    never crashes on context resolution.
    """
    provider_name = (name or DEFAULT_CONTEXT_PROVIDER).strip() or DEFAULT_CONTEXT_PROVIDER
    provider = CONTEXT_PROVIDERS.get_or_none(provider_name)
    if provider is None:
        if provider_name != DEFAULT_CONTEXT_PROVIDER:
            logger.debug("flows: unknown context provider {!r}, using default", provider_name)
        provider = CONTEXT_PROVIDERS.get(DEFAULT_CONTEXT_PROVIDER)
    try:
        result = await provider(
            inputs=inputs,
            company_override=company_override,
            base_cv_override=base_cv_override,
            config=config,
        )
        return dict(result) if isinstance(result, dict) else {}
    except Exception as exc:
        logger.debug("flows: context provider {!r} failed ({})", provider_name, exc)
        return {"base_cv": base_cv_override or "", "company": company_override or "untitled"}


def _tex_output_handler(
    node: Any, payload: Any, *, company: str = "untitled", run_id: Optional[str] = None
) -> dict:
    """The ``tex`` output handler — today's CV export behaviour verbatim.

    Exports a tailored ``.tex`` file via :mod:`lhassa.cv` to
    ``~/.lhassa/cv/out/{id}_{company}.tex`` and records its path. Best-effort:
    on any failure the record carries ``error`` instead of ``path``, never raises.
    """
    from lhassa import cv

    record: dict = {"output": "tex"}
    try:
        artifact = payload if isinstance(payload, str) else str(payload)
        artifact = cv.clean_latex(artifact)  # strip prose/fences → compile-ready .tex
        # Deterministically restore the owner's contact block from the base CV —
        # the model never has to reproduce it, and the contact_preserved gate
        # downstream always passes ($0; best-effort, degrades to no-op).
        artifact = cv.restore_contact(artifact, cv.load_base())
        path = cv.export(artifact, company=company, run_id=run_id)
        record["path"] = str(path)
        logger.info("flows: output[tex] {} -> {}", node.id, path)
    except Exception as exc:  # writing a file is best-effort; never crash the run
        record["error"] = str(exc)
        logger.warning("flows: output[tex] {} could not export: {}", node.id, exc)
    return record


OUTPUT_HANDLERS.register("tex", _tex_output_handler)


def resolve_output_handler(out_type: str) -> Optional[OutputHandler]:
    """Look up an output handler by type; ``None`` for an unregistered type.

    Callers degrade an unregistered type to a plain log record — never a
    ``KeyError``.
    """
    return OUTPUT_HANDLERS.get_or_none(out_type)


__all__ = [
    "CONTEXT_PROVIDERS",
    "DEFAULT_CONTEXT_PROVIDER",
    "DEFAULT_TASK_ROUTER",
    "OUTPUT_HANDLERS",
    "TASK_ROUTERS",
    "resolve_context",
    "resolve_output_handler",
    "resolve_router",
]
