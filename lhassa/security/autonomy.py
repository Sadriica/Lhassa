"""The autonomy knob — friction that lowers with repeated human approval (§2.6, #9).

"La autonomía se gana con evidencia. Supervisado primero; perilla que baja con
aprobaciones repetidas." This module tracks, per ``(accion, actor_id)`` within an
instance, the count of CONSECUTIVE human-origin approvals of that action. Once
that streak reaches ``Config.autonomy_threshold`` (default 3) the action
auto-approves: "una `humano` repetida se vuelve automática".

Three hard rules, straight from the brief and the invariants:

1. **Per-action, per-actor — never global.** HITL is by flow. The streak is keyed
   on the specific action AND the specific WHO, so trust earned for drafting a
   document never leaks into moving money.

2. **The high-risk set is never auto-promoted.** Actions in
   ``Config.always_approve_actions`` (e.g. move_money / delete_data) ALWAYS require
   human approval regardless of history — "mover-dinero exige aprobación aunque un
   borrador no". ``should_auto_approve`` returns ``False`` for them unconditionally.

3. **The knob never raises trust.** It only LOWERS friction for actions a human has
   already repeatedly approved; it does NOT promote an ``Origin``'s ``TrustLevel``
   (principle #7: "la confianza se degrada por contagio, nunca se promueve"). A
   denial resets the streak to zero.

The counters live in the ``autonomy`` table of the "grande" (``core.db``), keyed by
``instance_id`` like every other row. These helpers are ``async`` because the store
is async; the brief's ``def`` signatures are illustrative.
"""

from typing import Optional

from lhassa.config import Config
from lhassa.core import db
from lhassa.security.trust import TrustLevel


def _always_approve() -> "set[str]":
    """The configured high-risk action set (never auto-promoted)."""
    actions = Config.always_approve_actions
    return actions if isinstance(actions, set) else set(actions or [])


def _threshold() -> int:
    """Consecutive-human-approval count after which an action auto-approves."""
    return int(Config.autonomy_threshold)


def _key_actor(actor_id: Optional[str]) -> str:
    """Normalize an actor id for the streak key (``None`` → ``""``, unattributed)."""
    return actor_id or ""


async def autonomy_level(instance_id: str, accion: str, actor_id: Optional[str]) -> int:
    """Return the current consecutive-human-approval streak for (accion, actor_id)."""
    return await db.get_autonomy_streak(instance_id, accion, _key_actor(actor_id))


async def should_auto_approve(
    instance_id: str, accion: str, actor_id: Optional[str]
) -> bool:
    """True if this (accion, actor_id) has earned auto-approval.

    Returns ``False`` for any action in the high-risk always-approve set,
    regardless of how long its streak is (hard rule #2). Otherwise true once the
    streak has reached the configured threshold.
    """
    if accion in _always_approve():
        return False
    return (await autonomy_level(instance_id, accion, actor_id)) >= _threshold()


async def record_decision(
    instance_id: str,
    accion: str,
    actor_id: Optional[str],
    *,
    approved: bool,
    origen_trust: Optional[int] = None,
) -> int:
    """Feed one approval decision into the knob and return the resulting streak.

    - A **denial** resets the streak to zero (the autonomy must be re-earned).
    - An **approval of a HUMANO-origin action** advances the streak by one — this
      is the only thing that builds autonomy ("una `humano` repetida se vuelve
      automática").
    - An approval whose action did NOT originate from a human leaves the streak
      unchanged: it neither builds autonomy (no contagion-promotion) nor punishes
      an otherwise-clean history.

    ``origen_trust`` is the ``TrustLevel`` (int) of the action that was parked for
    approval; when it is unknown we treat the approval as human-origin (the common
    case is a human directly approving their own parked action).
    """
    actor = _key_actor(actor_id)
    if not approved:
        await db.reset_autonomy_streak(instance_id, accion, actor)
        return 0
    if origen_trust is not None and int(origen_trust) < int(TrustLevel.HUMANO):
        # Non-human-origin approval: do not build autonomy (never promote trust).
        return await db.get_autonomy_streak(instance_id, accion, actor)
    return await db.bump_autonomy_streak(instance_id, accion, actor)
