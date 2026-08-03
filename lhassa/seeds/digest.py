# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``digest`` seed — schedule Etapa 2's twice-daily operative digest.

Mints two recurring ``send_digest`` tasks (see :mod:`lhassa.actionpack._digest` /
:mod:`lhassa.notify.digest`): one due at 08:00 LOCAL time and one at 21:00 LOCAL
time, each with an 86400-second (daily) recurrence — ``core/step.py``'s recurrence
re-minting (§ the "hace y duerme" loop) keeps re-minting the next occurrence every
time one fires, so once applied this schedule runs forever with no further
intervention. Entirely deterministic and $0: minting a task spends no tokens, and
``send_digest`` itself never calls an LLM.

Idempotent + non-destructive, matching the ``cv`` seed's contract: re-applying does
NOT pile up duplicate schedules — it skips minting when enough matching pending
``send_digest`` tasks already exist.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.seeds import register_seed

if TYPE_CHECKING:
    from lhassa.config import Config

# The fixed action this seed schedules (registered by lhassa.actionpack).
_ACTION = "send_digest"

# Daily recurrence, in SECONDS (the unit ``core.dispatch.mint_task`` /
# ``core/step.py``'s recurrence re-minting expect).
_DAILY_SECONDS = str(24 * 60 * 60)

# Default (hour, minute) in LOCAL time for each scheduled digest; overridable via
# ``config.digest_hour_morning`` / ``config.digest_hour_evening``.
_DEFAULT_SCHEDULE: tuple[tuple[int, int], ...] = ((8, 0), (21, 0))


def _schedule(config: "Config") -> tuple[tuple[int, int], ...]:
    """The (hour, minute) pairs to schedule, honoring the config overrides."""
    overrides = (
        getattr(config, "digest_hour_morning", None),
        getattr(config, "digest_hour_evening", None),
    )
    return tuple(
        (h, 0) if isinstance(h, int) and 0 <= h <= 23 else (default_h, default_m)
        for h, (default_h, default_m) in zip(overrides, _DEFAULT_SCHEDULE)
    )


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def _next_local_occurrence(hour: int, minute: int = 0, *, now_local=None) -> datetime:
    """The next LOCAL wall-clock ``hour:minute`` (today, or tomorrow if already
    past), returned as a UTC-aware :class:`datetime`.

    Storing ``due_at`` in UTC matters: ``core/db.py`` compares ``due_at`` against
    ``datetime.now(timezone.utc).isoformat()`` as a plain STRING, which only sorts
    correctly when every stored timestamp shares the same (UTC, ``+00:00``) offset.
    """
    now_local = now_local or datetime.now().astimezone()
    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc)


async def _existing_digest_hours(instance_id: str) -> set[int]:
    """Local hours already covered by a pending ``send_digest`` task.

    Idempotency is per-SLOT, not a bare count: two tasks at the SAME hour must
    not satisfy a schedule that wants two DIFFERENT hours (morning + evening) —
    a count-based check (``existing >= len(schedule)``) let a doubled morning
    slot mask a missing evening one, so the evening digest was never minted and
    the owner got two morning digests and no evening. Keying on the local hour
    of each pending task makes the seed self-healing: it mints exactly the slots
    that are actually absent. Never raises — an unparseable ``due_at`` is skipped."""
    from lhassa.core import db

    tasks = await db.list_tasks(instance_id, estado="pendiente", limit=200)
    hours: set[int] = set()
    for t in tasks:
        if t.get("accion") != _ACTION:
            continue
        due = t.get("due_at")
        if not due:
            continue
        try:
            hours.add(datetime.fromisoformat(due).astimezone().hour)
        except (ValueError, TypeError):
            continue
    return hours


async def seed_digest_schedule(config: "Optional[Config]" = None) -> dict:
    """Mint the twice-daily ``send_digest`` tasks. Idempotent, non-destructive.

    Returns ``{"minted": [...], "skipped": bool, "errors": [...]}``. Skips minting
    when at least as many matching pending tasks already exist as the schedule has
    entries, so re-applying the seed never piles up duplicate recurring schedules.
    Never raises destructively — a partial failure is recorded under ``errors``.
    """
    cfg = _resolve_config(config)
    instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
    summary: dict = {"minted": [], "skipped": False, "errors": []}
    schedule = _schedule(cfg)

    try:
        from lhassa import actionpack

        actionpack.register_all()  # ensure "send_digest" is a known fixed action

        from lhassa.core import db
        from lhassa.core.dispatch import mint_task
        from lhassa.security.trust import Origin, TrustLevel

        await db.init_db()

        existing_hours = await _existing_digest_hours(instance_id)

        # HUMANO origin, actor "seed:digest" — a deliberate, human-authored bootstrap,
        # not a model judgment, so it needs no approval (send_digest is INTERNO/no-HITL).
        origin = Origin(trust=TrustLevel.HUMANO, actor_id="seed:digest")
        for hour, minute in schedule:
            if hour in existing_hours:
                continue  # this slot is already scheduled — never duplicate it
            due_at = _next_local_occurrence(hour, minute).isoformat()
            task_id, estado = await mint_task(
                instance_id,
                _ACTION,
                objetivo="operative digest",
                params={},
                origin=origin,
                due_at=due_at,
                recurrence=_DAILY_SECONDS,
            )
            if task_id:
                summary["minted"].append(
                    {"task_id": task_id, "due_at": due_at, "estado": estado}
                )
            else:
                summary["errors"].append(
                    f"mint failed for {hour:02d}:{minute:02d}: {estado}"
                )
        if not summary["minted"]:
            summary["skipped"] = True  # every wanted slot already existed
    except Exception as exc:  # partial failure is reported, not fatal
        logger.warning("seed[digest]: scheduling failed: {}", exc)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")

    return summary


@register_seed(
    "digest",
    "Schedule the twice-daily operative digest (08:00 and 21:00 local) as a "
    "recurring, $0 send_digest task.",
)
def apply(config: "Optional[Config]" = None) -> dict:
    """The ``digest`` seed entry point — see :func:`seed_digest_schedule`.

    ``Seed.apply`` is a plain sync callable (the CLI/``apply_all`` calling
    convention); ``seed_digest_schedule`` is async (it mints tasks through the DB),
    so this thin wrapper drives it with ``asyncio.run`` — safe here because seeds
    are only ever invoked from a sync context (the CLI), never from inside a
    already-running event loop.
    """
    return asyncio.run(seed_digest_schedule(config))
