# SPDX-License-Identifier: AGPL-3.0-or-later
"""``LhassaTui`` — the Textual application shell for ``lhassa tui``.

A tabbed layout over eight screens — Overview / Projects / Runs / Approvals /
Flows / Skills / Memory / Org — styled with the Lhasa palette (temple gold /
monk-robe red / snow white / sage green / stone) from ``lhassa.cli.theme``.

Three app-wide affordances sit on top of the tabs:

* **Command palette** (``ctrl+p``) — Textual's own palette, fed by
  ``lhassa.cli.tui.palette.LhassaCommands``: jump to any screen, open the help,
  refresh, quit.
* **Keyboard help** (``?`` / ``F1``) — the whole keymap in one modal.
* **Live refresh** — every table screen re-polls on a timer (see
  ``screens/base.py``); ``r`` forces it now.

All data access is DIRECT reads off ``lhassa.core.db`` / ``flows.store`` /
``skills.store`` / ``org.topology`` (see each screen module) — the TUI works with
the API server down. Mutating actions cross the SAME seams every other surface
uses: approve/deny through ``lhassa.security.approval.resolve``, a flow run
through ``lhassa.org.delegate.delegate_flow``. Never raw SQL.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, TabbedContent, TabPane

from lhassa.cli.theme import GOLD, GREEN, RED, STONE, WHITE
from lhassa.cli.tui.palette import LhassaCommands
from lhassa.cli.tui.screens.approvals import ApprovalsScreen
from lhassa.cli.tui.screens.flows import FlowsScreen
from lhassa.cli.tui.screens.help import HelpScreen
from lhassa.cli.tui.screens.memory import MemoryScreen
from lhassa.cli.tui.screens.org import OrgScreen
from lhassa.cli.tui.screens.overview import OverviewScreen
from lhassa.cli.tui.screens.projects import ProjectsScreen
from lhassa.cli.tui.screens.runs import RunsScreen
from lhassa.cli.tui.screens.skills import SkillsScreen


class LhassaTui(App):
    """The Lhassa terminal dashboard: a brain-status TUI over the local DB."""

    TITLE = "Lhassa"
    SUB_TITLE = "the brain, in a terminal"

    #: ``(tab id, label)`` — the ONE table the tabs, the palette and the help read.
    TABS: tuple[tuple[str, str], ...] = (
        ("overview", "Overview"),
        ("projects", "Projects"),
        ("runs", "Runs"),
        ("approvals", "Approvals"),
        ("flows", "Flows"),
        ("skills", "Skills"),
        ("memory", "Memory"),
        ("org", "Org"),
    )

    COMMANDS = App.COMMANDS | {LhassaCommands}

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

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("question_mark", "help", "Help"),
        ("f1", "help", "Help"),
    ]

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
            with TabPane("Flows", id="flows"):
                yield FlowsScreen(self._instance_id)
            with TabPane("Skills", id="skills"):
                yield SkillsScreen(self._instance_id)
            with TabPane("Memory", id="memory"):
                yield MemoryScreen(self._instance_id)
            with TabPane("Org", id="org"):
                yield OrgScreen(self._instance_id)
        yield Footer()

    # ── app actions (also the command palette's entries) ──────────────────────
    def switch_tab(self, tab_id: str) -> None:
        """Make ``tab_id`` the active screen. Never raises on an unknown id."""
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            pass

    def action_help(self) -> None:
        """Open the keyboard-help overlay (idempotent — never stacks two)."""
        if isinstance(self.screen, HelpScreen):
            return
        self.push_screen(HelpScreen())

    async def action_refresh_current(self) -> None:
        """Force an immediate re-poll of the active screen."""
        try:
            pane = self.query_one(TabbedContent).active_pane
        except Exception:
            return
        if pane is None:
            return
        for child in pane.walk_children():
            action = getattr(child, "action_refresh", None)
            if callable(action):
                result = action()
                if hasattr(result, "__await__"):
                    await result
                return


def run_tui(instance_id: "str | None" = None) -> None:
    """Launch the Lhassa TUI. Blocking; returns when the user quits."""
    LhassaTui(instance_id=instance_id).run()
