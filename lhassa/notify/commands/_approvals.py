# SPDX-License-Identifier: AGPL-3.0-or-later
"""Approve/deny from chat — /approvals /approve /deny.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

from loguru import logger

from ._registry import register_command
from ._shared import _approval_age, _config, _oops


@register_command("approvals")
async def _cmd_approvals(args: str, *, config=None) -> str:
    """List pending approvals — LLM-free; renders ``security.approval.describe``
    so the owner sees WHAT each one is (not just an id) plus its age and the
    exact commands to resolve it."""
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db
        from lhassa.security import approval as _approval

        await _db.init_db()
        rows = await _approval.pending(instance_id)
        if not rows:
            return "No pending approvals."

        lines = [f"Pending approvals ({len(rows)}):"]
        for i, row in enumerate(rows[:10], start=1):
            desc = _approval.describe(row)
            age = _approval_age(row.get("creado_en"))
            lines.append("")
            lines.append(f"{i}. {desc['title']}{f' — {age}' if age else ''}")
            if desc.get("detail"):
                lines.append(f"   {desc['detail']}")
            lines.append(f"   /approve {row.get('id', '?')} · /deny {row.get('id', '?')}")
        if len(rows) > 10:
            lines.append(f"\n…+{len(rows) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /approvals failed: {exc}")
        return _oops("open the approvals queue")


async def _resolve_from_chat(args: str, *, config, approved: bool) -> str:
    """Shared body for ``/approve`` and ``/deny`` — resolves one approval by id.

    Mirrors what ``api/approval_routes.py``'s POST endpoint does
    (``security.approval.resolve``), attributed to the ``"telegram"`` actor.
    """
    from lhassa.security import approval as _approval

    verb = "approve" if approved else "deny"
    ap_id = (args or "").strip().split()[0] if (args or "").strip() else ""
    if not ap_id:
        return f"Usage: /{verb} <id> — see /approvals for pending ids."
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db
        await _db.init_db()
        existing = await _db.get_approval(instance_id, ap_id)
        if existing is None:
            return f"No pending approval with id {ap_id!r}."
        await _approval.resolve(
            instance_id,
            ap_id,
            approved=approved,
            by_actor="telegram",
            approver=_approval.TrustedApprover("owner", "telegram-owner"),
        )

        # A resolved mint_route approval TURNS ON that capability's route —
        # same approval->apply connection as the inline-keyboard callback path
        # in lhassa.notify.telegram_inbound._handle_callback_query.
        if approved and existing.get("tipo") == "mint_route":
            from lhassa.notify import capabilities as _capabilities

            _capabilities.apply_approved_route(
                existing.get("payload") or {}, config=cfg
            )

        return f"Approval {ap_id} {'approved' if approved else 'denied'}."
    except _approval.ApprovalAuthorizationError as exc:
        return f"Approval refused: {exc}"
    except Exception as exc:
        logger.warning(f"commands: /{verb} {ap_id} failed: {exc}")
        return _oops(f"resolve approval {ap_id}")


@register_command("approve")
async def _cmd_approve(args: str, *, config=None) -> str:
    """``/approve <id>`` — approve a pending action from chat."""
    return await _resolve_from_chat(args, config=config, approved=True)


@register_command("deny")
async def _cmd_deny(args: str, *, config=None) -> str:
    """``/deny <id>`` — deny a pending action from chat."""
    return await _resolve_from_chat(args, config=config, approved=False)
