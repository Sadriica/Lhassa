# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flows screen — stored flow definitions on top, their durable runs below.

Reads go straight to :class:`~lhassa.flows.store.FlowStore` (the flows file) and
the durable kernel (:class:`~lhassa.flows.runtime.FlowRuntimeRepository`), so the
screen works with the API server down.

The one MUTATION here — ``x`` runs the selected flow — goes through
:func:`lhassa.org.delegate.delegate_flow`, exactly like ``lhassa flow run`` and
``POST /api/flows/{id}/run``. A flow is never run bare from any surface.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from lhassa.cli.tui.screens.base import EMPTY, Row, TableScreen


def _load_flows() -> list:
    from lhassa.flows.store import FlowStore

    try:
        return FlowStore().list_flows()
    except Exception:
        return []


async def _load_runs(instance_id: str, flow_id: str = "") -> list[dict]:
    from lhassa.flows.runtime import FlowRuntimeRepository

    try:
        rows = await FlowRuntimeRepository().list_runs(instance_id, limit=100)
    except Exception:
        return []
    if flow_id:
        rows = [r for r in rows if str(r.get("flow_id")) == flow_id]
    return rows


class FlowsScreen(TableScreen):
    """Flow definitions (filterable) + the durable runs of the selected one."""

    COLUMNS = ("id", "name", "nodes", "edges", "description")
    TABLE_ID = "flows-table"
    EMPTY_TEXT = "no flows defined yet"
    FILTER_PLACEHOLDER = "filter flows…"

    BINDINGS = [("x", "run_selected", "Run flow")]

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(instance_id, **kwargs)
        self._selected_flow = ""

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield Static("Runs — select a flow above", id="flows-runs-label")
        runs = DataTable(id="flows-runs-table", cursor_type="row")
        runs.add_columns("run", "status", "checkpoint", "updated")
        yield runs

    async def load_rows(self) -> list[Row]:
        out: list[Row] = []
        for flow in _load_flows():
            out.append(
                (
                    flow.id,
                    (
                        flow.id,
                        flow.name or EMPTY,
                        str(len(flow.nodes)),
                        str(len(flow.edges)),
                        (flow.description or EMPTY).splitlines()[0][:80],
                    ),
                )
            )
        return out

    async def action_refresh(self) -> None:
        await super().action_refresh()
        await self._refresh_runs()

    async def _refresh_runs(self) -> None:
        rows = await _load_runs(self._instance_id, self._selected_flow)
        try:
            table = self.query_one("#flows-runs-table", DataTable)
        except Exception:
            return
        cursor = table.cursor_coordinate
        table.clear()
        for row in rows:
            table.add_row(
                str(row.get("run_id") or EMPTY),
                str(row.get("status") or EMPTY),
                str(row.get("checkpoint_seq") if row.get("checkpoint_seq") is not None else EMPTY),
                str(row.get("updated_at") or EMPTY),
                key=str(row.get("run_id")),
            )
        if rows:
            try:
                table.cursor_coordinate = (min(cursor.row, len(rows) - 1), cursor.column)
            except Exception:
                pass

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != self.TABLE_ID:
            return
        self._selected_flow = str(event.row_key.value or "")
        label = self.query_one("#flows-runs-label", Static)
        label.update(f"Runs — [accent]{self._selected_flow}[/]")
        await self._refresh_runs()

    async def action_run_selected(self) -> None:
        """Run the selected flow THROUGH the org hierarchy (never ``run_flow``)."""
        from lhassa.org.delegate import delegate_flow

        flow_id = self.selected_key()
        if not flow_id:
            self.set_status("[dim]No flow selected.[/]")
            return
        flow = next((f for f in _load_flows() if f.id == flow_id), None)
        if flow is None:
            self.set_status(f"[error]✗[/] flow [model]{flow_id}[/] is gone")
            return
        self.set_status(f"[dim]running[/] [model]{flow_id}[/] …")
        try:
            record = await delegate_flow(flow, durable=True)
        except Exception as exc:  # a failed run must never crash the TUI
            self.set_status(f"[error]✗[/] {flow_id}: {exc}")
            return
        self.set_status(
            f"[success]✓[/] run [model]{record.get('id')}[/] — {record.get('status')}"
        )
        self._selected_flow = flow_id
        await self._refresh_runs()
