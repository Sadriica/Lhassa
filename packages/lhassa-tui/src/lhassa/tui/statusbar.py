# SPDX-License-Identifier: AGPL-3.0-or-later
"""The status bar: two lines docked at the bottom of every tab.

The content is core's status read-model (``lhassa.views.status``), the same one
the REPL's bottom toolbar shows: line one is the seat and spend (instance,
heartbeat seat, chat model, today's token budget as a bar that turns gold past
``budget_soft_pct`` and red when spent, time to the UTC reset, today's cost);
line two is the mode line (org live, approvals, tasks, last beat, health). This
module only maps the read-model's roles onto the TUI palette.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from lhassa.tui import colors as tui_colors
from lhassa.views import status as status_view

#: Re-exported for tests and callers that size the bar.
BAR_WIDTH = status_view.BAR_WIDTH
format_duration = status_view.format_duration
gather = status_view.gather


def _styles(palette: dict) -> dict[str, str]:
    def empty(fill: str) -> str:
        try:
            return tui_colors._mix(fill, palette["lh-bg"], 0.72)
        except Exception:
            return palette["lh-faint"]

    return {
        "brand": f"bold {palette['lh-gold']}",
        "label": palette["lh-faint"],
        "value": palette["lh-text"],
        "model": f"bold {palette['lh-gold-soft']}",
        "muted": palette["lh-faint"],
        "ok": palette["lh-green"],
        "warn": palette["lh-gold"],
        "alert": palette["lh-red"],
        "bar-ok": palette["lh-green"],
        "bar-warn": palette["lh-gold"],
        "bar-alert": palette["lh-red"],
        "bar-empty": empty(palette["lh-green"]),
    }


def render(snap: dict, palette: dict) -> Text:
    """The two status lines for ``snap`` in ``palette``. Pure."""
    styles = _styles(palette)
    out = Text(no_wrap=True, overflow="ellipsis")
    for role, text in status_view.segments(snap):
        out.append(text, style=styles.get(role, ""))
    return out


class StatusBar(Static):
    """Docked two-line status, re-polled on a timer; skips identical redraws."""

    REFRESH_SECONDS = 5.0

    def __init__(self, instance_id: str, theme: str = "dark", **kwargs) -> None:
        super().__init__("", **kwargs)
        self._instance_id = instance_id
        self._theme = theme
        self._snapshot: "dict | None" = None

    async def on_mount(self) -> None:
        await self.refresh_status()
        if self.REFRESH_SECONDS > 0:
            self.set_interval(self.REFRESH_SECONDS, self.refresh_status)

    async def refresh_status(self) -> None:
        snap = await gather(self._instance_id)
        if snap == self._snapshot:
            return
        self._snapshot = snap
        try:
            self.update(render(snap, tui_colors.variables(self._theme)))
        except Exception:
            pass  # the bar must never take the UI down
