# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runs screen — a DataTable of recent durable runs across all entities.

Now a :class:`~lhassa.cli.tui.screens.base.TableScreen`: it gains the ``/``
substring filter and the live re-poll for free, and keeps its direct
``core/db.py`` read (works with the API server down).
"""

from __future__ import annotations

from lhassa.cli.tui.screens.base import EMPTY, Row, TableScreen


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


class RunsScreen(TableScreen):
    """A DataTable of recent runs (id/kind/entity/status/cost/evidence)."""

    COLUMNS = ("id", "kind", "entity", "status", "cost", "evidence")
    TABLE_ID = "runs-table"
    EMPTY_TEXT = "no runs recorded yet"
    FILTER_PLACEHOLDER = "filter runs…"

    async def load_rows(self) -> list[Row]:
        out: list[Row] = []
        for row in await _load_runs(self._instance_id):
            cost = row.get("cost_usd") or 0.0
            try:
                cost = float(cost)
            except (TypeError, ValueError):
                cost = 0.0
            out.append(
                (
                    str(row.get("id")),
                    (
                        str(row.get("id") or EMPTY),
                        str(row.get("kind") or EMPTY),
                        str(row.get("entity_id") or EMPTY),
                        str(row.get("status") or EMPTY),
                        f"${cost:.4f}",
                        _evidence_of(row),
                    ),
                )
            )
        return out
