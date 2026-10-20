# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cv pack's work-flow entries: the ``cv`` context provider and ``tex`` output.

Both plug into the registries in :mod:`lhassa.flows.registries` (core never imports
this pack). The behaviour is the one the engine used to hard-wire:

  * ``cv`` (the default context provider name): ``{base_cv}`` is the base CV on disk
    (``cv.load_base()``) unless the caller passed an override (e.g. a CV extracted
    from an uploaded document); ``{company}`` resolves as it always has.
  * ``tex`` (an output node type): clean the model's answer into compile-ready LaTeX,
    restore the owner's contact block from the base CV, export it to
    ``~/.lhassa/cv/out/{id}_{company}.tex`` and record the path.

Both degrade instead of raising: a broken CV seam yields ``base_cv=""``, and a failed
export yields a record carrying ``error`` instead of ``path``.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from lhassa.flows.registries import (
    CONTEXT_PROVIDERS,
    DEFAULT_CONTEXT_PROVIDER,
    OUTPUT_HANDLERS,
)


async def cv_context_provider(
    *,
    inputs: Any,
    company_override: Optional[str],
    base_cv_override: Optional[str],
    config: Any,
) -> dict:
    """Supply ``{base_cv}``/``{company}``; the base CV read from disk by default.

    ``base_cv_override`` (non-``None``) wins verbatim; ``None`` reads
    ``cv.load_base()``, degrading to ``""`` on any failure so a broken CV seam never
    crashes a run.
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


def tex_output_handler(
    node: Any, payload: Any, *, company: str = "untitled", run_id: Optional[str] = None
) -> dict:
    """Export a tailored ``.tex`` and record its path. Best-effort, never raises."""
    from lhassa import cv

    record: dict = {"output": "tex"}
    try:
        artifact = payload if isinstance(payload, str) else str(payload)
        artifact = cv.clean_latex(artifact)  # strip prose/fences, keep compile-ready .tex
        # Deterministically restore the owner's contact block from the base CV;
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


def register() -> None:
    """Register both entries. Idempotent (a second call is a no-op)."""
    if DEFAULT_CONTEXT_PROVIDER not in CONTEXT_PROVIDERS:
        CONTEXT_PROVIDERS.register(DEFAULT_CONTEXT_PROVIDER, cv_context_provider)
    if "tex" not in OUTPUT_HANDLERS:
        OUTPUT_HANDLERS.register("tex", tex_output_handler)


__all__ = ["cv_context_provider", "register", "tex_output_handler"]
