# SPDX-License-Identifier: AGPL-3.0-or-later
"""``LhassaTui``: the Textual application shell for ``lhassa tui``.

A tabbed layout over eight screens. Overview / Projects / Runs / Approvals /
Flows / Skills / Memory / Org; styled with the Lhasa palette (temple gold /
monk-robe red / snow white / sage green / stone) from ``lhassa.cli.theme``.

Three app-wide affordances sit on top of the tabs:

* **Command palette** (``ctrl+p``); Textual's own palette, fed by
  ``lhassa.cli.tui.palette.LhassaCommands``: jump to any screen, open the help,
  refresh, quit.
* **Keyboard help** (``?`` / ``F1``); the whole keymap in one modal.
* **Live refresh**: every table screen re-polls on a timer (see
  ``screens/base.py``); ``r`` forces it now.

All data access is DIRECT reads off ``lhassa.core.db`` / ``flows.store`` /
``skills.store`` / ``org.topology`` (see each screen module); the TUI works with
the API server down. Mutating actions cross the SAME seams every other surface
uses: approve/deny through ``lhassa.security.approval.resolve``, a flow run
through ``lhassa.org.delegate.delegate_flow``. Never raw SQL.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, TabbedContent, TabPane

from lhassa.cli.tui import colors as tui_colors
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

    #: ``(tab id, label)``: the ONE table the tabs, the palette and the help read.
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

    #: All styling lives in the stylesheet now (resolved relative to this
    #: module's directory: ``lhassa/cli/tui/lhassa.tcss``). Colors reach it as
    #: ``$lh-*`` CSS variables via :meth:`get_css_variables`, sourced from
    #: ``lhassa.cli.tui.colors``: never a literal hex in this file.
    CSS_PATH = "lhassa.tcss"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("question_mark", "help", "Help"),
        ("f1", "help", "Help"),
    ]

    def __init__(self, instance_id: "str | None" = None, **kwargs) -> None:
        # Resolved BEFORE super().__init__(); Textual's own App.__init__ calls
        # get_css_variables() while building the initial stylesheet, so
        # ``_theme_name`` must already exist by then. Degrades to dark on ANY
        # detection error, and is fixed for the session (a probe never re-fires
        # mid-run).
        try:
            self._theme_name = tui_colors.detect_theme()
        except Exception:
            self._theme_name = "dark"
        self._log_sink_id: "int | None" = None
        super().__init__(**kwargs)
        if instance_id is None:
            from lhassa.config import Config

            instance_id = Config.instance_id
        self._instance_id = instance_id

    def get_css_variables(self) -> dict[str, str]:
        """Feed the ``$lh-*`` palette (see ``cli/tui/colors.py``) to the stylesheet."""
        variables = super().get_css_variables()
        try:
            variables.update(tui_colors.variables(self._theme_name))
        except Exception:  # a palette failure must never block the UI
            pass
        return variables

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

    # ── logging: nothing may paint over the running UI ─────────────────────────
    def on_mount(self) -> None:
        """Redirect loguru into Textual's own log for the session's lifetime.

        ``cli/app.py:tui_cmd`` already calls ``_boot.quiet_logging()`` before the
        app is constructed, which drops the DEBUG→stderr default sink for a
        WARNING+ one. That is still a stderr sink, though, and Textual owns the
        alternate screen from here on. ANY stderr write bleeds through and
        paints over the chrome. So on mount every sink is swapped for one that
        writes to ``self.log`` (Textual's own, off-screen log) instead; on
        unmount the WARNING+ stderr sink is restored so a plain CLI session
        after the TUI exits behaves exactly as before.
        """
        self._install_log_sink()

    def on_unmount(self) -> None:
        self._restore_log_sink()

    def _install_log_sink(self) -> None:
        try:
            from loguru import logger as _loguru
        except Exception:
            return
        try:
            _loguru.remove()
        except Exception:
            pass

        def _sink(message: object) -> None:
            try:
                self.log(str(message).rstrip("\n"))
            except Exception:
                pass  # the app may already be tearing down

        try:
            self._log_sink_id = _loguru.add(_sink, level="DEBUG")
        except Exception:
            self._log_sink_id = None

    def _restore_log_sink(self) -> None:
        """Bring back a WARNING+ stderr sink, mirroring ``_boot.quiet_logging()``.

        Deliberately a CALLABLE sink (``sys.stderr.write`` looked up at WRITE
        time), not ``logger.add(sys.stderr, ...)``: ``on_unmount`` fires while
        Textual has not yet released its stdio redirection, so binding the
        stream object here would capture Textual's transient print-capture
        wrapper instead of the real terminal stderr the process ends up with
        once ``run()`` actually returns.
        """
        import os
        import sys

        try:
            from loguru import logger as _loguru
        except Exception:
            return
        if self._log_sink_id is not None:
            try:
                _loguru.remove(self._log_sink_id)
            except Exception:
                pass
            self._log_sink_id = None
        level = (os.getenv("LHASSA_CLI_LOG_LEVEL") or "WARNING").upper()
        if level in ("OFF", "NONE", "SILENT"):
            return

        def _write_stderr(message: object) -> None:
            try:
                sys.stderr.write(str(message))
            except Exception:
                pass

        try:
            _loguru.add(
                _write_stderr, level=level, format="<dim>{level: <7}</dim> {message}"
            )
        except Exception:
            pass

    # ── app actions (also the command palette's entries) ──────────────────────
    def switch_tab(self, tab_id: str) -> None:
        """Make ``tab_id`` the active screen. Never raises on an unknown id."""
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            pass

    def action_help(self) -> None:
        """Open the keyboard-help overlay (idempotent; never stacks two)."""
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
