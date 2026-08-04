# SPDX-License-Identifier: AGPL-3.0-or-later
"""The command palette provider — every screen and app action, one ``ctrl+p`` away.

Uses Textual's built-in palette machinery (:class:`textual.command.Provider` →
:class:`~textual.command.Hits`), so the fuzzy matcher, the highlighting and the
``ctrl+p`` binding all come from the framework rather than a hand-rolled overlay.

The palette is a NAVIGATION + APP-ACTION surface only: it switches tabs, opens
the help overlay, refreshes and quits. It deliberately does NOT expose mutations
(approve/deny/run) — those stay on the screen that owns the selected row, where
the operator can see exactly what they are acting on.
"""

from __future__ import annotations

from functools import partial

from textual.command import DiscoveryHit, Hit, Hits, Provider


class LhassaCommands(Provider):
    """Screen navigation + app actions as command-palette entries."""

    def _entries(self) -> list[tuple[str, str, object]]:
        """``(command, help, callback)`` for every palette entry."""
        app = self.app
        out: list[tuple[str, str, object]] = []
        for tab_id, label in getattr(app, "TABS", ()):
            out.append(
                (
                    f"Go to {label}",
                    f"Switch to the {label} screen",
                    partial(app.switch_tab, tab_id),
                )
            )
        out.append(("Keyboard help", "Show every keybinding", app.action_help))
        out.append(("Refresh", "Re-poll the current screen now", app.action_refresh_current))
        out.append(("Quit", "Leave the TUI", app.action_quit))
        return out

    async def discover(self) -> Hits:
        """What the palette shows before the user types anything."""
        for command, help_text, callback in self._entries():
            yield DiscoveryHit(command, callback, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for command, help_text, callback in self._entries():
            score = matcher.match(command)
            if score > 0:
                yield Hit(score, matcher.highlight(command), callback, help=help_text)
