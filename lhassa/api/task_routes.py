# SPDX-License-Identifier: AGPL-3.0-or-later
"""FastAPI routes for tasks and the watchlist preview.

Routes::

    GET    /api/tasks?estado=... ; list tasks (newest first, cap 200)
    POST   /api/tasks            ; mint a new task via dispatch.mint_task
    GET    /api/watchlist        ; preview pending triggers (read-only peek)

Minting a task requires the ``accion`` to be in the ACTIONS registry OR the
``judgment`` flag set to ``true`` in the body. A judgment task is the
standing-goal use case: the user asks the brain to THINK about something on a
schedule (e.g. accion "review my inbox"); which costs tokens each tick.

``recurrence`` is accepted as an integer of seconds OR the convenience aliases
``hourly`` (3600), ``daily`` (86400), ``weekly`` (604800). ``due_at`` may be an
ISO 8601 timestamp or a relative shorthand ``+Nh`` / ``+Nm`` / ``+Ns``
(``N`` hours / minutes / seconds from now).

The auth middleware (``require_local_or_token`` in server.py) already guarantees
that only the owner or a holder of LHASSA_API_TOKEN can reach these routes; no
extra auth check is needed here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

from lhassa.config import Config
from lhassa.core import actions as _actions
from lhassa.core import db as _db
from lhassa.core import dispatch as _dispatch
from lhassa.core import watchlist as _watchlist
from lhassa.security.trust import Origin, TrustLevel

router = APIRouter(prefix="/api", tags=["tasks"])

# Convenience aliases for recurrence.
_RECURRENCE_ALIASES = {
    "hourly": "3600",
    "daily": "86400",
    "weekly": "604800",
}


def _parse_recurrence(raw: Any) -> Optional[str]:
    """Parse recurrence from the API body into a seconds-string or None."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in _RECURRENCE_ALIASES:
        return _RECURRENCE_ALIASES[s]
    try:
        secs = int(float(s))
        if secs > 0:
            return str(secs)
    except (ValueError, TypeError):
        pass
    return None


def _parse_due_at(raw: Any) -> Optional[str]:
    """Parse due_at: ISO 8601 string or relative shorthand ``+Nh`` / ``+Nm`` / ``+Ns``."""
    if raw is None:
        return None
    s = str(raw).strip()
    # Relative shorthand: +5h, +30m, +120s
    if s.startswith("+"):
        suffix = s[1:].lower()
        try:
            if suffix.endswith("h"):
                delta = timedelta(hours=float(suffix[:-1]))
            elif suffix.endswith("m"):
                delta = timedelta(minutes=float(suffix[:-1]))
            elif suffix.endswith("s"):
                delta = timedelta(seconds=float(suffix[:-1]))
            else:
                # Bare number treated as seconds.
                delta = timedelta(seconds=float(suffix))
            return (datetime.now(timezone.utc) + delta).isoformat()
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid due_at shorthand {raw!r}. Use +Nh / +Nm / +Ns.",
            )
    # ISO 8601; try parsing to validate.
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid due_at {raw!r}. Use ISO 8601 or a shorthand like +1h.",
        )


# ── routes ───────────────────────────────────────────────────────────────────


@router.get("/tasks")
async def list_tasks(estado: Optional[str] = None) -> list[dict]:
    """List tasks (newest first, cap 200), optionally filtered by estado."""
    await _db.init_db()
    instance_id = Config.instance_id
    return await _db.list_tasks(instance_id, estado=estado, limit=200)


@router.post("/tasks", status_code=201)
async def create_task(body: dict = Body(...)) -> dict:
    """Mint a new task via dispatch.mint_task (full security path: sign + approval).

    Required fields:
      - ``accion`` (str); the action name

    Optional fields:
      - ``objetivo`` (str); target of the action
      - ``params`` (dict); action parameters
      - ``due_at`` (str); ISO 8601 timestamp or ``+Nh``/``+Nm``/``+Ns`` shorthand
      - ``recurrence`` (int|str); seconds or ``hourly``/``daily``/``weekly``
      - ``judgment`` (bool); when true, any accion is allowed (costs tokens each tick)

    Returns ``{id, estado}`` on success, ``400`` on validation failure.
    """
    await _db.init_db()
    instance_id = Config.instance_id

    accion = str(body.get("accion") or "").strip()
    if not accion:
        raise HTTPException(status_code=400, detail="'accion' is required")

    judgment = bool(body.get("judgment", False))

    # Validate accion against registry unless judgment flag is set.
    if not judgment and accion not in _actions.ACTIONS:
        names = sorted(_actions.ACTIONS.keys())
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown accion {accion!r}. Registered actions: {names}. "
                "Set judgment=true to allow any accion (costs tokens each tick)."
            ),
        )

    objetivo = str(body.get("objetivo") or "").strip()
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="'params' must be a JSON object")

    recurrence = _parse_recurrence(body.get("recurrence"))
    due_at = _parse_due_at(body.get("due_at"))

    origin = Origin(trust=TrustLevel.HUMANO, actor_id="api")

    task_id, estado = await _dispatch.mint_task(
        instance_id,
        accion,
        objetivo=objetivo,
        params=params,
        origin=origin,
        due_at=due_at,
        recurrence=recurrence,
        judgment=judgment,
    )
    if not task_id:
        raise HTTPException(status_code=400, detail=estado)
    return {"id": task_id, "estado": estado}


@router.get("/watchlist")
async def get_watchlist() -> dict:
    """Preview the watchlist: registered sense names + a read-only trigger list.

    The preview runs ``watchlist.check(peek=True)`` which skips all dedup state
    writes (no side effects beyond DB reads). Trigger fields: kind, action,
    objetivo, task_id (nullable), params.

    The dashboard WatchlistPanel consumes this endpoint.
    """
    await _db.init_db()
    instance_id = Config.instance_id

    senses = [fn.__name__ for fn in _watchlist._CHECKS]

    triggers = await _watchlist.check(instance_id, peek=True)
    serialised = [
        {
            "kind": t.kind,
            "action": t.action,
            "objetivo": t.objetivo,
            "task_id": t.task_id,
            "params": t.params,
        }
        for t in triggers
    ]
    return {"senses": senses, "preview": serialised}
