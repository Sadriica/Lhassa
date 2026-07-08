"""temple_run — the fixed action that routes internal hygiene work THROUGH templo.

Five deterministic actions (``distill_memory``, ``detect_repetition``,
``review_tools``, ``curate_skills``, ``calendar_sync``) used to run bare on the
heartbeat — a task minted with the action's own name, dispatched straight to
``core.actions.run`` with no org attribution. This module is the OTHER end of
the down-execute-up seam :mod:`lhassa.org.temple` implements: ``temple_run`` is
itself a registered action (so it can be minted like any other), and its body
routes the REAL action through :func:`lhassa.org.temple.delegate_internal` —
Lhassa delegates DOWN to templo, templo executes (its own ``dominio="templo"``
episode), and the result travels back UP via ``org.deliver``.

``_TEMPLE_ACTIONS`` is a closed set on purpose: ``temple_run`` is a thin,
deterministic, $0 front for the maintenance seed's own five actions, NOT a
generic "run any action via templo" bypass — an action that carries its OWN
approval gate (e.g. ``run_script``, ``run_command``) stays OUT of this set, so
wrapping never becomes a way to dodge an existing HITL frontier.
"""

from __future__ import annotations

#: The deterministic, $0 internal actions temple_run is allowed to front.
TEMPLE_ACTIONS: frozenset[str] = frozenset(
    {
        "distill_memory",
        "detect_repetition",
        "review_tools",
        "curate_skills",
        "calendar_sync",
    }
)


async def temple_run(params: dict) -> str:
    """Delegate one internal action to templo: down-hop, execute, deliver up.

    params:
        action        (required) — one of :data:`TEMPLE_ACTIONS`.
        params                   — the params handed to the wrapped action.
        instruction              — optional human-readable note for the
                                    down-hop episode (defaults to a generic
                                    "<action> maintenance pass").
        config                   — optional Config override (tests).

    Never raises — any failure degrades to an explanatory string, matching
    every other action in this package.
    """
    try:
        if not isinstance(params, dict):
            params = {}
        action = str(params.get("action") or "").strip()
        if action not in TEMPLE_ACTIONS:
            return f"temple_run: action {action!r} is not routed through templo"

        inner_params = params.get("params") or {}
        if not isinstance(inner_params, dict):
            inner_params = {}
        instruction = str(params.get("instruction") or f"{action} maintenance pass")
        config = params.get("config")

        from lhassa.org.temple import delegate_internal

        record = await delegate_internal(
            action, inner_params, config=config, instruction=instruction
        )
        result = record.get("result")
        delivered = record.get("delivered")
        return f"temple_run: {action} → {str(result)[:300]} (delivered={delivered})"
    except Exception as exc:
        return f"temple_run: {type(exc).__name__}: {exc}"
