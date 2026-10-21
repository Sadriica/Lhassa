# SPDX-License-Identifier: AGPL-3.0-or-later
"""The keyboard-help overlay; a modal listing every binding, bound to ``?``/``F1``.

Textual's Footer only has room for the active screen's bindings; this modal is
the whole map in one place, grouped by scope. It is a plain ``ModalScreen``
dismissed by ``escape``, ``?`` or ``q``: the conventional Textual help idiom.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

#: ``(scope, [(keys, what it does), …])``: the single source of the help text.
HELP: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Anywhere",
        [
            ("ctrl+p", "Command palette; jump to a screen or run an action"),
            ("?  /  f1", "This help"),
            ("r", "Refresh the current screen now"),
            ("q", "Quit"),
        ],
    ),
    (
        "Tables (Projects · Runs · Approvals · Flows · Skills · Memory · Org)",
        [
            ("/", "Filter; case-insensitive substring over the row"),
            ("escape", "Clear the filter"),
            ("↑ ↓ · pgup pgdn", "Move the cursor"),
            ("enter", "Select the row (loads its detail pane where there is one)"),
        ],
    ),
    (
        "Approvals",
        [
            ("a", "Approve the selected row (via security/approval.py:resolve)"),
            ("d", "Deny the selected row (same seam)"),
        ],
    ),
    (
        "Flows",
        [
            ("enter", "Show that flow's durable runs"),
            ("x", "Run the selected flow (through org/delegate.py:delegate_flow)"),
        ],
    ),
    (
        "Memory",
        [("/", "Search facts AND run FTS5 episodic recall for the same query")],
    ),
]


class HelpScreen(ModalScreen):
    """A dismissible overlay listing every keybinding in the app."""

    BINDINGS = [
        ("escape", "dismiss_help", "Close"),
        ("question_mark", "dismiss_help", "Close"),
        ("q", "dismiss_help", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Lhassa TUI; keyboard map", classes="help-title")
            with VerticalScroll():
                for scope, bindings in HELP:
                    yield Static(scope, classes="help-scope")
                    for keys, what in bindings:
                        yield Static(f"  [b]{keys:<16}[/] {what}")
            yield Static("[dim]escape / ? / q to close[/]", classes="help-foot")

    def action_dismiss_help(self) -> None:
        self.dismiss(None)
