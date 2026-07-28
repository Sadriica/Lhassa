"""Projects screen — projects table + a detail pane of that project's recent runs.

``entity_id = lhassa.core.projects.entity_tag(project_id)`` is the ONE spelling
used to scope ``runs`` to a project (see ``core/projects.py``). The evidence
column renders through ``lhassa.memory.evidence.evidence_state`` — the same
humblest-first claim ladder used elsewhere.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

_EMPTY = "—"


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
        return await db.list_runs(
            instance_id, entity_id=entity_tag(project_id), limit=50
        )
    except Exception:
        return []


def _evidence_of(row: dict) -> str:
    from lhassa.memory.evidence import evidence_state

    try:
        return evidence_state(row)
    except Exception:
        return "prepared"


class ProjectsScreen(Vertical):
    """Top: a DataTable of projects. Bottom: recent runs for the selected one."""

    DEFAULT_CSS = """
    ProjectsScreen {
        padding: 1 2;
    }
    ProjectsScreen #projects-table {
        height: 1fr;
    }
    ProjectsScreen #runs-label {
        padding: 1 0 0 0;
    }
    ProjectsScreen #project-runs-table {
        height: 1fr;
    }
    """

    BINDINGS = [("r", "refresh", "Refresh")]

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._instance_id = instance_id
        self._project_ids: list[str] = []

    def compose(self) -> ComposeResult:
        table = DataTable(id="projects-table", cursor_type="row")
        table.add_columns("name", "kind", "status", "path")
        yield table
        yield Static("Recent runs — select a project above", id="runs-label")
        runs_table = DataTable(id="project-runs-table", cursor_type="row")
        runs_table.add_columns("id", "kind", "status", "cost", "evidence")
        yield runs_table

    async def on_mount(self) -> None:
        await self.action_refresh()

    async def action_refresh(self) -> None:
        rows = await _load_projects(self._instance_id)
        table = self.query_one("#projects-table", DataTable)
        table.clear()
        self._project_ids = []
        for row in rows:
            table.add_row(
                str(row.get("name") or _EMPTY),
                str(row.get("kind") or _EMPTY),
                str(row.get("status") or _EMPTY),
                str(row.get("path") or _EMPTY),
                key=str(row.get("id")),
            )
            self._project_ids.append(str(row.get("id")))

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "projects-table":
            return
        project_id = str(event.row_key.value)
        await self._load_runs(project_id)

    async def _load_runs(self, project_id: str) -> None:
        label = self.query_one("#runs-label", Static)
        label.update(f"Recent runs — [accent]{project_id}[/]")
        runs = await _load_runs_for_project(self._instance_id, project_id)
        runs_table = self.query_one("#project-runs-table", DataTable)
        runs_table.clear()
        for run in runs:
            cost = run.get("cost_usd") or 0.0
            try:
                cost = float(cost)
            except (TypeError, ValueError):
                cost = 0.0
            runs_table.add_row(
                str(run.get("id") or _EMPTY),
                str(run.get("kind") or _EMPTY),
                str(run.get("status") or _EMPTY),
                f"${cost:.4f}",
                _evidence_of(run),
                key=str(run.get("id")),
            )
