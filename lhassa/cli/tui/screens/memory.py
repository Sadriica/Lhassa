# SPDX-License-Identifier: AGPL-3.0-or-later
"""Memory screen — the grande's facts, and FTS5 recall over the episode log.

The filter input does DOUBLE duty here, which is the point of the screen: it
substring-filters the facts table locally AND is handed to
``core/db.py:search_episodes`` (the BM25-ranked layer-5 recall) so the same
keystrokes ask "what do I know?" and "what did I do?" at once. Both are $0 and
read the local DB directly — no model, no API server.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from lhassa.cli.tui.screens.base import EMPTY, Row, TableScreen


async def _load_facts(instance_id: str) -> list[dict]:
    from lhassa.core import db

    try:
        await db.init_db()
        return await db.query_facts(instance_id)
    except Exception:
        return []


async def _recall(instance_id: str, query: str) -> list[dict]:
    from lhassa.core import db

    if not query:
        return []
    try:
        await db.init_db()
        return await db.search_episodes(instance_id, query, n=25)
    except Exception:
        return []


class MemoryScreen(TableScreen):
    """Facts on top (filterable); episodic recall for the same query below."""

    COLUMNS = ("tipo", "contenido", "tags", "conf", "creado_en")
    TABLE_ID = "memory-table"
    FILTER_PLACEHOLDER = "search memory (facts + episodic recall)…"

    DEFAULT_CSS = """
    MemoryScreen #memory-recall-label {
        padding: 1 0 0 0;
    }
    MemoryScreen #memory-recall-table {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield Static("Episodic recall — type a query with /", id="memory-recall-label")
        recall = DataTable(id="memory-recall-table", cursor_type="row")
        recall.add_columns("when", "trigger", "accion", "snippet")
        yield recall

    async def load_rows(self) -> list[Row]:
        out: list[Row] = []
        for row in await _load_facts(self._instance_id):
            tags = row.get("tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]
            pin = "📌 " if row.get("pinned") else ""
            out.append(
                (
                    str(row.get("id")),
                    (
                        str(row.get("tipo") or EMPTY),
                        pin + str(row.get("contenido") or EMPTY)[:90],
                        ", ".join(str(t) for t in tags) or EMPTY,
                        str(row.get("confianza") if row.get("confianza") is not None else EMPTY),
                        str(row.get("creado_en") or EMPTY),
                    ),
                )
            )
        # Newest first reads better than the DB's oldest-first insertion order.
        return list(reversed(out))

    async def action_refresh(self) -> None:
        await super().action_refresh()
        await self._refresh_recall()

    async def _refresh_recall(self) -> None:
        rows = await _recall(self._instance_id, self._filter)
        try:
            label = self.query_one("#memory-recall-label", Static)
            table = self.query_one("#memory-recall-table", DataTable)
        except Exception:
            return
        label.update(
            f"Episodic recall — [accent]{self._filter}[/] ({len(rows)})"
            if self._filter
            else "Episodic recall — type a query with /"
        )
        table.clear()
        for row in rows:
            table.add_row(
                str(row.get("creado_en") or EMPTY),
                str(row.get("despertar_por") or EMPTY),
                str(row.get("accion") or EMPTY),
                str(row.get("snippet") or row.get("decision") or EMPTY)[:80],
                key=str(row.get("id")),
            )
