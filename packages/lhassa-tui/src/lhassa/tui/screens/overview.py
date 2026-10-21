# SPDX-License-Identifier: AGPL-3.0-or-later
"""Overview screen; the brain at a glance, as four titled sections.

**State** (instance, last episode) · **Today** (thinks, cost, episodes) ·
**Pending** (approvals, tasks, active projects) · **Health** (integrity
attestation, failed system checks). All four read straight off the local DB /
the health digest snapshot; no API server, no model, $0.

Re-polled on a timer like every other screen (``r`` forces it now). The redraw
is skipped when the whole snapshot is unchanged, so an idle brain never
flickers. Rows are real widgets (a label ``Static`` + a right-aligned value
``Static``), not one bespoke markup string; labels in stone, values in white,
cost in green, a failing health check in red.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.widgets import Static

_EMPTY = "—"


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _health_dir() -> Path:
    """Where the health digest snapshot lives (config override → default home).

    Mirrors ``lhassa.org.monitor._health_dir`` (a read-only, $0 duplication;
    this screen must keep working even if that private helper's shape moves;
    the location itself is documented, not incidental).
    """
    try:
        from lhassa.config import Config

        override = (getattr(Config, "health_dir", "") or "").strip()
        if override:
            return Path(override)
    except Exception:
        pass
    return Path.home() / ".lhassa" / "health"


def _health_snapshot() -> dict:
    """``{worst_level, ok, failing}`` from the last health digest. Never raises."""
    empty = {"worst_level": _EMPTY, "ok": None, "failing": []}
    try:
        path = _health_dir() / "digest.json"
        if not path.exists():
            return empty
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(raw, dict):
            return empty
        results = raw.get("results") or {}
        failing = [
            name
            for name, r in results.items()
            if isinstance(r, dict) and r.get("level") in ("warn", "alert")
        ]
        return {
            "worst_level": raw.get("worst_level", "info"),
            "ok": bool(raw.get("ok", True)),
            "failing": failing,
        }
    except Exception:
        return empty


async def _integrity_status(instance_id: str) -> str:
    """A one-line integrity attestation summary. Never raises."""
    try:
        from lhassa.core.conformance import attestation

        result = await attestation(instance_id)
        if result.get("valid"):
            return "valid"
        return str(result.get("reason") or "invalid")
    except Exception:
        return "unavailable"


async def _gather(instance_id: str) -> dict:
    """Collect the overview snapshot. Never raises; degrades field by field."""
    from lhassa.core import db

    snapshot = {
        "instance_id": instance_id,
        "last_episode_time": _EMPTY,
        "last_episode_accion": _EMPTY,
        "think_count": 0,
        "episode_count": 0,
        "think_cost": 0.0,
        "pending_approvals": 0,
        "pending_tasks": 0,
        "active_projects": 0,
        "integrity_status": _EMPTY,
        "health_failing": (),
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
        think_count = 0
        episode_count = 0
        cost = 0.0
        for row in recent:
            when = str(row.get("creado_en") or "")
            if not when.startswith(today):
                continue
            episode_count += 1
            spent = row.get("costo_usd") or 0.0
            try:
                spent = float(spent)
            except (TypeError, ValueError):
                spent = 0.0
            if spent > 0:
                think_count += 1
                cost += spent
        snapshot["think_count"] = think_count
        snapshot["episode_count"] = episode_count
        snapshot["think_cost"] = cost

    try:
        pending = await db.load_pending_approvals(instance_id)
        snapshot["pending_approvals"] = len(pending)
    except Exception:
        pass

    try:
        tasks = await db.load_pending_tasks(instance_id)
        snapshot["pending_tasks"] = len(tasks)
    except Exception:
        pass

    try:
        projects = await db.list_project_rows(instance_id, status="active")
        snapshot["active_projects"] = len(projects)
    except Exception:
        pass

    snapshot["integrity_status"] = await _integrity_status(instance_id)
    health = _health_snapshot()
    snapshot["health_failing"] = tuple(health.get("failing") or ())

    return snapshot


class OverviewScreen(Vertical):
    """The brain-at-a-glance panel: State · Today · Pending · Health."""

    BINDINGS = [("r", "refresh", "Refresh")]

    #: Seconds between background re-polls (mirrors ``screens/base.py``).
    REFRESH_SECONDS = 4.0

    def __init__(self, instance_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._instance_id = instance_id
        self._snapshot: "dict | None" = None

    @staticmethod
    def _row(label: str, value_id: str, *, value_class: str = "ov-value") -> ComposeResult:
        with Horizontal(classes="ov-row"):
            yield Static(label, classes="ov-label")
            yield Static(_EMPTY, id=value_id, classes=value_class)

    def compose(self) -> ComposeResult:
        with Grid(id="ov-grid"):
            with Vertical(id="ov-state", classes="ov-card"):
                yield Static("State", classes="ov-card-title")
                yield from self._row("Instance", "ov-instance")
                yield from self._row("Last episode", "ov-last-episode")
                yield from self._row("Last action", "ov-last-action")
            with Vertical(id="ov-today", classes="ov-card"):
                yield Static("Today", classes="ov-card-title")
                yield from self._row("Thinks", "ov-thinks")
                yield from self._row("Episodes", "ov-episodes")
                yield from self._row("Cost", "ov-cost", value_class="ov-value-cost")
            with Vertical(id="ov-pending", classes="ov-card"):
                yield Static("Pending", classes="ov-card-title")
                yield from self._row("Approvals", "ov-approvals")
                yield from self._row("Tasks", "ov-tasks")
                yield from self._row("Active projects", "ov-projects")
            with Vertical(id="ov-health", classes="ov-card"):
                yield Static("Health", classes="ov-card-title")
                yield from self._row("Integrity", "ov-integrity")
                yield from self._row("Failed checks", "ov-failed")

    async def on_mount(self) -> None:
        await self.action_refresh()
        if self.REFRESH_SECONDS > 0:
            self.set_interval(self.REFRESH_SECONDS, self.action_refresh)

    async def action_refresh(self) -> None:
        snapshot = await _gather(self._instance_id)
        if snapshot == self._snapshot:
            return  # nothing changed; do not redraw (no flicker)
        self._snapshot = snapshot
        self._paint(snapshot)

    def _paint(self, s: dict) -> None:
        failing = s.get("health_failing") or ()
        values = {
            "ov-instance": s["instance_id"],
            "ov-last-episode": s["last_episode_time"],
            "ov-last-action": s["last_episode_accion"],
            "ov-thinks": str(s["think_count"]),
            "ov-episodes": str(s["episode_count"]),
            "ov-cost": f"${s['think_cost']:.4f}",
            "ov-approvals": str(s["pending_approvals"]),
            "ov-tasks": str(s["pending_tasks"]),
            "ov-projects": str(s["active_projects"]),
            "ov-integrity": s["integrity_status"],
            "ov-failed": ", ".join(failing) if failing else "none",
        }
        for widget_id, text in values.items():
            try:
                self.query_one(f"#{widget_id}", Static).update(text)
            except Exception:
                pass  # a partial mount must never crash the redraw
        try:
            self.query_one("#ov-failed", Static).set_class(bool(failing), "-alert")
        except Exception:
            pass
