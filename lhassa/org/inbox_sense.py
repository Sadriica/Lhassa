"""The org inbox → watchlist sense (Etapa 3): closes enqueue → heartbeat.

``org.messaging`` already has a real, durable per-instance inbox
(``inbox()`` / ``mark()``); nothing outside the module's own ``__main__`` demo
ever drained it. This module is the missing consumer: a watchlist check-func
that reads THIS instance's unread ``org_messages``, turns each ``TASK_ASSIGN``
into a ``Trigger``, and marks it delivered — so a message enqueued by
:class:`lhassa.org.coordinator.GeneralBrain` (or any coordinator hop) is
actually picked up on the next heartbeat, not left to rot in the queue.

Layering note (see ``doc/architecture-layers.md`` /
``tests/test_architecture.py``): ``core`` is the spine and must never import a
shell (``org`` is one). Rather than a lazy import inside ``core/watchlist.py``
(which the architecture guard would still flag — it walks every import in the
file regardless of nesting), this module INVERTS the seam: ``core.watchlist``
exposes a small ``register_check`` registry, and THIS module (a shell) registers
its own sense here, imported from a process entrypoint
(``actionpack.register_all()`` — called from every long-lived entrypoint:
``main.py``, ``cli/_boot.py``, ``notify/telegram_inbound.py``,
``org/watcher.py``). Core stays completely unaware that org exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lhassa.config import Config
from lhassa.core.watchlist import Trigger, register_check

if TYPE_CHECKING:
    pass

_REGISTERED = False

# The logical inbox identity this sense drains — the permanent root
# administrator (Lhassa is always the root; see ``org.topology``). A deeper org
# could extend this to also drain ``dept:<name>`` inboxes for each configured
# department, but Lhassa's own inbox is the one every top-level delegation
# (``GeneralBrain.assign`` / a promoted brain / a template) actually targets.
def _inbox_identity() -> str:
    return getattr(Config, "root_admin_id", "") or "lhassa"


async def _from_org_inbox(
    instance_id: str, out: "list[Trigger]", peek: bool = False
) -> None:
    """Unread (pending) org_messages addressed to this instance → Triggers.

    Only ``TASK_ASSIGN`` becomes work: a ``goal`` (+ optional ``department``)
    maps to a deterministic ``org_run`` Trigger (the fixed, $0-unless-live
    action — see ``actionpack._org.org_run``) UNLESS the message explicitly asks
    for judgment (``payload["judgment"]`` truthy), in which case the model
    reasons about how to handle it instead. Every read message (any kind) is
    marked "delivered" (unless ``peek=True``) so it fires exactly once — the
    queue's own ``mark`` is the dedup store, mirroring the other checks' use of
    ``watch_state`` / task ``estado``. $0 and NEVER raises: a missing DB, an
    absent org layer, or a malformed payload degrades to no triggers.
    """
    try:
        from lhassa.org import messaging
        from lhassa.org.messaging import MessageKind

        to_instance = _inbox_identity()
        pending = await messaging.inbox(
            to_instance, instance_id=instance_id, status="pending"
        )
        for msg in pending:
            if msg.kind is MessageKind.TASK_ASSIGN:
                payload = msg.payload or {}
                goal = str(payload.get("goal") or "").strip()
                if goal:
                    department = payload.get("department")
                    wants_judgment = bool(payload.get("judgment"))
                    if wants_judgment:
                        out.append(
                            Trigger(
                                kind="org",
                                action="org_message",
                                objetivo=goal,
                                params={"goal": goal, "department": department, "org_msg_id": msg.id},
                                origen_trust=1,  # INTERNO — inter-brain traffic, not world input
                                judgment=True,
                            )
                        )
                    else:
                        params: dict = {"goal": goal}
                        if isinstance(department, str) and department.strip():
                            params["department"] = department.strip()
                        out.append(
                            Trigger(
                                kind="org",
                                action="org_run",
                                objetivo=goal,
                                params=params,
                                origen_trust=1,  # INTERNO
                            )
                        )
            # Any other kind (TASK_RESULT / STATUS_*) is read-only bookkeeping
            # for now — still marked delivered so it does not pile up unread.
            if not peek:
                try:
                    await messaging.mark(msg.id, "delivered", instance_id=instance_id)
                except Exception as exc:
                    logger.debug(
                        "org.inbox_sense: could not mark {} delivered: {}", msg.id, exc
                    )
    except Exception as exc:
        logger.debug("org.inbox_sense: _from_org_inbox error: {}", exc)


def register() -> None:
    """Register :func:`_from_org_inbox` with the watchlist. Idempotent."""
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    register_check(_from_org_inbox)
