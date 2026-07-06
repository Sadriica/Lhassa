"""The ``maintenance`` seed — mint the recurring self-improvement/hygiene tasks.

Four of the built-in deterministic actions only do anything useful if something
schedules them repeatedly: ``distill_memory`` (turn recent episodes into durable
facts), ``review_tools`` (gate 4 — retire unused/failing graduated tools),
``detect_repetition`` (spot a repeated-judgment pattern worth automating), and
``curate_skills`` (Fase 2 — the skill usage lifecycle sweep: active -> stale ->
archived, never deletes). This seed mints them as recurring tasks via the same
task-minting path everything else uses (:func:`lhassa.core.dispatch.mint_task`),
so they show up in ``lhassa task list`` and fire on schedule through the
ordinary watchlist/step loop — no new scheduler, no core edit.

Idempotent: a matching PENDING recurring task (same accion) is left alone rather
than duplicated, so re-applying this seed on an already-seeded machine is a
no-op. Never raises destructively — a single mint failing is recorded under
``errors``, the others still proceed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.seeds import register_seed

if TYPE_CHECKING:
    from lhassa.config import Config

# accion -> recurrence in seconds. distill_memory/detect_repetition are daily
# hygiene passes; review_tools (gate 4) and curate_skills (Fase 2) run less
# often — both need time to accrue real usage before "unused" means anything.
_RECURRING_ACTIONS: tuple[tuple[str, int], ...] = (
    ("distill_memory", 86400),
    ("detect_repetition", 86400),
    ("review_tools", 604800),
    ("curate_skills", 604800),
)


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


async def _ensure_recurring_task(instance_id: str, accion: str, recurrence: int) -> str:
    """Mint ``accion`` as a recurring task unless a matching pending one exists.

    Returns ``"created"`` or ``"existing"``.
    """
    from lhassa.core import db as _db
    from lhassa.core.dispatch import mint_task
    from lhassa.security.trust import Origin, TrustLevel

    pending = await _db.load_pending_tasks(instance_id)
    for row in pending:
        if row.get("accion") == accion and row.get("recurrence"):
            return "existing"

    origin = Origin(trust=TrustLevel.INTERNO, actor_id="seed:maintenance")
    task_id, estado = await mint_task(
        instance_id,
        accion,
        origin=origin,
        recurrence=str(recurrence),
    )
    if not task_id:
        raise RuntimeError(estado)
    return "created"


def seed_maintenance(config: "Optional[Config]" = None) -> dict:
    """Mint the recurring self-improvement/hygiene tasks. Idempotent.

    Returns ``{"created": [accion, ...], "existing": [accion, ...], "errors": [...]}``.
    """
    import asyncio

    cfg = _resolve_config(config)
    instance_id = getattr(cfg, "instance_id", None) or "inst_personal"

    summary: dict = {"created": [], "existing": [], "errors": []}

    async def _run() -> None:
        from lhassa.core import db as _db

        await _db.init_db()
        for accion, recurrence in _RECURRING_ACTIONS:
            try:
                status = await _ensure_recurring_task(instance_id, accion, recurrence)
                summary[status].append(accion)
            except Exception as exc:  # a single failed mint never aborts the seed
                logger.warning("seed[maintenance]: {} failed: {}", accion, exc)
                summary["errors"].append(f"{accion}: {type(exc).__name__}: {exc}")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_run())
    else:
        # Already inside a running loop (e.g. called from async test code) —
        # this seed is only ever applied from sync CLI/bootstrap contexts, but
        # degrade gracefully rather than crash if that assumption ever breaks.
        summary["errors"].append("seed_maintenance: called from a running event loop")

    return summary


@register_seed(
    "maintenance",
    "Mint the recurring self-improvement/hygiene tasks: distill_memory (daily), "
    "detect_repetition (daily), review_tools (weekly), curate_skills (weekly).",
)
def apply(config: "Optional[Config]" = None) -> dict:
    """The ``maintenance`` seed entry point — see :func:`seed_maintenance`."""
    return seed_maintenance(config)
