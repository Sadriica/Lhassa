# SPDX-License-Identifier: AGPL-3.0-or-later
"""Approvals screen — the pending queue, rendered through the ONE describe()
helper (never bare ids), with in-place approve/deny.

``a``/``d`` resolve the selected row via ``lhassa.security.approval.resolve``
— the SAME seam the CLI/API use — never bypassed. A confirmation line renders
in the footer status and the queue reloads after every resolution.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

_EMPTY = "—"


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


class ApprovalsScreen(Vertical):
    """A DataTable of pending approvals with ``a``/``d`` resolve keybindings."""

    DEFAULT_CSS = """
    ApprovalsScreen {
        padding: 1 2;
    }
    ApprovalsScreen #approvals-table {
        height: 1fr;
    }
    ApprovalsScreen #approvals-status {
        padding: 1 0 0 0;
    }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("a", "approve_selected", "Approve"),
        ("d", "deny_selected", "Deny"),
    ]

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._instance_id = instance_id

    def compose(self) -> ComposeResult:
        table = DataTable(id="approvals-table", cursor_type="row")
        table.add_columns("id", "title", "detail", "creado_en")
        yield table
        yield Static("", id="approvals-status")

    async def on_mount(self) -> None:
        await self.action_refresh()

    async def action_refresh(self) -> None:
        rows = await _load_approvals(self._instance_id)
        table = self.query_one("#approvals-table", DataTable)
        table.clear()
        for row in rows:
            desc = _describe(row)
            table.add_row(
                str(row.get("id") or _EMPTY),
                str(desc.get("title") or _EMPTY),
                str(desc.get("detail") or _EMPTY),
                str(row.get("creado_en") or _EMPTY),
                key=str(row.get("id")),
            )

    def _selected_approval_id(self) -> "str | None":
        table = self.query_one("#approvals-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        return None if row_key.value is None else str(row_key.value)

    async def _resolve_selected(self, approved: bool) -> None:
        from lhassa.security import approval

        status = self.query_one("#approvals-status", Static)
        approval_id = self._selected_approval_id()
        if not approval_id:
            status.update("[dim]No approval selected.[/]")
            return
        verb = "approved" if approved else "denied"
        try:
            await approval.resolve(
                self._instance_id, approval_id, approved=approved, by_actor="tui"
            )
            status.update(f"[success]✓[/] {verb} [model]{approval_id}[/]")
        except Exception as exc:  # a resolution failure must never crash the TUI
            status.update(f"[error]✗[/] could not resolve [model]{approval_id}[/]: {exc}")
        await self.action_refresh()

    async def action_approve_selected(self) -> None:
        await self._resolve_selected(True)

    async def action_deny_selected(self) -> None:
        await self._resolve_selected(False)
