# SPDX-License-Identifier: AGPL-3.0-or-later
"""
review_capabilities — the utility tribunal's weekly sitting (deterministic, $0).

The counterweight action to ``review_tools``/``curate_skills``: where those ask
whether a TOOL still works or a SKILL still gets read, this asks whether a
CAPABILITY still serves the owner. Detection lives in
:mod:`lhassa.agents.utility` (a pure derived read over the episode log, no LLM,
no new storage); this module turns each finding into ONE parked approval and a
single calm notification.

It never retires anything. A registered action lives in code — the approval
records the recommendation and asks the owner to remove it or pin it. That is
the same rule the project-detection sense follows: detection never acts, the
owner's tap is the act.
"""

from __future__ import annotations

from lhassa.agents import utility as _utility
from lhassa.config import Config
from lhassa.core import db as _db
from lhassa.notify.dispatch import notify as _notify
from lhassa.security import approval as _approval

# watch_state key prefix: a capability is put on the docket ONCE, not on every
# weekly sweep — the same transition-dedup idiom detect_repetition uses.
_SEEN_PREFIX = "utility:proposed:"

# Never park more than this many approvals in a single sweep. The tribunal is
# meant to be calm: the docket is sorted least-earned-first, so the cap keeps
# the worst offenders and defers the rest to next week rather than flooding the
# queue on first run. A truncated docket is REPORTED, never silently dropped.
_MAX_PER_RUN = 5


async def review_capabilities(params: dict) -> str:
    """Report capabilities that no owner-driven use exercises; propose retiring them.

    Runs ``agents.utility.find_dormant`` ($0 SQL over episodes + the live
    ACTIONS registry). For each NEW finding (deduped via ``watch_state``) parks
    a ``retire_capability`` approval and, if anything was parked, sends ONE
    summary notification. Never raises; never calls an LLM.

    params:
        instance_id  — defaults to Config.instance_id.
        limit        — max approvals to park this run (default 5).
    """
    try:
        instance_id = params.get("instance_id") or Config.instance_id
        limit_raw = params.get("limit")
        limit = int(limit_raw) if limit_raw is not None else _MAX_PER_RUN

        await _db.init_db()
        docket = await _utility.find_dormant(instance_id)
        if not docket:
            return "review_capabilities: no dormant capabilities"

        proposed: list = []
        skipped_seen = 0
        for row in docket:
            name = row["capability"]
            key = _SEEN_PREFIX + name
            if await _db.get_watch_state(instance_id, key):
                skipped_seen += 1
                continue
            if len(proposed) >= limit:
                break

            state = row["state"]
            if state == _utility.STATE_UNUSED:
                title = f"retire '{name}' — never used"
            else:
                title = f"retire '{name}' — only Lhassa itself uses it"

            payload = {
                "accion": "retire_capability",
                "capability": name,
                "state": state,
                "total_uses": row["total_uses"],
                "owner_uses": row["owner_uses"],
                "self_uses": row["self_uses"],
                "days_since_owner_use": row["days_since_owner_use"],
                "last_use_at": row["last_use_at"],
                "title": title,
            }
            await _approval.request_approval(
                instance_id, tipo="retire_capability", payload=payload
            )
            await _db.set_watch_state(instance_id, key, "1")
            proposed.append(name)

        remaining = len(docket) - len(proposed) - skipped_seen
        if proposed:
            msg = (
                f"utility tribunal: {len(proposed)} capability(ies) have not "
                f"earned their place — {', '.join(proposed)}. "
                "Approve to record the retirement, or pin them via "
                "LHASSA_UTILITY_PINNED."
            )
            if remaining > 0:
                msg += f" ({remaining} more deferred to next sweep.)"
            await _notify(msg, level="info")

        parts = [f"review_capabilities: {len(docket)} dormant"]
        parts.append(f"{len(proposed)} proposed")
        if skipped_seen:
            parts.append(f"{skipped_seen} already on the docket")
        if remaining > 0:
            parts.append(f"{remaining} deferred")
        return ", ".join(parts)

    except Exception as exc:  # an action must NEVER raise
        return f"review_capabilities: error: {exc}"
