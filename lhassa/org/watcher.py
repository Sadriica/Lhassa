"""The MONITOR WATCHER — an always-on, $0 "ear" for the environment.

Where :mod:`lhassa.org.monitor` provides the *capability* to gather signals and
reason over them, this module is the standing **loop** that makes the monitor a
constant presence. It watches cheaply and continuously, and only wakes the brain /
alerts the owner when something notable actually happens.

Design (agreed with the owner):

  * **$0 while idle.** Each tick is deterministic signal-gathering
    (:func:`lhassa.org.monitor.gather_signals` — read-only, no LLM) diffed against
    the previous snapshot. Nothing is written and no token is spent unless a notable
    event is detected. The ONLY place a token could ever be spent is an optional
    live "think" (:func:`lhassa.org.monitor.monitor_pass`), OFF by default and
    additionally gated behind ``LHASSA_ORG_LIVE``.
  * **Elastic cadence.** While nothing is running the loop checks every
    ``monitor_idle_interval`` seconds (default 300); while something IS running
    (an ``agent_runs`` entry in a running status) it listens on the short
    ``monitor_active_interval`` (default 15) so a failure/finish is caught quickly.
  * **React on transition, not on persistence.** :func:`detect_events` diffs two
    snapshots into notable events; :func:`react` always logs a deterministic monitor
    Episode and pushes a Telegram alert only for *important* events (failures /
    health-alert / pending-approvals). A "last alerted" fingerprint de-dupes a
    STANDING condition so it never re-notifies every tick.
  * **Never crashes.** Every tick is wrapped: an error is logged and the loop backs
    off (exponential, capped) — a monitoring failure is data, never a crash.

The loop is gated by ``monitor_enabled`` (default OFF, opt-in). The systemd service
``scripts/systemd/lhassa-monitor.service`` is how it is turned on; the module
no-ops + exits cleanly when disabled.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.episode import Episode

# Imported by NAME (not via the module) so tests can patch ``watcher.gather_signals``
# / ``watcher.monitor_pass`` directly, mirroring the monitor's own test seams.
from lhassa.org.monitor import gather_signals, monitor_pass

# Truthy env values that flip a gate on — identical to the monitor/executor gate.
_TRUTHY = {"1", "true", "yes", "on"}

# Agent-run statuses that mean "something is executing right now" (see
# :class:`lhassa.state.AgentRun`; terminal = done/error/stopped).
_RUNNING_STATUSES = frozenset({"running"})

# Health severity ladder — a higher number is worse. Used to detect a WORSENING.
_HEALTH_ORDER = {"info": 0, "ok": 0, "warn": 1, "alert": 2}

# Event kinds the watcher emits, and which of them are "important" (→ notify).
_KIND_FAILURES = "failures_new"
_KIND_HEALTH = "health_worsened"
_KIND_APPROVALS = "approvals_new"
_KIND_RUN_STARTED = "run_started"
_KIND_RUN_FINISHED = "run_finished"

# Backoff ceiling for the never-crash loop (seconds).
_BACKOFF_CAP = 60.0

# Loop state kept at module level so a series of ``once=True`` pokes chains snapshots
# and de-dupes across calls exactly as the long-running loop would.
#   prev            — the previous snapshot (``None`` until the first tick baselines)
#   last_alert_fp   — fingerprint of the last important set we alerted on (de-dupe)
_STATE: dict = {"prev": None, "last_alert_fp": None}


def _live_enabled() -> bool:
    """Whether the live reasoning path is on (``LHASSA_ORG_LIVE``; default OFF)."""
    return (os.getenv("LHASSA_ORG_LIVE", "") or "").strip().lower() in _TRUTHY


def _resolve_config(config: "Optional[Config]") -> Config:
    return config if config is not None else Config()


def _instance_id(config: Config) -> str:
    return getattr(config, "instance_id", None) or Config.instance_id


# ── agent-run inspection (the "is something running" signal) ──────────────────────


def _run_statuses() -> dict:
    """Snapshot of ``state.agent_runs`` reduced to ``{run_id: status}``. Never raises.

    Read lazily (``state`` is root-level infra) and defensively so a missing/odd
    state degrades to empty rather than breaking a tick.
    """
    try:
        from lhassa.state import state

        return {rid: run.status for rid, run in state.agent_runs.items()}
    except Exception:  # pragma: no cover - state is always importable
        return {}


def is_active(config: Optional[Config] = None) -> bool:
    """True if any tracked agent run is in a running status (something executing).

    This is what flips the loop from the idle cadence to the short active cadence.
    """
    return any(status in _RUNNING_STATUSES for status in _run_statuses().values())


# ── the pure, deterministic diff ($0) ─────────────────────────────────────────────


def _count(sig: dict, key: str) -> int:
    return len(sig.get(key) or [])


def _diff_runs(prev_runs: dict, curr_runs: dict) -> list[dict]:
    """Run start/finish events from two ``{run_id: status}`` maps."""
    events: list[dict] = []
    for rid, status in curr_runs.items():
        prev_status = prev_runs.get(rid)
        running = status in _RUNNING_STATUSES
        if prev_status is None:
            # A newly-seen run: still running → started; already terminal → it both
            # started and finished within the interval, report the finish.
            if running:
                events.append(
                    {"kind": _KIND_RUN_STARTED, "level": "info",
                     "summary": f"run {rid} started"}
                )
            else:
                events.append(
                    {"kind": _KIND_RUN_FINISHED, "level": "info",
                     "summary": f"run {rid} finished ({status})"}
                )
        else:
            was_running = prev_status in _RUNNING_STATUSES
            if was_running and not running:
                events.append(
                    {"kind": _KIND_RUN_FINISHED, "level": "info",
                     "summary": f"run {rid} finished ({status})"}
                )
            elif not was_running and running:
                events.append(
                    {"kind": _KIND_RUN_STARTED, "level": "info",
                     "summary": f"run {rid} started"}
                )
    return events


def detect_events(prev_signals: Optional[dict], curr_signals: dict) -> list[dict]:
    """Diff two :func:`gather_signals` snapshots into notable events.

    Pure and deterministic — no I/O, no LLM. Each event is
    ``{kind, level, summary}``. Notable transitions:

      * **NEW failures** — the failure-marker count rose (``failures_new``).
      * **health WORSENED** — ``health.worst_level`` climbed to ``warn`` / ``alert``
        (``health_worsened``; ``level`` carries the new worst level).
      * **NEW pending approvals** — the pending-approval count rose (``approvals_new``).
      * **run STARTED / FINISHED** — ``agent_runs`` changed (``run_started`` /
        ``run_finished``).

    ``prev_signals`` being ``None`` (the loop's first tick) or an identical snapshot
    yields ``[]`` — the watcher reports change, never the standing baseline.
    """
    if prev_signals is None or not isinstance(curr_signals, dict):
        return []

    events: list[dict] = []

    # NEW failures — count of failure/offline/deferred markers rose.
    prev_fail, curr_fail = _count(prev_signals, "failures"), _count(curr_signals, "failures")
    if curr_fail > prev_fail:
        new = curr_fail - prev_fail
        events.append(
            {
                "kind": _KIND_FAILURES,
                "level": "alert",
                "summary": f"{new} new failure(s) (total {curr_fail})",
            }
        )

    # health WORSENED — worst level climbed into warn/alert territory.
    prev_h = (prev_signals.get("health") or {}).get("worst_level", "info")
    curr_h = (curr_signals.get("health") or {}).get("worst_level", "info")
    if (
        _HEALTH_ORDER.get(curr_h, 0) > _HEALTH_ORDER.get(prev_h, 0)
        and curr_h in ("warn", "alert")
    ):
        failing = (curr_signals.get("health") or {}).get("failing") or []
        detail = f": {', '.join(failing)}" if failing else ""
        events.append(
            {
                "kind": _KIND_HEALTH,
                "level": curr_h,
                "summary": f"health={curr_h} (was {prev_h}){detail}",
            }
        )

    # NEW pending approvals — the parked-work queue grew.
    prev_ap, curr_ap = _count(prev_signals, "approvals"), _count(curr_signals, "approvals")
    if curr_ap > prev_ap:
        new = curr_ap - prev_ap
        events.append(
            {
                "kind": _KIND_APPROVALS,
                "level": "warn",
                "summary": f"{new} new pending approval(s) (total {curr_ap})",
            }
        )

    # run STARTED / FINISHED — agent_runs changed.
    events.extend(
        _diff_runs(prev_signals.get("agent_runs") or {}, curr_signals.get("agent_runs") or {})
    )

    return events


def _is_important(event: dict) -> bool:
    """An event that warrants a push notification (not just an episode).

    Important = new failures, a *health-alert* worsening, or new pending approvals.
    A health *warn* worsening and run start/finish are logged (episode) only.
    """
    kind = event.get("kind")
    if kind in (_KIND_FAILURES, _KIND_APPROVALS):
        return True
    if kind == _KIND_HEALTH:
        return event.get("level") == "alert"
    return False


def _fingerprint(events: list[dict]) -> str:
    """A stable fingerprint of a set of events (for the de-dupe seam)."""
    return "|".join(sorted(f"{e.get('kind')}:{e.get('summary')}" for e in events))


def _summarize(events: list[dict]) -> str:
    return "; ".join(str(e.get("summary") or e.get("kind")) for e in events)


# ── the reaction (episode always; notify + think only when warranted) ─────────────

# Cap on how many pending approvals get an inline Approve/Deny row (keep the
# keyboard short even when the queue is long).
_APPROVAL_KEYBOARD_CAP = 5


async def _approval_keyboard(instance_id: str) -> Optional[dict]:
    """An inline keyboard with an Approve/Deny row per pending approval, or ``None``.

    Callback data is the simple ``"approve:<id>"`` / ``"deny:<id>"`` encoding the
    inbound Telegram poller parses (:mod:`lhassa.notify.telegram_inbound`). Never
    raises — a lookup failure (or nothing pending) degrades to ``None`` (no keyboard
    attached, the alert is still sent as plain text).
    """
    try:
        from lhassa.security import approval as _approval

        pending = await _approval.pending(instance_id)
    except Exception as exc:
        logger.debug("monitor watcher: could not build approval keyboard: {}", exc)
        return None
    if not pending:
        return None

    rows = []
    for ap in pending[:_APPROVAL_KEYBOARD_CAP]:
        ap_id = ap.get("id")
        if not ap_id:
            continue
        rows.append(
            [
                {"text": f"✅ Approve {ap_id}", "callback_data": f"approve:{ap_id}"},
                {"text": f"❌ Deny {ap_id}", "callback_data": f"deny:{ap_id}"},
            ]
        )
    return {"inline_keyboard": rows} if rows else None


async def react(events: list[dict], *, department: str, config: Optional[Config] = None) -> dict:
    """React to detected events: ALWAYS log an episode; notify only when important.

    Deterministic and $0 by default:

      * **Episode** — one monitor ``Episode`` summarizing the events is always
        appended (observability; no LLM, ``costo_usd=0``).
      * **Notify** — if any event is *important* AND ``monitor_notify`` is on, a
        concise deterministic message is pushed through :func:`notify`. A "last
        alerted" fingerprint DE-DUPES a standing condition: an identical important
        set does not re-notify. When no important event is present the fingerprint
        is cleared, so a cleared-then-recurring condition alerts again (transition,
        not persistence).
      * **Think** — only when ``monitor_think`` AND ``LHASSA_ORG_LIVE`` are both on
        is :func:`monitor_pass` called for a richer analysis (the sole token spend;
        OFF by default). Its failure never breaks the reaction.

    Never raises. Returns a small summary dict (for tests / a manual poke).
    """
    config = _resolve_config(config)
    instance_id = _instance_id(config)
    events = events or []
    important = [e for e in events if _is_important(e)]
    notified = False

    # 1) Episode — always, deterministic, $0.
    with contextlib.suppress(Exception):
        await db.init_db()
        ep = Episode.begin(despertar_por="monitor", instance_id=instance_id)
        ep.finish(
            decision="monitor: watch",
            accion=f"monitor:{department}",
            resultado=f"{len(events)} event(s): {_summarize(events)}"[:2000],
            costo_usd=0.0,
        )
        await ep.append()

    # 2) Notify — important only, de-duped against the last alerted fingerprint.
    if important:
        fp = _fingerprint(important)
        if getattr(config, "monitor_notify", True) and fp != _STATE.get("last_alert_fp"):
            message = f"monitor · {department}: {_summarize(important)}"
            level = "error" if any(e.get("level") == "alert" for e in important) else "warning"

            # A NEW pending approval gets an inline Approve/Deny keyboard attached
            # (Telegram-specific; other channels simply ignore the extra kwarg).
            extra: dict = {}
            if any(e.get("kind") == _KIND_APPROVALS for e in important):
                with contextlib.suppress(Exception):
                    keyboard = await _approval_keyboard(instance_id)
                    if keyboard:
                        extra["reply_markup"] = keyboard

            with contextlib.suppress(Exception):
                # Lazy import: keep the module's import graph shell-agnostic.
                from lhassa.notify.dispatch import notify

                notified = await notify(message, level=level, config=config, **extra)
            _STATE["last_alert_fp"] = fp
    else:
        # Condition no longer present → forget it so a recurrence alerts afresh.
        _STATE["last_alert_fp"] = None

    # 3) Think — optional, gated, the only token spend; OFF by default.
    if important and getattr(config, "monitor_think", False) and _live_enabled():
        with contextlib.suppress(Exception):
            await monitor_pass(department=department, config=config)

    return {
        "events": len(events),
        "important": len(important),
        "notified": notified,
    }


# ── the adaptive watch loop ───────────────────────────────────────────────────────


def _capture(config: Config) -> dict:
    """One $0 snapshot: the monitor signals + the current agent-run statuses.

    ``gather_signals`` is read by NAME so tests can patch ``watcher.gather_signals``.
    """
    signals = dict(gather_signals(config))
    signals["agent_runs"] = _run_statuses()
    return signals


async def watch_loop(config: Optional[Config] = None, *, once: bool = False) -> float:
    """The adaptive watch loop — gather, diff, react, sleep; never crashes.

    Each iteration: capture a $0 snapshot, :func:`detect_events` against the previous
    one, :func:`react` if anything is notable, then choose the sleep interval —
    ``monitor_active_interval`` when :func:`is_active`, else ``monitor_idle_interval``.
    Any error is logged and the loop backs off exponentially (capped at
    :data:`_BACKOFF_CAP`); it never raises out.

    ``once=True`` runs exactly ONE tick and RETURNS the interval it would have slept
    (for tests + a manual poke — no real sleeping). The continuous loop persists the
    previous snapshot across iterations at module level so consecutive pokes chain.
    """
    config = _resolve_config(config)
    backoff = 1.0

    while True:
        interval = getattr(config, "monitor_idle_interval", 300.0)
        try:
            curr = _capture(config)
            events = detect_events(_STATE.get("prev"), curr)
            if events:
                await react(events, department=getattr(config, "monitor_department", "interns"), config=config)
            _STATE["prev"] = curr
            backoff = 1.0
            interval = (
                getattr(config, "monitor_active_interval", 15.0)
                if is_active(config)
                else getattr(config, "monitor_idle_interval", 300.0)
            )
        except Exception as exc:  # a monitoring failure is data, never a crash
            logger.error("monitor watcher: tick failed — {}", exc)
            interval = min(backoff, _BACKOFF_CAP)
            backoff = min(backoff * 2, _BACKOFF_CAP)

        if once:
            return interval
        await asyncio.sleep(interval)


def run_forever(config: Optional[Config] = None) -> int:
    """Entry point for ``python -m lhassa.org.watcher`` — the always-on ear.

    Gated by ``monitor_enabled`` (default OFF): when disabled it logs once and exits
    cleanly (return 0) without touching signals or the network — the systemd service
    is how the watcher is opted in. Otherwise it runs :func:`watch_loop` forever.
    """
    from lhassa import actionpack
    actionpack.register_all()

    config = _resolve_config(config)
    if not getattr(config, "monitor_enabled", False):
        logger.info(
            "monitor watcher disabled (LHASSA_MONITOR_ENABLED off) — exiting."
        )
        return 0

    logger.info(
        "monitor watcher started: idle={}s active={}s dept={} notify={} think={}",
        getattr(config, "monitor_idle_interval", 300.0),
        getattr(config, "monitor_active_interval", 15.0),
        getattr(config, "monitor_department", "interns"),
        getattr(config, "monitor_notify", True),
        getattr(config, "monitor_think", False),
    )
    try:
        asyncio.run(watch_loop(config))
    except KeyboardInterrupt:  # pragma: no cover - signal path
        logger.info("monitor watcher stopped.")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(run_forever())
