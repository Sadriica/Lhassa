# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``curate_skills`` action — Fase 2's deterministic, $0 skill lifecycle sweep.

A thin ``(params) -> str`` wrapper over :func:`lhassa.skills.curator.run_transitions`
so the curator's usage-driven ``active -> stale -> archived`` sweep fits the
actionpack calling convention (every action is a plain callable, never raises,
returns a short outcome string). Skips entirely while an agent run is active, so
the curator never touches the skills directory mid-think. Minted as a recurring
weekly task by the ``maintenance`` seed (mirrors ``review_tools``) — see
:mod:`lhassa.seeds.maintenance`.
"""

from __future__ import annotations

from lhassa.config import Config
from lhassa.org import watcher as _watcher
from lhassa.skills import curator as _curator


async def curate_skills(params: dict) -> str:
    """Run the skill usage lifecycle sweep (active -> stale -> archived). $0, no LLM.

    Skips (returns without touching anything) while any tracked agent run is
    currently running, so the curator never mutates the skills directory
    mid-think.

    params: (none required — ``config`` defaults to the process ``Config()``).
    Never raises.
    """
    try:
        if _watcher.is_active():
            return "curate_skills: skipped — agent run active"

        summary = _curator.run_transitions(Config())
        return (
            f"curate_skills: checked {summary.get('checked', 0)}, "
            f"stale {summary.get('stale', 0)}, "
            f"archived {summary.get('archived', 0)}, "
            f"skipped_pinned {summary.get('skipped_pinned', 0)}"
        )
    except Exception as exc:  # an action must NEVER raise
        return f"curate_skills: error: {exc}"
