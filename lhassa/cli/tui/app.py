"""``LhassaTui`` — the Textual application shell for ``lhassa tui``.

A tabbed layout over the four screens (Overview / Projects / Runs /
Approvals), styled with the Lhasa palette (temple gold / monk-robe red / snow
white / sage green / stone) from ``lhassa.cli.theme``. All data access is
DIRECT reads off ``lhassa.core.db`` (see each screen module) — the TUI works
with the API server down. Mutating actions (approve/deny) go through
``lhassa.security.approval.resolve``, the same seam CLI/API use.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, TabbedContent, TabPane

from lhassa.cli.theme import GOLD, GREEN, RED, STONE, WHITE
from lhassa.cli.tui.screens.approvals import ApprovalsScreen
from lhassa.cli.tui.screens.overview import OverviewScreen
from lhassa.cli.tui.screens.projects import ProjectsScreen
from lhassa.cli.tui.screens.runs import RunsScreen


class LhassaTui(App):
    """The Lhassa terminal dashboard: a brain-status TUI over the local DB."""

    TITLE = "Lhassa"
    SUB_TITLE = "the brain, in a terminal"

    CSS = f"""
    Screen {{
        background: $surface;
        color: {WHITE};
    }}
    Header {{
        background: {GOLD};
        color: black;
    }}
    Footer {{
        background: {STONE};
        color: {WHITE};
    }}
    TabbedContent {{
        height: 1fr;
    }}
    Tabs {{
        background: $surface;
    }}
    Tab.-active {{
        color: {GOLD};
        text-style: bold;
    }}
    DataTable > .datatable--header {{
        background: {STONE};
        color: {WHITE};
        text-style: bold;
    }}
    DataTable > .datatable--cursor {{
        background: {RED};
        color: {WHITE};
    }}
    .success {{
        color: {GREEN};
    }}
    """

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, instance_id: "str | None" = None, **kwargs) -> None:
        super().__init__(**kwargs)
        if instance_id is None:
            from lhassa.config import Config

            instance_id = Config.instance_id
        self._instance_id = instance_id

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="overview"):
            with TabPane("Overview", id="overview"):
                yield OverviewScreen(self._instance_id)
            with TabPane("Projects", id="projects"):
                yield ProjectsScreen(self._instance_id)
            with TabPane("Runs", id="runs"):
                yield RunsScreen(self._instance_id)
            with TabPane("Approvals", id="approvals"):
                yield ApprovalsScreen(self._instance_id)
        yield Footer()


def run_tui(instance_id: "str | None" = None) -> None:
    """Launch the Lhassa TUI. Blocking; returns when the user quits."""
    LhassaTui(instance_id=instance_id).run()
