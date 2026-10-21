# SPDX-License-Identifier: AGPL-3.0-or-later
"""The status bar: two lines docked at the bottom of every tab.

Line one is the brain's seat and spend: instance, heartbeat seat, chat model,
today's token budget as a bar (green under ``budget_soft_pct``, gold past it,
red when spent), the time until the daily budget resets (UTC midnight) and
today's real cost. Line two is the mode line: whether the org runs live, what
is waiting on the owner (approvals, tasks), how long since the last beat, and
health.

Everything is a local read ($0, no model, works with the API server down):
``llm.gateway.spend_summary`` for the budget, ``core.db`` for the rest, the
health digest snapshot for health. Gathering never raises; a field that cannot
be read degrades to "unknown" and the bar still paints. Rendering is a pure
function of the snapshot (:func:`render`) so it is tested without a terminal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rich.text import Text
from textual.widgets import Static

from lhassa.tui import colors as tui_colors

#: Cells in the budget bar.
BAR_WIDTH = 10


def _seconds_to_utc_midnight(now: "datetime | None" = None) -> int:
    now = now or datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((tomorrow - now).total_seconds()))


def _age_seconds(stamp: object, now: "datetime | None" = None) -> "int | None":
    """Seconds since an episode's ``creado_en``; ``None`` if it cannot be read."""
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - when).total_seconds()))


def format_duration(seconds: "int | None") -> str:
    """``7h20m`` / ``12m`` / ``40s``; ``?`` when unknown."""
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    return f"{hours // 24}d{hours % 24}h"


async def gather(instance_id: str) -> dict:
    """Collect the status snapshot. Never raises; degrades field by field."""
    from lhassa.config import Config

    snap: dict = {
        "instance_id": instance_id,
        "heartbeat_seat": str(getattr(Config, "heartbeat_provider", "") or "auto"),
        "model": str(getattr(Config, "model", "") or "?"),
        "budget": 0,
        "pct_used": 0.0,
        "cost_usd": 0.0,
        "soft_pct": float(getattr(Config, "budget_soft_pct", 0.8) or 0.8),
        "reset_seconds": _seconds_to_utc_midnight(),
        "org_live": bool(getattr(Config, "org_live", False)),
        "approvals": None,
        "tasks": None,
        "beat_age": None,
        "health_failing": None,
    }
    try:
        from lhassa.llm.gateway import spend_summary

        spend = await spend_summary(Config())
        snap["budget"] = int(spend.get("budget") or 0)
        snap["pct_used"] = float(spend.get("pct_used") or 0.0)
        snap["cost_usd"] = float(spend.get("cost_usd") or 0.0)
    except Exception:
        pass
    try:
        from lhassa.core import db

        await db.init_db()
        snap["approvals"] = len(await db.load_pending_approvals(instance_id))
        snap["tasks"] = len(await db.load_pending_tasks(instance_id))
        recent = await db.load_recent_episodes(instance_id, n=1)
        if recent:
            snap["beat_age"] = _age_seconds(recent[0].get("creado_en"))
    except Exception:
        pass
    try:
        from lhassa.tui.screens.overview import _health_snapshot

        health = _health_snapshot()
        if health.get("ok") is not None:
            snap["health_failing"] = tuple(health.get("failing") or ())
    except Exception:
        pass
    return snap


def _bar(pct: float, soft: float, palette: dict) -> Text:
    pct = max(0.0, pct)
    if pct >= 1.0:
        fill = palette["lh-red"]
    elif pct >= soft:
        fill = palette["lh-gold"]
    else:
        fill = palette["lh-green"]
    try:
        empty = tui_colors._mix(fill, palette["lh-bg"], 0.72)
    except Exception:
        empty = palette["lh-faint"]
    filled = min(BAR_WIDTH, round(pct * BAR_WIDTH))
    if pct > 0 and filled == 0:
        filled = 1  # any spend shows, like a sliver of context
    bar = Text("[", style=fill)
    bar.append("█" * filled, style=fill)
    bar.append("█" * (BAR_WIDTH - filled), style=empty)
    bar.append("]", style=fill)
    return bar


def render(snap: dict, palette: dict) -> Text:
    """The two status lines for ``snap`` in ``palette``. Pure."""
    gold, faint, text = palette["lh-gold"], palette["lh-faint"], palette["lh-text"]
    green, red, stone = palette["lh-green"], palette["lh-red"], palette["lh-stone"]

    out = Text(no_wrap=True, overflow="ellipsis")
    out.append(str(snap.get("instance_id") or "?"), style=f"bold {gold}")
    out.append("  beat ", style=faint)
    out.append(str(snap.get("heartbeat_seat") or "auto"), style=stone)
    out.append("  chat ", style=faint)
    out.append(str(snap.get("model") or "?"), style=f"bold {palette['lh-gold-soft']}")
    out.append("  budget ", style=faint)
    budget = int(snap.get("budget") or 0)
    if budget > 0:
        pct = float(snap.get("pct_used") or 0.0)
        out.append_text(_bar(pct, float(snap.get("soft_pct") or 0.8), palette))
        out.append(f" {round(pct * 100)}%", style=f"bold {text}")
        out.append(f" {format_duration(snap.get('reset_seconds'))}", style=green)
    else:
        out.append("unlimited", style=text)
    out.append(f"  ${float(snap.get('cost_usd') or 0.0):.4f}", style=green)

    out.append("\n")
    if snap.get("org_live"):
        out.append("▸▸ org live", style=gold)
    else:
        out.append("▸▸ org offline", style=stone)
    approvals = snap.get("approvals")
    out.append(" · ", style=faint)
    if approvals is None:
        out.append("approvals ?", style=faint)
    else:
        out.append(f"{approvals} approvals", style=gold if approvals else faint)
    tasks = snap.get("tasks")
    out.append(" · ", style=faint)
    out.append(f"{'?' if tasks is None else tasks} tasks", style=faint)
    out.append(" · ", style=faint)
    out.append(f"last beat {format_duration(snap.get('beat_age'))} ago", style=faint)
    out.append(" · ", style=faint)
    failing = snap.get("health_failing")
    if failing is None:
        out.append("health ?", style=faint)
    elif failing:
        out.append("health: " + ", ".join(failing), style=red)
    else:
        out.append("health ok", style=green)
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
