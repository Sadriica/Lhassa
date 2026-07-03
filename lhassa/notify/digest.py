"""The scheduled operative digest — a deterministic, $0 status message.

Composes a compact, plain-language snapshot of the brain's day: the monitor's
environment signals (:func:`lhassa.org.monitor.gather_signals`), today's LLM
spend, pending approvals, a recent-episodes summary, running/finished agent job
counts, and tasks due soon. NO LLM is ever called here — every section is a
read of state already on disk, formatted with a few emoji section markers for
readability in a Telegram chat.

:func:`build_digest` is the pure composer; :func:`send_digest` is the actionpack
action wired into the allowlist (see :mod:`lhassa.actionpack._digest`) so it can be
minted as a recurring task via ``core.dispatch.mint_task`` (see the ``digest`` seed
in :mod:`lhassa.seeds.digest`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

# How many recent episodes to summarize.
_EPISODE_WINDOW = 20

# A pending task is "due soon" if its due_at falls within this many hours.
_DUE_SOON_HOURS = 24

# Cap on how many due-soon tasks are listed by name.
_DUE_SOON_LIST_CAP = 5


def _resolve_config(config):
    if config is not None:
        return config
    from lhassa.config import Config

    return Config()


def _instance_id(config) -> str:
    return getattr(config, "instance_id", None) or "inst_personal"


def _signals_section(config, instance_id: str) -> list[str]:
    """The monitor's environment snapshot — health, flows, failures. Never raises.

    Lazy-imports ``org`` (both ``notify`` and ``org`` are shells, so this edge is
    architecturally fine per the layering rules; the lazy import is kept anyway as
    good practice so ``notify`` stays import-light for callers that never touch org).
    """
    try:
        from lhassa.org.monitor import gather_signals

        signals = gather_signals(config, instance_id=instance_id)
        health = signals.get("health") or {}
        worst = health.get("worst_level", "info")
        ok = "ok" if health.get("ok", True) else "needs attention"
        lines = [f"🩺 Health: {worst} ({ok})"]
        failing = health.get("failing") or []
        if failing:
            lines.append(f"   failing: {', '.join(failing)}")
        n_failures = len(signals.get("failures") or [])
        if n_failures:
            lines.append(f"   {n_failures} recent failure/offline marker(s)")
        n_flows = len(signals.get("flows") or [])
        lines.append(f"   {n_flows} work-flow(s) defined")
        return lines
    except Exception as exc:
        logger.debug(f"digest: signals section unavailable: {exc}")
        return ["🩺 Health: unavailable"]


async def _spend_section(config) -> list[str]:
    """Today's LLM spend — reuses the /status command's ledger helper."""
    try:
        from lhassa.notify.commands import _today_spend

        spend = await _today_spend(config)
        if spend is None:
            return []
        return [f"💰 Spend today: ${spend:.4f}"]
    except Exception as exc:
        logger.debug(f"digest: spend section unavailable: {exc}")
        return []


async def _approvals_section(instance_id: str) -> list[str]:
    """Pending human-in-the-loop approvals."""
    try:
        from lhassa.security import approval as _approval

        rows = await _approval.pending(instance_id)
        if not rows:
            return ["✅ Approvals: none pending"]
        return [f"⏳ Approvals: {len(rows)} pending — /approvals for details"]
    except Exception as exc:
        logger.debug(f"digest: approvals section unavailable: {exc}")
        return []


async def _episodes_section(instance_id: str) -> list[str]:
    """Recent episodes summarized by decision-prefix / despertar_por."""
    try:
        from lhassa.core import db as _db

        episodes = await _db.load_recent_episodes(instance_id, _EPISODE_WINDOW)
        if not episodes:
            return [f"📜 Episodes: none in the last {_EPISODE_WINDOW}"]

        by_source: dict[str, int] = {}
        for ep in episodes:
            source = str(ep.get("despertar_por") or "?")
            by_source[source] = by_source.get(source, 0) + 1
        breakdown = ", ".join(
            f"{name}={count}" for name, count in sorted(by_source.items())
        )
        return [f"📜 Episodes (last {len(episodes)}): {breakdown}"]
    except Exception as exc:
        logger.debug(f"digest: episodes section unavailable: {exc}")
        return []


def _jobs_section() -> list[str]:
    """Running/finished agent job counts from ``state.agent_runs``."""
    try:
        from lhassa.state import state

        runs = list(state.agent_runs.values())
        if not runs:
            return ["🤖 Agent jobs: none tracked"]
        running = sum(1 for r in runs if r.status == "running")
        finished = sum(1 for r in runs if r.status != "running")
        return [f"🤖 Agent jobs: {running} running, {finished} finished"]
    except Exception as exc:
        logger.debug(f"digest: jobs section unavailable: {exc}")
        return []


async def _due_soon_section(instance_id: str) -> list[str]:
    """Pending tasks whose ``due_at`` falls within the next 24h (or is overdue)."""
    try:
        from lhassa.core import db as _db

        tasks = await _db.list_tasks(instance_id, estado="pendiente", limit=200)
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=_DUE_SOON_HOURS)

        due_soon = []
        for task in tasks:
            due_raw = task.get("due_at")
            if not due_raw:
                continue
            try:
                due_dt = datetime.fromisoformat(str(due_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if due_dt <= horizon:
                due_soon.append((due_dt, task))

        if not due_soon:
            return ["🗓️ Due soon: nothing in the next 24h"]

        due_soon.sort(key=lambda pair: pair[0])
        names = [
            f"{t.get('accion', '?')}@{due.strftime('%H:%M')}"
            for due, t in due_soon[:_DUE_SOON_LIST_CAP]
        ]
        more = f" …+{len(due_soon) - _DUE_SOON_LIST_CAP}" if len(due_soon) > _DUE_SOON_LIST_CAP else ""
        return [f"🗓️ Due soon ({len(due_soon)}): {', '.join(names)}{more}"]
    except Exception as exc:
        logger.debug(f"digest: due-soon section unavailable: {exc}")
        return []


async def build_digest(config: Optional[object] = None) -> str:
    """Compose the operative digest. Deterministic; NEVER calls an LLM.

    Every section is read from state already on disk (the monitor's signals, the
    cost ledger, the approvals queue, the episode log, tracked agent jobs, and the
    task table); a source that errors degrades to an omitted or "unavailable" line
    rather than breaking the whole digest.
    """
    cfg = _resolve_config(config)
    instance_id = _instance_id(cfg)

    lines: list[str] = ["🧭 Lhassa — operative digest"]
    lines.extend(_signals_section(cfg, instance_id))
    lines.extend(await _spend_section(cfg))
    lines.extend(await _approvals_section(instance_id))
    lines.extend(await _episodes_section(instance_id))
    lines.extend(_jobs_section())
    lines.extend(await _due_soon_section(instance_id))
    return "\n".join(lines)


async def send_digest(params: Optional[dict] = None, *, config: Optional[object] = None) -> str:
    """Build the digest and fan it out via :func:`lhassa.notify.dispatch.notify`.

    The actionpack ``send_digest`` action (see :mod:`lhassa.actionpack._digest`) is a
    thin ``(params) -> str`` wrapper over this so it fits the deterministic-action
    calling convention; this function is the reusable core, callable directly too.
    Never raises — a delivery failure is reported as a string, not an exception.
    """
    cfg = _resolve_config(config)
    try:
        text = await build_digest(cfg)
        from lhassa.notify.dispatch import notify

        delivered = await notify(text, level="info", config=cfg)
        return "send_digest: delivered" if delivered else "send_digest: no channel delivered"
    except Exception as exc:
        return f"send_digest: error: {exc}"
