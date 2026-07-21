"""The ``maintenance`` seed — mint the recurring self-improvement/hygiene tasks.

Five of the built-in deterministic actions only do anything useful if something
schedules them repeatedly: ``distill_memory`` (turn recent episodes into durable
facts), ``review_tools`` (gate 4 — retire unused/failing graduated tools),
``detect_repetition`` (spot a repeated-judgment pattern worth automating),
``curate_skills`` (Fase 2 — the skill usage lifecycle sweep: active -> stale ->
archived, never deletes), ``calendar_sync`` (the master-calendar reconcile —
a $0 set diff that delivers a ``.ics`` for each event missing from the primary
calendar; a clean no-op with no primary/feeds configured, so it's safe to
schedule unconditionally), ``derive_world_facts`` (the calendar-derived
world model — bootstraps durable facts about courses/commitments/rhythm from
the calendar feed; also a clean no-op with no calendar configured), and
``ingest_knowledge`` (document perception — re-indexes the owner's opt-in
knowledge roots; a clean no-op with ``LHASSA_KNOWLEDGE_ROOTS`` unset).

Rather than minting each action bare (a task that used to dispatch straight to
``core.actions.run`` with no org attribution), this seed mints ``temple_run``
— see :mod:`lhassa.actionpack._temple` / :mod:`lhassa.org.temple` — with the
real action name in its params. Each beat now runs the FULL down-execute-up
loop: Lhassa delegates the action DOWN to the ``temple`` department (one
``org-delegate`` Episode), temple EXECUTES it via the same deterministic
action (its own ``dominio="temple"`` Episode; what the action DOES is
unchanged), and the result travels back UP to Lhassa via ``org.deliver`` (the
sender of record). This is still minted through the same
:func:`lhassa.core.dispatch.mint_task` path everything else uses, so it shows
up in ``lhassa task list`` and fires on schedule through the ordinary
watchlist/step loop — no new scheduler, no core edit.

Idempotent: a matching PENDING recurring ``temple_run`` task for the same
wrapped action is left alone rather than duplicated, so re-applying this seed
on an already-seeded machine is a no-op. Never raises destructively — a single
mint failing is recorded under ``errors``, the others still proceed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.seeds import register_seed

if TYPE_CHECKING:
    from lhassa.config import Config

# accion -> recurrence in seconds. distill_memory/detect_repetition/calendar_sync
# are daily hygiene passes; review_tools (gate 4) and curate_skills (Fase 2) run
# less often — both need time to accrue real usage before "unused" means
# anything. derive_world_facts is a gentler weekly cadence too — the owner's
# courses/commitments/rhythm don't change day to day, so a daily re-derive
# would just re-check dedup for nothing. Each is minted as accion="temple_run"
# with the real action name carried in params["action"] — see
# lhassa.actionpack._temple.TEMPLE_ACTIONS (the closed set temple_run is
# allowed to front).
_RECURRING_ACTIONS: tuple[tuple[str, int], ...] = (
    ("distill_memory", 86400),
    ("detect_repetition", 86400),
    ("apply_feedback", 86400),
    ("review_tools", 604800),
    ("curate_skills", 604800),
    ("calendar_sync", 86400),
    ("derive_world_facts", 604800),
    # Document perception — re-index the owner's knowledge roots weekly (a
    # clean $0 no-op when LHASSA_KNOWLEDGE_ROOTS is unset; notes/papers don't
    # change so fast that a daily pass would earn its keep).
    ("ingest_knowledge", 604800),
)


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def _is_matching_temple_task(row: dict, accion: str) -> bool:
    """Whether a pending row is already the ``temple_run`` wrapper for ``accion``."""
    if row.get("accion") != "temple_run" or not row.get("recurrence"):
        return False
    params = row.get("params")
    if isinstance(params, str):
        import json

        try:
            params = json.loads(params)
        except (json.JSONDecodeError, ValueError):
            return False
    return isinstance(params, dict) and params.get("action") == accion


async def _ensure_recurring_task(instance_id: str, accion: str, recurrence: int) -> str:
    """Mint ``accion`` (via the ``temple_run`` wrapper) as a recurring task,
    unless a matching pending one already exists.

    Returns ``"created"`` or ``"existing"``.
    """
    from lhassa.core import db as _db
    from lhassa.core.dispatch import mint_task
    from lhassa.security.trust import Origin, TrustLevel

    # ALL pending tasks, not load_pending_tasks() — that one is DUE-AWARE and
    # hides a task whose due_at is still in the future, so a recurring wrapper
    # already scheduled for tomorrow would be invisible here and re-minted every
    # apply (the real cause of the temple_run pile-up). list_tasks sees them.
    pending = await _db.list_tasks(instance_id, estado="pendiente", limit=500)
    for row in pending:
        if _is_matching_temple_task(row, accion):
            return "existing"

    origin = Origin(trust=TrustLevel.INTERNO, actor_id="seed:maintenance")
    task_id, estado = await mint_task(
        instance_id,
        "temple_run",
        params={"action": accion, "instruction": f"{accion} maintenance pass"},
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
    "detect_repetition (daily), review_tools (weekly), curate_skills (weekly), "
    "calendar_sync (daily), derive_world_facts (weekly), ingest_knowledge (weekly).",
)
def apply(config: "Optional[Config]" = None) -> dict:
    """The ``maintenance`` seed entry point — see :func:`seed_maintenance`."""
    return seed_maintenance(config)
