# SPDX-License-Identifier: AGPL-3.0-or-later
"""Approvals screen; the pending queue, rendered through the ONE describe()
helper (never bare ids), with in-place approve/deny.

``a``/``d`` resolve the selected row via ``lhassa.security.approval.resolve``
(the SAME seam the CLI/API use; never bypassed). A confirmation line renders
in the screen's status line and the queue reloads after every resolution.

A :class:`~lhassa.tui.screens.base.TableScreen`, so the queue also gets the
``/`` filter and the live re-poll (a new approval parked by the heartbeat shows
up without a keypress).
"""

from __future__ import annotations

from lhassa.tui.screens.base import EMPTY, Row, TableScreen


async def _load_approvals(instance_id: str) -> list[dict]:
    from lhassa.core import db

    try:
        await db.init_db()
        return await db.load_pending_approvals(instance_id)
    except Exception:
        return []


def _describe(row: dict) -> dict:
    from lhassa.security.approval import describe

    try:
        return describe(row)
    except Exception:
        return {"title": str(row.get("tipo") or "?"), "detail": ""}


class ApprovalsScreen(TableScreen):
    """A DataTable of pending approvals with ``a``/``d`` resolve keybindings."""

    COLUMNS = ("id", "title", "detail", "created")
    TABLE_ID = "approvals-table"
    EMPTY_TEXT = "no approvals pending"
    FILTER_PLACEHOLDER = "filter approvals…"

    BINDINGS = [
        ("a", "approve_selected", "Approve"),
        ("d", "deny_selected", "Deny"),
    ]

    async def load_rows(self) -> list[Row]:
        out: list[Row] = []
        for row in await _load_approvals(self._instance_id):
            desc = _describe(row)
            out.append(
                (
                    str(row.get("id")),
                    (
                        str(row.get("id") or EMPTY),
                        str(desc.get("title") or EMPTY),
                        str(desc.get("detail") or EMPTY),
                        str(row.get("creado_en") or EMPTY),
                    ),
                )
            )
        return out

    async def _resolve_selected(self, approved: bool) -> None:
        from lhassa.security import approval

        approval_id = self.selected_key()
        if not approval_id:
            self.set_status("[dim]No approval selected.[/]")
            return
        verb = "approved" if approved else "denied"
        try:
            await approval.resolve(
                self._instance_id,
                approval_id,
                approved=approved,
                by_actor="tui",
                approver=approval.TrustedApprover("owner", "local-process"),
            )
            self.set_status(f"[success]✓[/] {verb} [model]{approval_id}[/]")
        except Exception as exc:  # a resolution failure must never crash the TUI
            self.set_status(f"[error]✗[/] could not resolve [model]{approval_id}[/]: {exc}")
        await self.action_refresh()

    async def action_approve_selected(self) -> None:
        await self._resolve_selected(True)

    async def action_deny_selected(self) -> None:
        await self._resolve_selected(False)
