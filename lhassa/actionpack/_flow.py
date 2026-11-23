# SPDX-License-Identifier: AGPL-3.0-or-later
"""run_flow_action; the fixed action that puts a work-flow on Lhassa's own clock.

Every other recurring thing Lhassa does rides ONE clock: a row in the ``tasks``
table that the heartbeat advances (``schedule_reminder`` mints ``send_reminder``,
the maintenance seed mints ``temple_run``, ``lhassa/scheduler.py`` is a
deprecation shim). Work-flows were the exception; there was no action that runs
one, so a flow could only be scheduled from OUTSIDE the brain. Asked to run a
dashboard daily, Lhassa did the only thing available to it and wrote a systemd
timer: the work happens, but off the one clock, invisible to the episode log and
unknown to the brain that scheduled it. This closes that gap.

The standing principle holds here: a flow is NEVER run bare. This delegates
through ``org/delegate.py:delegate_flow``, which resolves the owning department,
logs the ``org-delegate`` hop and only then reaches the engine.
"""

from __future__ import annotations


async def run_flow_action(params: dict) -> str:
    """Run a stored work-flow through the org hierarchy.

    params:
        flow_id     (required); the stored flow's id.
        inputs                 ; optional dict threaded into the trigger node.
        durable                ; bool; record a durable run (default False).
        instance_id            ; defaults to Config.instance_id.

    Minted with a ``recurrence`` this becomes a real standing job: the heartbeat
    re-mints the next occurrence exactly as it does for the digest.

    Never raises; any failure degrades to an explanatory string, matching every
    other action in this package.
    """
    try:
        if not isinstance(params, dict):
            params = {}
        flow_id = str(params.get("flow_id") or "").strip()
        if not flow_id:
            return "run_flow_action: 'flow_id' param is required"

        from lhassa.config import Config
        from lhassa.flows.store import FlowStore
        from lhassa.org.delegate import delegate_flow

        config = params.get("config") or Config()
        flow = FlowStore(config).get(flow_id)  # FlowStore takes the config, not a path
        if flow is None:
            return f"run_flow_action: no stored flow {flow_id!r}"

        inputs = params.get("inputs")
        record = await delegate_flow(
            flow,
            inputs=inputs if isinstance(inputs, dict) else None,
            config=config,
            durable=bool(params.get("durable")),
        )

        status = str((record or {}).get("status") or "unknown")
        steps = (record or {}).get("steps") or []
        failed = [
            str(s.get("node"))
            for s in steps
            if isinstance(s, dict) and not s.get("ok")
        ]
        detail = f"; failed step(s): {', '.join(failed)}" if failed else ""
        run_id = str((record or {}).get("id") or "")
        run_note = f" (run {run_id})" if run_id else ""
        return (
            f"run_flow_action: {flow_id} {status}, "
            f"{len(steps)} step(s){detail}{run_note}"
        )

    except Exception as exc:
        return f"run_flow_action: error: {exc}"
