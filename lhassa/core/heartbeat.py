# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The heartbeat; the brain's pulse (§2.1).

"Hace y duerme": a systemd timer (or any caller) fires ``beat()`` on a fixed
cadence; it ensures the brain DB exists, advances the brain exactly one tick via
``step()``, and returns ``step``'s summary. Nothing here calls an LLM; a routine
beat is deterministic and costs zero tokens (§0, §2.4). The model only wakes for
the hard judgment, which ``step()`` defers to the Phase 2 router.

This is the timer-side counterpart to the machine-side ``remote.local_agent``
heartbeat: the local agent pulses *toward* the brain (check-in), and ``beat()``
pulses the brain itself.
"""

from typing import Optional

from lhassa.config import Config
from lhassa.core import db, step


async def beat(instance_id: Optional[str] = None) -> dict:
    """One heartbeat: ensure the DB, advance the brain one tick, return the summary.

    Returns ``step()``'s summary dict (e.g. ``{"triggers": N, "actions": A,
    "deferred": D}`` or ``{"triggers": 0}`` when nothing woke it). Deterministic
    and LLM-free; safe to call on a timer.
    """
    instance_id = instance_id or Config.instance_id
    await db.init_db()
    return await step.step(instance_id)
