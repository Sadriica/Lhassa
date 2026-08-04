# SPDX-License-Identifier: AGPL-3.0-or-later
"""Org screen — the command chart, read straight off ``org/topology.py``.

One row per brain, indented by depth so the tree reads as a tree while still
being a filterable DataTable. Lhassa (the permanent root administrator) is
marked. Read-only: the chart is mutated through ``lhassa org`` / the API, which
validate the tree; this screen never writes.
"""

from __future__ import annotations

from lhassa.cli.tui.screens.base import EMPTY, Row, TableScreen


def _walk(topology, node_id: str, depth: int, out: list[tuple[int, object]]) -> None:
    try:
        node = topology.node(node_id)
    except Exception:
        return
    out.append((depth, node))
    for child in topology.children_of(node_id):
        _walk(topology, child.id, depth + 1, out)


def _load_chart() -> list[tuple[int, object]]:
    from lhassa.org import topology as topo_mod

    out: list[tuple[int, object]] = []
    try:
        topology = topo_mod.load_topology()
        _walk(topology, topology.root().id, 0, out)
    except Exception:
        return []
    return out


class OrgScreen(TableScreen):
    """The org chart: brain, role, department, skills, limits — depth-indented."""

    COLUMNS = ("brain", "role", "department", "skills", "limits")
    TABLE_ID = "org-table"
    FILTER_PLACEHOLDER = "filter brains…"
    # The topology is a file on disk, not a hot table — poll it gently.
    REFRESH_SECONDS = 8.0

    async def load_rows(self) -> list[Row]:
        out: list[Row] = []
        for depth, node in _load_chart():
            prefix = ("  " * depth) + ("└ " if depth else "")
            limits = getattr(node, "limits", None) or {}
            out.append(
                (
                    node.id,
                    (
                        f"{prefix}{node.id}",
                        str(getattr(node, "role", "") or EMPTY),
                        str(getattr(node, "department", None) or EMPTY),
                        ", ".join(getattr(node, "skills", ()) or ()) or EMPTY,
                        ", ".join(f"{k}={v}" for k, v in limits.items()) or EMPTY,
                    ),
                )
            )
        return out
