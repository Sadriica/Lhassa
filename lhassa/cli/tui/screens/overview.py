# SPDX-License-Identifier: AGPL-3.0-or-later
"""Overview screen — the brain at a glance.

The snapshot: instance id, the last episode (time + accion, standing in for
"last heartbeat" since every episode is one step() tick), today's think count +
cost (episodes with ``costo_usd > 0`` — routine $0 actions don't count as a
"think"), pending approvals count, active projects count.

Re-polled on a timer like every other screen (``r`` forces it now). The redraw
is skipped when the snapshot is unchanged, so an idle brain never flickers.
"""

from __future__ import annotations

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

_EMPTY = "—"


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def _gather(instance_id: str) -> dict:
    """Collect the overview snapshot. Never raises — degrades field by field."""
    from lhassa.core import db

    snapshot = {
        "instance_id": instance_id,
        "last_episode_time": _EMPTY,
        "last_episode_accion": _EMPTY,
        "think_count": 0,
        "think_cost": 0.0,
        "pending_approvals": 0,
        "active_projects": 0,
    }
    try:
        await db.init_db()
    except Exception:
        return snapshot

    try:
        recent = await db.load_recent_episodes(instance_id, n=500)
    except Exception:
        recent = []
    if recent:
        latest = recent[0]
        snapshot["last_episode_time"] = str(latest.get("creado_en") or _EMPTY)
        snapshot["last_episode_accion"] = str(latest.get("accion") or _EMPTY)
        today = _today_utc()
        count = 0
        cost = 0.0
        for row in recent:
            when = str(row.get("creado_en") or "")
            if not when.startswith(today):
                continue
            spent = row.get("costo_usd") or 0.0
            try:
                spent = float(spent)
            except (TypeError, ValueError):
                spent = 0.0
            if spent > 0:
                count += 1
                cost += spent
        snapshot["think_count"] = count
        snapshot["think_cost"] = cost

    try:
        pending = await db.load_pending_approvals(instance_id)
        snapshot["pending_approvals"] = len(pending)
    except Exception:
        pass

    try:
        projects = await db.list_project_rows(instance_id, status="active")
        snapshot["active_projects"] = len(projects)
    except Exception:
        pass

    return snapshot


class OverviewScreen(Vertical):
    """The brain-at-a-glance panel. Loads on mount; ``r`` re-loads."""

    DEFAULT_CSS = """
    OverviewScreen {
        padding: 1 2;
    }
    OverviewScreen Static.row {
        padding: 0 0 1 0;
    }
    """

    BINDINGS = [("r", "refresh", "Refresh")]

    #: Seconds between background re-polls (mirrors ``screens/base.py``).
    REFRESH_SECONDS = 4.0

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._instance_id = instance_id
        self._snapshot: "dict | None" = None

    def compose(self) -> ComposeResult:
        yield Static("Loading overview…", id="ov-body", classes="row")

    async def on_mount(self) -> None:
        await self.action_refresh()
        if self.REFRESH_SECONDS > 0:
            self.set_interval(self.REFRESH_SECONDS, self.action_refresh)

    async def action_refresh(self) -> None:
        snapshot = await _gather(self._instance_id)
        if snapshot == self._snapshot:
            return  # nothing changed — do not redraw (no flicker)
        self._snapshot = snapshot
        body = self.query_one("#ov-body", Static)
        body.update(
            "\n".join(
                [
                    f"[title]Instance[/] [value]{snapshot['instance_id']}[/]",
                    "",
                    f"[title]Last episode[/] [value]{snapshot['last_episode_time']}[/] "
                    f"[dim]·[/] [accent]{snapshot['last_episode_accion']}[/]",
                    "",
                    f"[title]Today[/] [value]{snapshot['think_count']} think(s)[/] "
                    f"[dim]·[/] [cost]${snapshot['think_cost']:.4f}[/]",
                    "",
                    f"[title]Pending approvals[/] [value]{snapshot['pending_approvals']}[/]",
                    f"[title]Active projects[/] [value]{snapshot['active_projects']}[/]",
                ]
            )
        )
