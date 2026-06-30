"""Typed inter-brain messaging — the task queue between brains (Fase 7).

Inter-brain communication is **typed messages, never free prose**. A closed
``MessageKind`` enum is the whole vocabulary: a brain cannot invent a new kind of
message, which is exactly what prevents collusion/contradiction and keeps the
queue legible (the brief: "mensajes tipados entre instancias — NO prosa libre;
previene colusión/contradicción, es la cola de tareas entre cerebros").

A ``Message`` is a frozen dataclass; ``corr_id`` threads a ``TASK_RESULT`` back to
the ``TASK_ASSIGN`` it answers. Persistence is a thin layer over the instance-
scoped ``org_messages`` helpers in :mod:`lhassa.core.db` — this is the durable
queue, not an in-memory bus.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Union

from lhassa.config import Config
from lhassa.core import db


class UnknownMessageKind(ValueError):
    """Raised when a message kind is not a member of the closed ``MessageKind`` set."""


class MessageKind(str, Enum):
    """The CLOSED set of inter-brain message kinds. No free-prose kind exists.

    Adding a kind is a deliberate schema change here — not something a brain can
    do at runtime. Each kind has a known payload shape (documented per producer).
    """

    TASK_ASSIGN = "TASK_ASSIGN"        # general → department: do this typed work
    TASK_RESULT = "TASK_RESULT"        # department → general: here is the result
    STATUS_REQUEST = "STATUS_REQUEST"  # general → department: report your status
    STATUS_REPORT = "STATUS_REPORT"    # department → general: my status


# Valid persisted lifecycle states for a message (the queue's status column).
_STATUSES = ("pending", "delivered", "done")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def coerce_kind(value: Union[MessageKind, str]) -> MessageKind:
    """Coerce ``value`` to a :class:`MessageKind`, rejecting anything unknown.

    Accepts a ``MessageKind`` member or its string name/value. Any other token —
    e.g. a free-prose "kind" — raises :class:`UnknownMessageKind`. This is the
    single choke point that enforces typed-only messaging.
    """
    if isinstance(value, MessageKind):
        return value
    try:
        return MessageKind(value)
    except ValueError as exc:
        raise UnknownMessageKind(
            f"unknown message kind {value!r}; allowed: "
            f"{[k.value for k in MessageKind]}"
        ) from exc


@dataclass(frozen=True)
class Message:
    """One typed message on the inter-brain queue (immutable once created).

    Fields: ``id``, ``kind`` (a :class:`MessageKind`), ``from_instance`` /
    ``to_instance`` (logical brain identities — e.g. ``"general"`` or
    ``"dept:sales"``), ``payload`` (a dict, schema'd per kind), ``status``
    (pending/delivered/done), ``corr_id`` (correlates a result to its
    assignment) and ``creado_en``.
    """

    id: str
    kind: MessageKind
    from_instance: str
    to_instance: str
    payload: dict = field(default_factory=dict)
    status: str = "pending"
    corr_id: str = ""
    creado_en: str = ""

    def __post_init__(self) -> None:
        # Validate even on direct construction so an unknown/free-prose kind can
        # never exist as a Message (frozen → use object.__setattr__ to normalize).
        kind = coerce_kind(self.kind)
        object.__setattr__(self, "kind", kind)
        if self.status not in _STATUSES:
            raise ValueError(
                f"invalid status {self.status!r}; allowed: {list(_STATUSES)}"
            )


def new_message(
    kind: Union[MessageKind, str],
    *,
    from_instance: str,
    to_instance: str,
    payload: Optional[dict] = None,
    corr_id: str = "",
    status: str = "pending",
    msg_id: Optional[str] = None,
) -> Message:
    """Build a validated :class:`Message`, stamping a fresh id/timestamp.

    ``kind`` is validated through :func:`coerce_kind` (typed-only). A
    ``TASK_ASSIGN`` typically leaves ``corr_id`` empty and uses its own ``id`` as
    the correlation key; a ``TASK_RESULT`` carries the assignment's ``corr_id``.
    """
    kind = coerce_kind(kind)
    return Message(
        id=msg_id or db._new_id("msg"),
        kind=kind,
        from_instance=from_instance,
        to_instance=to_instance,
        payload=dict(payload or {}),
        status=status,
        corr_id=corr_id,
        creado_en=_now(),
    )


def _row_to_message(row: dict) -> Message:
    """Rebuild a :class:`Message` from a persisted ``org_messages`` row."""
    return Message(
        id=row["id"],
        kind=coerce_kind(row["kind"]),
        from_instance=row.get("from_instance") or "",
        to_instance=row.get("to_instance") or "",
        payload=row.get("payload") or {},
        status=row.get("status") or "pending",
        corr_id=row.get("corr_id") or "",
        creado_en=row.get("creado_en") or "",
    )


# ── persistence (the durable typed queue) ────────────────────────────────────────


async def enqueue(msg: Message, *, instance_id: str = Config.instance_id) -> str:
    """Persist a typed message onto the queue. Returns its id.

    Re-validates the kind (defence in depth): a non-:class:`MessageKind` payload
    can never be enqueued, so the queue holds typed messages only. Scoped to
    ``instance_id`` (the org/tenant).
    """
    kind = coerce_kind(msg.kind)
    return await db.enqueue_org_message(
        instance_id,
        kind=kind.value,
        from_instance=msg.from_instance,
        to_instance=msg.to_instance,
        payload=msg.payload,
        corr_id=msg.corr_id,
        status=msg.status,
        msg_id=msg.id,
        creado_en=msg.creado_en or _now(),
    )


async def inbox(
    to_instance: str,
    *,
    instance_id: str = Config.instance_id,
    status: Optional[str] = None,
) -> list[Message]:
    """Return the messages addressed to ``to_instance`` (oldest first).

    Optionally filtered by ``status``. Scoped to ``instance_id``.
    """
    rows = await db.get_org_inbox(instance_id, to_instance, status=status)
    return [_row_to_message(r) for r in rows]


async def by_corr(
    corr_id: str,
    *,
    instance_id: str = Config.instance_id,
    kind: Optional[Union[MessageKind, str]] = None,
) -> list[Message]:
    """Return messages sharing ``corr_id`` (oldest first), optionally by ``kind``.

    Used to correlate a ``TASK_RESULT`` back to its ``TASK_ASSIGN``.
    """
    kind_val = coerce_kind(kind).value if kind is not None else None
    rows = await db.get_org_messages_by_corr(instance_id, corr_id, kind=kind_val)
    return [_row_to_message(r) for r in rows]


async def mark(
    msg_id: str,
    status: str,
    *,
    instance_id: str = Config.instance_id,
) -> None:
    """Advance a message's status (pending → delivered → done). Instance-scoped."""
    if status not in _STATUSES:
        raise ValueError(f"invalid status {status!r}; allowed: {list(_STATUSES)}")
    await db.set_org_message_status(instance_id, msg_id, status)
