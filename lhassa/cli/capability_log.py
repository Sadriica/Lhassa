# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record that a ``lhassa`` subcommand exercised one of the brain's capabilities.

``agents/utility.py`` answers "is this capability still used, and by whom?" by
grouping the episode log on ``accion``. That works because everywhere else a
capability leaves an episode naming itself. Subcommands did not: running
``lhassa memory add`` writes a fact and leaves no trace the tribunal can read,
so the owner's most direct use of a capability was also its most invisible, and
the tribunal began parking proposals to retire things used daily.

The chat door records its tool calls (``cli/agent.py:_log_capability_uses``);
this is the other half, for the subcommands the owner types by hand. Same shape,
same wake source, same reason.

Deliberately NOT a map of every subcommand. Only a command that IS the door onto
a registered action belongs here; a read like ``lhassa status`` exercises no
capability and inventing a row for it would corrupt the very ledger this exists
to make honest.
"""

from __future__ import annotations

import asyncio

from loguru import logger

#: ``<command path>`` -> the ACTIONS registry name it exercises.
COMMAND_CAPABILITY: dict = {
    "memory add": "add_fact",
    "task add": "add_task",
    "knowledge ingest": "ingest_knowledge",
    "project create": "create_project",
    "config set": "set_config",
    "flow run": "run_flow",
    "org run": "org_run",
    "report": "check_spend",
    "code": "run_command",
}


def capability_for(command_path: str) -> "str | None":
    """The capability ``command_path`` is a door onto, or ``None``."""
    return COMMAND_CAPABILITY.get((command_path or "").strip())


async def record(capability: str, command_path: str, instance_id: str = "") -> None:
    """Append one $0 Episode naming ``capability``. Never raises."""
    try:
        from lhassa.config import Config
        from lhassa.core import db
        from lhassa.core.episode import Episode

        await db.init_db()  # idempotent; a subcommand may be the first thing to wake
        ep = Episode.begin(
            despertar_por="cli",  # classify_wake buckets this as owner-driven
            instance_id=instance_id or Config.instance_id,
        )
        ep.finish(
            decision=f"capability use recorded from `lhassa {command_path}`",
            accion=capability,
            resultado=f"{capability}: exercised from the terminal",
            costo_usd=0.0,
        )
        await ep.append()
    except Exception as exc:  # accounting must never cost the owner their command
        logger.debug(f"cli: capability-use log degraded to a no-op: {exc}")


def record_sync(command_path: str) -> None:
    """Blocking wrapper for the Typer/Click layer, which is not async.

    A no-op when the command maps to nothing, and a no-op when a loop is already
    running (the async doors do their own logging and must not be re-entered).
    """
    capability = capability_for(command_path)
    if not capability:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no loop: the normal subcommand case, safe to run one
    else:
        return
    try:
        asyncio.run(record(capability, command_path))
    except Exception as exc:
        logger.debug(f"cli: capability-use log degraded to a no-op: {exc}")
