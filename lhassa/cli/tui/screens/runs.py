# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runs screen — a DataTable of recent durable runs across all entities."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable

_EMPTY = "—"


async def _load_runs(instance_id: str) -> list[dict]:
    from lhassa.core import db

    try:
        await db.init_db()
        return await db.list_runs(instance_id, limit=200)
    except Exception:
        return []


def _evidence_of(row: dict) -> str:
    from lhassa.memory.evidence import evidence_state

    try:
        return evidence_state(row)
    except Exception:
        return "prepared"


class RunsScreen(Vertical):
    """A DataTable of recent runs (id/kind/entity/status/cost/evidence)."""

    DEFAULT_CSS = """
    RunsScreen {
        padding: 1 2;
    }
    RunsScreen #runs-table {
        height: 1fr;
    }
    """

    BINDINGS = [("r", "refresh", "Refresh")]

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._instance_id = instance_id

    def compose(self) -> ComposeResult:
        table = DataTable(id="runs-table", cursor_type="row")
        table.add_columns("id", "kind", "entity", "status", "cost", "evidence")
        yield table

    async def on_mount(self) -> None:
        await self.action_refresh()

    async def action_refresh(self) -> None:
        rows = await _load_runs(self._instance_id)
        table = self.query_one("#runs-table", DataTable)
        table.clear()
        for row in rows:
            cost = row.get("cost_usd") or 0.0
            try:
                cost = float(cost)
            except (TypeError, ValueError):
                cost = 0.0
            table.add_row(
                str(row.get("id") or _EMPTY),
                str(row.get("kind") or _EMPTY),
                str(row.get("entity_id") or _EMPTY),
                str(row.get("status") or _EMPTY),
                f"${cost:.4f}",
                _evidence_of(row),
                key=str(row.get("id")),
            )
