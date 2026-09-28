# SPDX-License-Identifier: AGPL-3.0-or-later
"""Projects screen; projects table + a detail pane of that project's recent runs.

``entity_id = lhassa.core.projects.entity_tag(project_id)`` is the ONE spelling
used to scope ``runs`` to a project (see ``core/projects.py``). The evidence
column renders through ``lhassa.memory.evidence.evidence_state``: the same
humblest-first claim ladder used elsewhere.

A :class:`~lhassa.cli.tui.screens.base.TableScreen`, so the project list gains
the ``/`` filter and the live re-poll; the runs pane refreshes with it.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from lhassa.cli.tui.screens.base import EMPTY, Row, TableScreen


async def _load_projects(instance_id: str) -> list[dict]:
    from lhassa.core import db

    try:
        await db.init_db()
        return await db.list_project_rows(instance_id, status=None)
    except Exception:
        return []


async def _load_runs_for_project(instance_id: str, project_id: str) -> list[dict]:
    from lhassa.core import db
    from lhassa.core.projects import entity_tag

    try:
        await db.init_db()
        return await db.list_runs(instance_id, entity_id=entity_tag(project_id), limit=50)
    except Exception:
        return []


def _evidence_of(row: dict) -> str:
    from lhassa.memory.evidence import evidence_state

    try:
        return evidence_state(row)
    except Exception:
        return "prepared"


class ProjectsScreen(TableScreen):
    """Top: a DataTable of projects. Bottom: recent runs for the selected one."""

    COLUMNS = ("name", "kind", "status", "path")
    TABLE_ID = "projects-table"
    EMPTY_TEXT = "no projects yet"
    FILTER_PLACEHOLDER = "filter projects…"

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(instance_id, **kwargs)
        self._selected_project = ""

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield Static("Recent runs; select a project above", id="runs-label")
        runs_table = DataTable(id="project-runs-table", cursor_type="row")
        runs_table.add_columns("id", "kind", "status", "cost", "evidence")
        yield runs_table

    async def load_rows(self) -> list[Row]:
        out: list[Row] = []
        for row in await _load_projects(self._instance_id):
            out.append(
                (
                    str(row.get("id")),
                    (
                        str(row.get("name") or EMPTY),
                        str(row.get("kind") or EMPTY),
                        str(row.get("status") or EMPTY),
                        str(row.get("path") or EMPTY),
                    ),
                )
            )
        return out

    async def action_refresh(self) -> None:
        await super().action_refresh()
        if self._selected_project:
            await self._load_runs(self._selected_project)

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != self.TABLE_ID:
            return
        self._selected_project = str(event.row_key.value or "")
        await self._load_runs(self._selected_project)

    async def _load_runs(self, project_id: str) -> None:
        try:
            label = self.query_one("#runs-label", Static)
            runs_table = self.query_one("#project-runs-table", DataTable)
        except Exception:
            return
        label.update(f"Recent runs; [accent]{project_id}[/]")
        runs = await _load_runs_for_project(self._instance_id, project_id)
        runs_table.clear()
        for run in runs:
            cost = run.get("cost_usd") or 0.0
            try:
                cost = float(cost)
            except (TypeError, ValueError):
                cost = 0.0
            runs_table.add_row(
                str(run.get("id") or EMPTY),
                str(run.get("kind") or EMPTY),
                str(run.get("status") or EMPTY),
                f"${cost:.4f}",
                _evidence_of(run),
                key=str(run.get("id")),
            )
