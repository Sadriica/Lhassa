# SPDX-License-Identifier: AGPL-3.0-or-later
"""apply_feedback; the deterministic action fronting the feedback→knob bridge.

Runs :func:`lhassa.security.autonomy.apply_feedback` and renders a one-line
summary. $0, no external send; mirrors ``distill_memory`` / ``calendar_sync``
/ ``ingest_knowledge`` (all ``requires_approval=False``): a routine hygiene
pass that MOVES the autonomy knob from lived feedback (§2.2), never asks a
model, and never raises.
"""

from __future__ import annotations


async def apply_feedback(params: dict) -> str:
    """Apply recent behavioral feedback to the autonomy knob. Never raises.

    params:
        config; optional Config override (tests).
        window_days; optional int, how many days of episode history to read
            (default matches ``security.autonomy.apply_feedback``'s own default).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        config = params.get("config")
        window_days = params.get("window_days")

        from lhassa.config import Config
        from lhassa.security import autonomy

        cfg = config or Config
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"

        kwargs = {"config": cfg}
        if window_days is not None:
            kwargs["window_days"] = int(window_days)

        report = await autonomy.apply_feedback(instance_id, **kwargs)

        reset = report.get("reset") or []
        reinforced = report.get("reinforced") or []
        skipped = report.get("skipped", 0)
        window = report.get("window_days", 7)

        if not reset and not reinforced and not skipped:
            return f"apply_feedback: no scorable signals in the last {window}d"

        return (
            f"apply_feedback: reset {len(reset)} accion(s) {reset}, "
            f"reinforced {len(reinforced)} accion(s) {reinforced}, "
            f"skipped {skipped} unattributable/neutral signal(s) "
            f"(last {window}d)"
        )
    except Exception as exc:
        return f"apply_feedback: {type(exc).__name__}: {exc}"
