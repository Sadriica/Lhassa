# SPDX-License-Identifier: AGPL-3.0-or-later
"""Org screen; the command chart, read straight off ``org/topology.py``.

One row per brain, indented by depth so the tree reads as a tree while still
being a filterable DataTable. Lhassa (the permanent root administrator) is
marked. Read-only: the chart is mutated through ``lhassa org`` / the API, which
validate the tree; this screen never writes.

The org is actually TWO graphs: this table is the command TREE; a typed overlay
of RELATIONSHIP edges (``org/relationships.py``: reads/notifies/reviews/
collaborates/shares) is a separate graph layered on top, not hierarchy. A nested
detail panel below the table shows the edges touching the SELECTED brain, same
pattern as Projects' runs pane / Flows' runs pane.

An "activity" column sources a $0 aggregate SQL read over the episode log
(``dominio`` is the brain that produced an episode) so the chart shows who
actually WORKED recently, not just who exists.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from lhassa.cli.tui.screens.base import EMPTY, Row, TableScreen

# Keys in a node's opaque ``limits`` mapping that are actually LIMIT-shaped
# (budget/token/cost/rate/max_* caps). Anything else stashed there (e.g. a
# human ``description``, see ``org/monitor.py:make_brain_monitor``) is metadata,
# not a limit, and must not render under a header that promises limits.
_LIMIT_KEY_MARKERS = ("budget", "token", "cost", "rate")


def _is_limit_key(key: str) -> bool:
    key = (key or "").lower()
    return key.startswith("max_") or any(marker in key for marker in _LIMIT_KEY_MARKERS)


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


def _describe(node) -> str:
    """The node's human description, if one was stashed in ``limits``."""
    limits = getattr(node, "limits", None) or {}
    desc = limits.get("description")
    return str(desc) if desc else ""


def _limits_summary(node) -> str:
    limits = getattr(node, "limits", None) or {}
    shown = {k: v for k, v in limits.items() if _is_limit_key(k)}
    return ", ".join(f"{k}={v}" for k, v in shown.items()) or EMPTY


async def _load_activity(instance_id: str) -> dict[str, dict]:
    """Per-brain ``{dominio: {"last": iso, "count": n}}`` over the last 7 days."""
    from lhassa.core import db

    try:
        await db.init_db()
        return await db.dominio_activity(instance_id)
    except Exception:
        return {}


def _format_activity(entry: "dict | None") -> str:
    if not entry:
        return EMPTY
    count = int(entry.get("count") or 0)
    last = entry.get("last") or ""
    if not last and not count:
        return EMPTY
    when = _relative_time(last) if last else EMPTY
    return f"{count} (7d) · {when}" if last else f"{count} (7d)"


def _relative_time(iso_ts: str) -> str:
    from datetime import datetime, timezone

    try:
        ts = iso_ts.replace("Z", "+00:00") if isinstance(iso_ts, str) else ""
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        seconds = delta.total_seconds()
        if seconds < 0:
            seconds = 0
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        return f"{int(seconds // 86400)}d ago"
    except Exception:
        return EMPTY


def _load_relationships():
    """All overlay edges. Degrades to ``[]`` on a missing/broken relationships file."""
    from lhassa.org import relationships as rel_mod

    try:
        return rel_mod.all()
    except Exception:
        return []


class OrgScreen(TableScreen):
    """The org chart: brain, role, department, skills, limits, activity;
    depth-indented; plus a relationship-overlay panel for the selected brain."""

    COLUMNS = ("brain", "role", "department", "skills", "limits", "activity (7d)")
    TABLE_ID = "org-table"
    EMPTY_TEXT = "no brains in the org chart yet"
    FILTER_PLACEHOLDER = "filter brains…"
    # The topology is a file on disk, not a hot table; poll it gently.
    REFRESH_SECONDS = 8.0

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(instance_id, **kwargs)
        self._selected_brain = ""
        self._descriptions: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield Static(
            "Relationships; select a brain above", id="org-relationships-label"
        )
        rel_table = DataTable(id="org-relationships-table", cursor_type="row")
        rel_table.add_columns("direction", "kind", "brain", "note")
        yield rel_table
        yield Static("", id="org-relationships-legend", classes="status")

    async def load_rows(self) -> list[Row]:
        out: list[Row] = []
        activity = await _load_activity(self._instance_id)
        self._descriptions = {}
        for depth, node in _load_chart():
            prefix = ("  " * depth) + ("└ " if depth else "")
            desc = _describe(node)
            if desc:
                self._descriptions[node.id] = desc
            out.append(
                (
                    node.id,
                    (
                        f"{prefix}{node.id}",
                        str(getattr(node, "role", "") or EMPTY),
                        str(getattr(node, "department", None) or EMPTY),
                        ", ".join(getattr(node, "skills", ()) or ()) or EMPTY,
                        _limits_summary(node),
                        _format_activity(activity.get(node.id)),
                    ),
                )
            )
        return out

    async def action_refresh(self) -> None:
        await super().action_refresh()
        # The collapse must not wait for a selection: the owner opens this tab
        # to read the CHART, and on a machine with no overlay edges the panel
        # would otherwise hold half the screen until they clicked something.
        self._sync_panel_visibility()
        if self._selected_brain:
            self._render_relationships(self._selected_brain)

    def _sync_panel_visibility(self) -> None:
        """Collapse the overlay panel while no relationship edges exist."""
        empty = not _load_relationships()
        self.set_class(empty, "-no-relationships")
        if empty and not self._selected_brain:
            try:
                self.query_one("#org-relationships-legend", Static).update(
                    "[dim]no relationships defined; brains are linked only by "
                    "the command tree[/]"
                )
            except Exception:
                return

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != self.TABLE_ID:
            return
        self._selected_brain = str(event.row_key.value or "")
        self._render_relationships(self._selected_brain)

    def _render_relationships(self, brain_id: str) -> None:
        try:
            label = self.query_one("#org-relationships-label", Static)
            rel_table = self.query_one("#org-relationships-table", DataTable)
            legend = self.query_one("#org-relationships-legend", Static)
        except Exception:
            return

        all_edges = _load_relationships()
        self.set_class(not all_edges, "-no-relationships")
        desc = self._descriptions.get(brain_id, "")
        title = f"Relationships; [accent]{brain_id}[/]"
        if desc:
            title += f"  ({desc})"
        label.update(title)

        rel_table.clear()
        if not all_edges:
            legend.update(
                "[dim]no relationships defined; brains are linked only by the "
                "command tree[/]"
            )
            return

        from lhassa.org import relationships as rel_mod

        edges = rel_mod.list_for(brain_id)
        rows: list[tuple[str, str, str, str]] = []
        for edge in edges.get("outgoing", []):
            other = edge.get("to") if edge.get("from") == brain_id else edge.get("from")
            rows.append(("→", edge.get("kind", EMPTY), other or EMPTY, edge.get("note") or EMPTY))
        for edge in edges.get("incoming", []):
            other = edge.get("from") if edge.get("to") == brain_id else edge.get("to")
            row = ("←", edge.get("kind", EMPTY), other or EMPTY, edge.get("note") or EMPTY)
            if row not in rows:
                rows.append(row)

        if not rows:
            legend.update(f"[dim]no relationships touch '{brain_id}'[/]")
        else:
            for direction, kind, other, note in rows:
                rel_table.add_row(direction, kind, other, note)
            counts: dict[str, int] = {}
            for rel in all_edges:
                counts[rel.kind] = counts.get(rel.kind, 0) + 1
            summary = ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
            legend.update(f"[dim]org-wide: {summary}[/]")
