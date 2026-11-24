# SPDX-License-Identifier: AGPL-3.0-or-later
"""
review_capabilities; the utility tribunal's weekly sitting (deterministic, $0).

The counterweight action to ``review_tools``/``curate_skills``: where those ask
whether a TOOL still works or a SKILL still gets read, this asks whether a
CAPABILITY still serves the owner. Detection lives in
:mod:`lhassa.agents.utility` (a pure derived read over the episode log, no LLM,
no new storage); this module turns each finding into ONE parked approval and a
single calm notification.

It never retires anything. A registered action lives in code; approving a
``retire_capability`` approval only records the capability in the
``retired_candidates`` list (``lhassa utility candidates``), and REJECTING it
pins the capability so it is never asked about again (see
``security.approval.resolve``). Detection never acts, the owner's tap records.

Design: ONE approval PER capability (not one summary per sweep). A summary row
would need partial resolution (some revived, some pinned) and a per-capability
payload anyway; a row per capability keeps approve = candidate, reject = pin,
revived/gone = close, each atomic and testable. The flood is bounded instead:
at most ``limit`` (5) per sweep and never beyond ``Config.approval_pending_cap``
pending ``retire_capability`` rows.
"""

from __future__ import annotations

from lhassa.agents import utility as _utility
from lhassa.config import Config
from lhassa.core import db as _db
from lhassa.notify.dispatch import notify as _notify
from lhassa.security import approval as _approval

def _door_names() -> set:
    """Doors that live in the CLI shell: subcommands and chat tools -> actions."""
    from lhassa.cli.agent import _TOOL_CAPABILITY
    from lhassa.cli.capability_log import COMMAND_CAPABILITY

    return {str(v) for v in COMMAND_CAPABILITY.values()} | {
        str(v) for v in _TOOL_CAPABILITY.values()
    }


def _seeded_names() -> set:
    """What the maintenance seed schedules plus what the temple may front."""
    from lhassa.actionpack._temple import TEMPLE_ACTIONS
    from lhassa.seeds.maintenance import _RECURRING_ACTIONS

    return {a for a, _ in _RECURRING_ACTIONS} | set(TEMPLE_ACTIONS)


_utility.register_door_source(_door_names)
_utility.register_seeded_source(_seeded_names)

# watch_state key prefix: a capability is put on the docket ONCE, not on every
# weekly sweep; the same transition-dedup idiom detect_repetition uses.
_SEEN_PREFIX = "utility:proposed:"

# Never park more than this many approvals in a single sweep. The tribunal is
# meant to be calm: the docket is sorted least-earned-first, so the cap keeps
# the worst offenders and defers the rest to next week rather than flooding the
# queue on first run. A truncated docket is REPORTED, never silently dropped.
_MAX_PER_RUN = 5


def _title(row: dict) -> str:
    """The approval title, derived from the real state and counts (never a guess)."""
    name = row["capability"]
    state = row["state"]
    owner = int(row.get("owner_uses") or 0)
    selfu = int(row.get("self_uses") or 0)
    days = row.get("days_since_owner_use")
    if state == _utility.STATE_UNUSED:
        return f"retire '{name}': never used by anyone"
    if state == _utility.STATE_DORMANT_OWNER or owner > 0:
        ago = f", last {float(days):.0f}d ago" if days is not None else ""
        return f"retire '{name}': you used it {owner}x{ago}, nothing recent"
    return f"retire '{name}': only Lhassa itself used it ({selfu}x), never you"


async def _close_resolved(instance_id: str, alive: dict, registered: set) -> dict:
    """Close pending retire_capability approvals whose premise no longer holds.

    ``alive`` maps capability -> survey state. A capability that turned
    ``active`` again (the owner used it) closes ``rejected-by-revived``; one no
    longer in the ACTIONS registry closes ``rejected-by-gone``. Neither is a
    human decision (no pin, no autonomy streak), and the dedup key is cleared so
    a later relapse can be proposed afresh.
    """
    closed = {"revived": 0, "gone": 0}
    for row in await _db.load_pending_approvals(instance_id):
        if row.get("tipo") != "retire_capability":
            continue
        cap = (row.get("payload") or {}).get("capability")
        if not cap:
            continue
        why = None
        if registered and cap not in registered:
            why = "gone"
        elif alive.get(cap) == _utility.STATE_ACTIVE:
            why = "revived"
        if why and await _db.close_approval(instance_id, row["id"], f"rejected-by-{why}"):
            await _db.set_watch_state(instance_id, _SEEN_PREFIX + cap, "")
            closed[why] += 1
    return closed


async def review_capabilities(params: dict) -> str:
    """Report capabilities that no owner-driven use exercises; propose retiring them.

    Runs ``agents.utility.find_dormant`` ($0 SQL over episodes + the live
    ACTIONS registry). First closes pending proposals that revived or vanished,
    then, for each NEW finding (deduped via ``watch_state``), parks a
    ``retire_capability`` approval and, if anything was parked, sends ONE
    summary notification. Never parks past ``Config.approval_pending_cap``
    pending rows of the type. Never raises; never calls an LLM.

    params:
        instance_id ; defaults to Config.instance_id.
        limit       ; max approvals to park this run (default 5).
    """
    try:
        instance_id = params.get("instance_id") or Config.instance_id
        limit_raw = params.get("limit")
        limit = int(limit_raw) if limit_raw is not None else _MAX_PER_RUN

        await _db.init_db()

        survey_rows = await _utility.survey(instance_id)
        registered = set(_utility._registered_capabilities())
        closed = await _close_resolved(
            instance_id, {r["capability"]: r["state"] for r in survey_rows}, registered
        )

        docket = await _utility.find_dormant(instance_id)
        closed_note = ""
        if closed["revived"] or closed["gone"]:
            closed_note = (
                f"; closed {closed['revived']} revived, {closed['gone']} gone"
            )
        if not docket:
            return "review_capabilities: no dormant capabilities" + closed_note

        cap = int(getattr(Config, "approval_pending_cap", 0) or 0)
        if cap > 0:
            pending = await _approval.pending_count(instance_id, "retire_capability")
            if pending >= cap:
                return (
                    f"review_capabilities: {len(docket)} dormant, 0 proposed; "
                    f"{pending} retire_capability already pending (cap {cap})"
                    + closed_note
                )
            limit = min(limit, cap - pending)

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

            payload = {
                "accion": "retire_capability",
                "capability": name,
                "state": row["state"],
                "total_uses": row["total_uses"],
                "owner_uses": row["owner_uses"],
                "self_uses": row["self_uses"],
                "days_since_owner_use": row["days_since_owner_use"],
                "last_use_at": row["last_use_at"],
                "last_owner_use_at": row.get("last_owner_use_at"),
                "dormant_days": int(getattr(Config, "utility_dormant_days", 30) or 30),
                "title": _title(row),
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
                f"earned their place; {', '.join(proposed)}. "
                "Approving records a retirement candidate (nothing is removed); "
                "rejecting pins it so it is not asked again."
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
        return ", ".join(parts) + closed_note

    except Exception as exc:  # an action must NEVER raise
        return f"review_capabilities: error: {exc}"
