# SPDX-License-Identifier: AGPL-3.0-or-later
"""The status read-model: the brain's seat, spend and mode in one snapshot.

One place builds what every terminal surface shows at its edge: the REPL's
bottom toolbar (``cli/repl_ui.py``) and the TUI's status bar (the ``tui``
pack). :func:`gather` reads it; :func:`segments` lays it out as ``(role,
text)`` pairs so each surface only maps roles to its own styling. Roles are a
closed set: ``brand``, ``label``, ``value``, ``model``, ``muted``, ``ok``,
``warn``, ``alert``, ``bar-ok``/``bar-warn``/``bar-alert`` (filled cells) and
``bar-empty``.

Everything is a local read ($0, no model, works with the API server down):
``llm.gateway.spend_summary`` for the budget, ``core.db`` for the rest, the
health digest snapshot for health. Gathering never raises; a field that cannot
be read stays ``None`` and renders as ``?``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: Cells in the budget bar.
BAR_WIDTH = 10

#: Every role :func:`segments` may emit.
ROLES = frozenset(
    {
        "brand", "label", "value", "model", "muted", "ok", "warn", "alert",
        "bar-ok", "bar-warn", "bar-alert", "bar-empty",
    }
)

SEP = " · "


def seconds_to_utc_midnight(now: "datetime | None" = None) -> int:
    now = now or datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((tomorrow - now).total_seconds()))


def age_seconds(stamp: object, now: "datetime | None" = None) -> "int | None":
    """Seconds since a stored timestamp; ``None`` if it cannot be read."""
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


def health_failing(config=None) -> "tuple[str, ...] | None":
    """Failing checks from the last health digest; ``None`` if there is none."""
    try:
        if config is None:
            from lhassa.config import Config as config
        override = (getattr(config, "health_dir", "") or "").strip()
        path = (Path(override) if override else Path.home() / ".lhassa" / "health") / "digest.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(raw, dict):
            return None
        results = raw.get("results") or {}
        return tuple(
            name
            for name, r in results.items()
            if isinstance(r, dict) and r.get("level") in ("warn", "alert")
        )
    except Exception:
        return None


async def gather(instance_id: "str | None" = None) -> dict:
    """Collect the status snapshot. Never raises; degrades field by field."""
    from lhassa.config import Config

    if instance_id is None:
        instance_id = Config.instance_id
    snap: dict = {
        "instance_id": instance_id,
        "heartbeat_seat": str(getattr(Config, "heartbeat_provider", "") or "auto"),
        "chat_provider": str(getattr(Config, "llm_provider", "") or "?"),
        "model": str(getattr(Config, "model", "") or "?"),
        "budget": 0,
        "pct_used": 0.0,
        "cost_usd": 0.0,
        "soft_pct": float(getattr(Config, "budget_soft_pct", 0.8) or 0.8),
        "reset_seconds": seconds_to_utc_midnight(),
        "org_live": bool(getattr(Config, "org_live", False)),
        "approvals": None,
        "tasks": None,
        "beat_age": None,
        "health_failing": health_failing(Config),
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
            snap["beat_age"] = age_seconds(recent[0].get("creado_en"))
    except Exception:
        pass
    return snap


def _bar(pct: float, soft: float) -> list[tuple[str, str]]:
    pct = max(0.0, pct)
    level = "alert" if pct >= 1.0 else "warn" if pct >= soft else "ok"
    filled = min(BAR_WIDTH, round(pct * BAR_WIDTH))
    if pct > 0 and filled == 0:
        filled = 1  # any spend shows
    cells = [(f"bar-{level}", "█" * filled), ("bar-empty", "█" * (BAR_WIDTH - filled))]
    return [(f"bar-{level}", "[")] + [c for c in cells if c[1]] + [(f"bar-{level}", "]")]


def seat_segments(snap: dict) -> list[tuple[str, str]]:
    """Line one: instance, seats, today's budget and cost."""
    out: list[tuple[str, str]] = [
        ("brand", str(snap.get("instance_id") or "?")),
        ("label", "  beat "),
        ("value", str(snap.get("heartbeat_seat") or "auto")),
        ("label", "  chat "),
        ("model", str(snap.get("model") or "?")),
        ("label", "  budget "),
    ]
    budget = int(snap.get("budget") or 0)
    if budget > 0:
        pct = float(snap.get("pct_used") or 0.0)
        out += _bar(pct, float(snap.get("soft_pct") or 0.8))
        out.append(("value", f" {round(pct * 100)}%"))
        out.append(("ok", f" {format_duration(snap.get('reset_seconds'))}"))
    else:
        out.append(("value", "unlimited"))
    out.append(("ok", f"  ${float(snap.get('cost_usd') or 0.0):.4f}"))
    return out


def mode_segments(snap: dict) -> list[tuple[str, str]]:
    """Line two: org mode, what waits on the owner, last beat, health."""
    out: list[tuple[str, str]] = []
    out.append(("model", "▸▸ org live") if snap.get("org_live") else ("muted", "▸▸ org offline"))
    approvals = snap.get("approvals")
    out.append(("muted", SEP))
    if approvals is None:
        out.append(("muted", "approvals ?"))
    else:
        out.append(("model" if approvals else "muted", f"{approvals} approvals"))
    tasks = snap.get("tasks")
    out.append(("muted", SEP))
    out.append(("muted", f"{'?' if tasks is None else tasks} tasks"))
    out.append(("muted", SEP))
    out.append(("muted", f"last beat {format_duration(snap.get('beat_age'))} ago"))
    out.append(("muted", SEP))
    failing = snap.get("health_failing")
    if failing is None:
        out.append(("muted", "health ?"))
    elif failing:
        out.append(("alert", "health: " + ", ".join(failing)))
    else:
        out.append(("ok", "health ok"))
    return out


def segments(snap: dict) -> list[tuple[str, str]]:
    """Both lines, separated by a ``("", "\\n")`` pair."""
    return seat_segments(snap) + [("", "\n")] + mode_segments(snap)


def plain(snap: dict) -> str:
    return "".join(text for _, text in segments(snap))
