# SPDX-License-Identifier: AGPL-3.0-or-later
"""``TableScreen`` — the one pattern every DataTable-driven TUI screen shares.

Three things every screen in ``lhassa tui`` gets for free by subclassing it:

* **Search / filter** — ``/`` focuses a one-line ``Input``; the rows are filtered
  by a case-insensitive SUBSTRING match over the joined cells (deliberately not
  fuzzy: a filter you can predict beats a filter that guesses). ``escape`` clears.
* **Live refresh** — a ``set_interval`` re-polls the source every
  :attr:`REFRESH_SECONDS` (default 4s: fresh enough to feel live, slow enough not
  to hammer SQLite). The redraw is SKIPPED when the rendered rows are unchanged,
  so an idle screen never flickers, and the cursor/scroll position is restored
  across a redraw that does happen.
* **Degradation** — :meth:`load_rows` failures render an empty table rather than
  crashing the app; the TUI must keep working with the API server (and half the
  brain) down.

Subclasses declare ``COLUMNS`` / ``TABLE_ID`` and implement :meth:`load_rows`,
which returns ``[(row_key, (cell, cell, …)), …]``. All data access stays DIRECT
(``core/db.py``, ``flows/store.py``, ``skills/store.py``, ``org/topology.py``);
mutations still cross the real seams (``security/approval.py:resolve``,
``org/delegate.py:delegate_flow``) — never raw SQL.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Input, Static

EMPTY = "—"

Row = tuple[str, tuple[str, ...]]


class TableScreen(Vertical):
    """A DataTable screen with a ``/`` substring filter and live re-polling."""

    #: Column headers, in order.
    COLUMNS: tuple[str, ...] = ()
    #: The DataTable's DOM id. The filter input becomes ``<TABLE_ID>-filter``.
    TABLE_ID = "table"
    #: Seconds between background re-polls (0 disables live refresh).
    REFRESH_SECONDS = 4.0
    #: Placeholder shown in the filter input.
    FILTER_PLACEHOLDER = "filter…"

    DEFAULT_CSS = """
    TableScreen {
        padding: 1 2;
    }
    TableScreen Input.filter {
        display: none;
        margin: 0 0 1 0;
    }
    TableScreen Input.filter.-visible {
        display: block;
    }
    TableScreen DataTable {
        height: 1fr;
    }
    TableScreen Static.status {
        padding: 1 0 0 0;
    }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("slash", "focus_filter", "Filter"),
        ("escape", "clear_filter", "Clear filter"),
    ]

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._instance_id = instance_id
        self._filter = ""
        self._signature: "tuple | None" = None
        self._timer = None

    # ── the subclass seam ─────────────────────────────────────────────────────
    async def load_rows(self) -> list[Row]:  # pragma: no cover - abstract
        """Return ``[(row_key, cells), …]``. Must never raise."""
        raise NotImplementedError

    # ── composition ───────────────────────────────────────────────────────────
    @property
    def filter_id(self) -> str:
        return f"{self.TABLE_ID}-filter"

    def compose(self) -> ComposeResult:
        yield Input(placeholder=self.FILTER_PLACEHOLDER, id=self.filter_id, classes="filter")
        table = DataTable(id=self.TABLE_ID, cursor_type="row")
        if self.COLUMNS:
            table.add_columns(*self.COLUMNS)
        yield table
        yield Static("", id=f"{self.TABLE_ID}-status", classes="status")

    async def on_mount(self) -> None:
        await self.action_refresh()
        if self.REFRESH_SECONDS > 0:
            self._timer = self.set_interval(self.REFRESH_SECONDS, self.action_refresh)

    # ── status line ───────────────────────────────────────────────────────────
    def set_status(self, message: str) -> None:
        try:
            self.query_one(f"#{self.TABLE_ID}-status", Static).update(message)
        except Exception:
            pass

    # ── filtering ─────────────────────────────────────────────────────────────
    def action_focus_filter(self) -> None:
        widget = self.query_one(f"#{self.filter_id}", Input)
        widget.add_class("-visible")
        widget.focus()

    async def action_clear_filter(self) -> None:
        widget = self.query_one(f"#{self.filter_id}", Input)
        widget.value = ""
        widget.remove_class("-visible")
        self._filter = ""
        self.query_one(f"#{self.TABLE_ID}", DataTable).focus()
        await self.action_refresh()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != self.filter_id:
            return
        self._filter = (event.value or "").strip().lower()
        await self.action_refresh()

    def _matches(self, cells: tuple[str, ...]) -> bool:
        if not self._filter:
            return True
        return self._filter in " ".join(str(c) for c in cells).lower()

    # ── refresh ───────────────────────────────────────────────────────────────
    async def action_refresh(self) -> None:
        """Re-poll the source and redraw only if something actually changed."""
        try:
            rows = await self.load_rows()
        except Exception:  # a read failure must never crash the TUI
            rows = []
        visible = [(key, cells) for key, cells in rows if self._matches(cells)]
        signature = (self._filter, tuple((k, c) for k, c in visible))
        if signature == self._signature:
            return
        self._signature = signature

        try:
            table = self.query_one(f"#{self.TABLE_ID}", DataTable)
        except Exception:
            return
        # Preserve where the user was looking across the redraw.
        cursor = table.cursor_coordinate
        offset = table.scroll_offset
        table.clear()
        for key, cells in visible:
            table.add_row(*[str(c) for c in cells], key=key)
        if visible:
            row = min(cursor.row, len(visible) - 1)
            try:
                table.cursor_coordinate = (row, cursor.column)
                table.scroll_to(offset.x, offset.y, animate=False)
            except Exception:
                pass

    # ── selection helper ──────────────────────────────────────────────────────
    def selected_key(self) -> "str | None":
        """The row key under the cursor, or ``None`` on an empty/odd table."""
        try:
            table = self.query_one(f"#{self.TABLE_ID}", DataTable)
        except Exception:
            return None
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        return None if row_key.value is None else str(row_key.value)
