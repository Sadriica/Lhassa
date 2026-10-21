# SPDX-License-Identifier: AGPL-3.0-or-later
"""The MONITOR WATCHER; an always-on, $0 "ear" for the environment.

Where :mod:`lhassa.org.monitor` provides the *capability* to gather signals and
reason over them, this module is the standing **loop** that makes the monitor a
constant presence. It watches cheaply and continuously, and only wakes the brain /
alerts the owner when something notable actually happens.

Design (agreed with the owner):

  * **$0 while idle.** Each tick is deterministic signal-gathering
    (:func:`lhassa.org.monitor.gather_signals`: read-only, no LLM) diffed against
    the previous snapshot. Nothing is written and no token is spent unless a notable
    event is detected. The ONLY place a token could ever be spent is an optional
    live "think" (:func:`lhassa.org.monitor.monitor_pass`), OFF by default and
    additionally gated behind ``LHASSA_ORG_LIVE``.
  * **Elastic cadence.** While nothing is running the loop checks every
    ``monitor_idle_interval`` seconds (default 300); while something IS running
    (an ``agent_runs`` entry in a running status) it listens on the short
    ``monitor_active_interval`` (default 15) so a failure/finish is caught quickly.
  * **Sensor, not governor.** The watcher decides only when the deterministic
    conformance pass is due. It invokes the closed ``temple_run`` gateway;
    Temple delegates ``check_integrity`` to ``guardian``, which executes and
    reports upward through the normal down-execute-up hierarchy.
  * **React on transition, not on persistence.** :func:`detect_events` diffs two
    snapshots into notable events; :func:`react` always logs a deterministic monitor
    Episode and pushes a Telegram alert only for *important* events (failures /
    health-alert / pending-approvals). A "last alerted" fingerprint de-dupes a
    STANDING condition so it never re-notifies every tick.
  * **Never crashes.** Every tick is wrapped: an error is logged and the loop backs
    off (exponential, capped); a monitoring failure is data, never a crash.

The loop is gated by ``monitor_enabled`` (default OFF, opt-in). The systemd service
``scripts/systemd/lhassa-monitor.service`` is how it is turned on; the module
no-ops + exits cleanly when disabled.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from typing import Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.episode import Episode

# Imported by NAME (not via the module) so tests can patch ``watcher.gather_signals``
# / ``watcher.monitor_pass`` directly, mirroring the monitor's own test seams.
from lhassa.org.monitor import gather_signals, monitor_pass

# Truthy env values that flip a gate on; identical to the monitor/executor gate.
_TRUTHY = {"1", "true", "yes", "on"}

# Agent-run statuses that mean "something is executing right now" (see
# :class:`lhassa.state.AgentRun`; terminal = done/error/stopped).
_RUNNING_STATUSES = frozenset({"running"})

# Health severity ladder; a higher number is worse. Used to detect a WORSENING.
_HEALTH_ORDER = {"info": 0, "ok": 0, "warn": 1, "alert": 2}

# Event kinds the watcher emits, and which of them are "important" (→ notify).
_KIND_FAILURES = "failures_new"
_KIND_HEALTH = "health_worsened"
_KIND_APPROVALS = "approvals_new"
_KIND_RUN_STARTED = "run_started"
_KIND_RUN_FINISHED = "run_finished"
_KIND_CONFORMANCE = "conformance_failed"

# Backoff ceiling for the never-crash loop (seconds).
_BACKOFF_CAP = 60.0

# Loop state kept at module level so a series of ``once=True`` pokes chains snapshots
# and de-dupes across calls exactly as the long-running loop would.
#   prev            ; the previous snapshot (``None`` until the first tick baselines)
#   last_alert_fp   ; fingerprint of the last important set we alerted on (de-dupe)
#   delegated_fps    ; fingerprints of monitor delegations already minted as
#                       org_run tasks, so a STANDING finding is not re-minted
#                       every tick (mirrors ``last_alert_fp``'s de-dupe spirit).
_STATE: dict = {
    "prev": None,
    "last_alert_fp": None,
    "delegated_fps": set(),
    "last_integrity_at": 0.0,
}


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


# Internal trail the temple's deliver seam appends to a resultado ("· reports ↑
# temple"): true and worth logging, but it is Lhassa's own plumbing, not the
# owner's news. Same for the correlation id a run carries.
_HOP_TRAIL = re.compile(r"\s*·\s*reports\s*↑.*$", re.IGNORECASE)
_RUN_ID = re.compile(r"\s*\(run\s+[A-Za-z0-9_]+\)")


def _failure_reason(failure: dict, limit: int = 90) -> str:
    """What actually went WRONG in a failure episode, in one short clause.

    Reads ``resultado`` first: an episode's ``decision`` states what the brain
    set out to do ("guardian executes check_integrity"), so an alert built from
    it told the owner the INTENT and never the outcome; the failure that
    prompted this read "check_integrity: fail; 1 critical and 1 warning". The
    internal hop trail and the run id are stripped (plumbing, not news), and
    ``decision``/``accion`` remain the fallback when a resultado is empty.
    """
    raw = str(failure.get("resultado") or "").strip()
    raw = _RUN_ID.sub("", _HOP_TRAIL.sub("", raw)).strip(" ;·")
    if not raw:
        raw = str(failure.get("decision") or failure.get("accion") or "episode").strip()
    return raw[:limit].rstrip(" ;,·")


def detect_events(prev_signals: Optional[dict], curr_signals: dict) -> list[dict]:
    """Diff two :func:`gather_signals` snapshots into notable events.

    Pure and deterministic; no I/O, no LLM. Each event is
    ``{kind, level, summary}``. Notable transitions:

      * **NEW failures**: failure episodes not present in the previous snapshot,
        diffed by episode id and named in the summary (``failures_new``).
      * **health WORSENED**: ``health.worst_level`` climbed to ``warn`` / ``alert``
        (``health_worsened``; ``level`` carries the new worst level).
      * **NEW pending approvals**: the pending-approval count rose (``approvals_new``).
      * **run STARTED / FINISHED**: ``agent_runs`` changed (``run_started`` /
        ``run_finished``).

    ``prev_signals`` being ``None`` (the loop's first tick) or an identical snapshot
    yields ``[]``: the watcher reports change, never the standing baseline.
    """
    if prev_signals is None or not isinstance(curr_signals, dict):
        return []

    events: list[dict] = []

    # NEW failures; failure episodes whose id was NOT in the previous snapshot.
    # Diffing by identity (not by count) means an old failure aging out of the
    # freshness window can never mask (or masquerade as) a genuinely new one,
    # and the alert names only what happened in THIS tick; the standing total is
    # deliberately not quoted (it read as if it all just happened).
    prev_ids = {f.get("id") for f in (prev_signals.get("failures") or [])}
    fresh = [
        f for f in (curr_signals.get("failures") or []) if f.get("id") not in prev_ids
    ]
    if fresh:
        # Failures arrive in batches that share ONE cause (five hygiene tasks
        # refused by the same gate, say). Listing the identical reason three
        # times and calling the rest "+1 more" hid both the count and the
        # variety, so group by reason and quote each one once with its tally.
        by_reason: dict[str, int] = {}
        for f in fresh:
            reason = _failure_reason(f)
            by_reason[reason] = by_reason.get(reason, 0) + 1
        ordered = sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))
        names = "; ".join(
            f"{reason} ×{count}" if count > 1 else reason
            for reason, count in ordered[:3]
        )
        extra = f" (+{len(ordered) - 3} more)" if len(ordered) > 3 else ""
        noun = "failure" if len(fresh) == 1 else "failures"
        events.append(
            {
                "kind": _KIND_FAILURES,
                "level": "alert",
                "summary": f"{len(fresh)} new {noun}: {names}{extra}",
            }
        )

    # health WORSENED; worst level climbed into warn/alert territory.
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

    # NEW pending approvals; the parked-work queue grew.
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

    # run STARTED / FINISHED; agent_runs changed.
    events.extend(
        _diff_runs(prev_signals.get("agent_runs") or {}, curr_signals.get("agent_runs") or {})
    )

    return events


def _is_important(event: dict) -> bool:
    """An event that warrants a push notification (not just an episode).

    Important = new failures, a *health-alert* worsening, or new pending approvals.
    A health *warn* worsening and run start/finish are logged (episode) only.
    """
    # An event the hierarchy ALREADY delivered to the owner is still an event
    # (logged, and it can still become work through the delegation seam); it is
    # simply not a second push. See the conformance event in `watch_loop`.
    if event.get("already_delivered"):
        return False
    kind = event.get("kind")
    if kind in (_KIND_FAILURES, _KIND_APPROVALS):
        return True
    if kind == _KIND_CONFORMANCE:
        return True
    if kind == _KIND_HEALTH:
        return event.get("level") == "alert"
    return False


def _fingerprint(events: list[dict]) -> str:
    """A stable fingerprint of a set of events (for the de-dupe seam)."""
    return "|".join(sorted(f"{e.get('kind')}:{e.get('summary')}" for e in events))


def _summarize(events: list[dict]) -> str:
    return "; ".join(str(e.get("summary") or e.get("kind")) for e in events)


# ── monitor findings become work (Etapa 3; findings become work) ─────────────────


async def _delegate_findings(
    report: dict, *, department: str, instance_id: str
) -> list[str]:
    """Turn each delegation in a monitor ``report`` into a minted ``org_run`` task.

    De-duped by a ``(department, fix)`` fingerprint kept in
    ``_STATE["delegated_fps"]`` so a STANDING finding (the same risk surfacing
    every tick) is minted exactly ONCE, not on every reaction. Minting itself is
    $0 (``mint_task`` only writes a task row); the org_run action actually spends
    anything only at its own next-tick execution, offline unless
    ``LHASSA_ORG_LIVE``. Never raises; a minting failure is skipped, not fatal.
    Returns short notes (``"minted org_run→<dept>: <fix>"``) for the caller to
    fold into the Telegram alert text.
    """
    notes: list[str] = []
    delegations = report.get("delegations") or []
    if not delegations:
        return notes

    from lhassa.core import dispatch as _dispatch
    from lhassa.security.trust import Origin, TrustLevel

    minted_fps: set = set(_STATE.get("delegated_fps") or set())
    for item in delegations:
        if not isinstance(item, dict):
            continue
        dept = str(item.get("department") or "").strip() or department
        fix = str(item.get("fix") or "").strip()
        if not fix:
            continue
        fp = f"{dept}:{fix}"
        if fp in minted_fps:
            continue
        try:
            origin = Origin(trust=TrustLevel.INTERNO, actor_id="monitor")
            task_id, _estado = await _dispatch.mint_task(
                instance_id,
                "org_run",
                objetivo=fix[:200],
                params={"goal": fix, "department": dept},
                origin=origin,
            )
            if task_id:
                minted_fps.add(fp)
                notes.append(f"minted org_run→{dept}: {fix[:80]}")
        except Exception as exc:
            logger.debug("monitor watcher: could not mint delegation for {}: {}", dept, exc)

    _STATE["delegated_fps"] = minted_fps
    return notes


# ── the reaction (episode always; notify + think only when warranted) ─────────────

# Cap on how many pending approvals get an inline Approve/Deny row (keep the
# keyboard short even when the queue is long).
_APPROVAL_KEYBOARD_CAP = 5


async def _approval_keyboard(instance_id: str) -> Optional[dict]:
    """An inline keyboard with an Approve/Deny row per pending approval, or ``None``.

    Callback data is the simple ``"approve:<id>"`` / ``"deny:<id>"`` encoding the
    chat engine resolves (:mod:`lhassa.chat.callbacks`). Never
    raises; a lookup failure (or nothing pending) degrades to ``None`` (no keyboard
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

      * **Episode**: one monitor ``Episode`` summarizing the events is always
        appended (observability; no LLM, ``costo_usd=0``).
      * **Notify**: if any event is *important* AND ``monitor_notify`` is on, a
        concise deterministic message is pushed through :func:`notify`. A "last
        alerted" fingerprint DE-DUPES a standing condition: an identical important
        set does not re-notify. When no important event is present the fingerprint
        is cleared, so a cleared-then-recurring condition alerts again (transition,
        not persistence).
      * **Think**: only when ``monitor_think`` AND ``LHASSA_ORG_LIVE`` are both on
        is :func:`monitor_pass` called for a richer analysis (the sole token spend;
        OFF by default). Its failure never breaks the reaction.
      * **Delegate**: when ``monitor_delegate`` is on, each delegation in the
        monitor's report (the OFFLINE, deterministic one by default; still $0;
        a richer live one if ``think`` also ran above) is minted as an
        ``org_run`` task, de-duped by fingerprint so a standing condition mints
        once, not every tick. Minted findings are folded into the Telegram alert
        text alongside the important events.

    Never raises. Returns a small summary dict (for tests / a manual poke).
    """
    config = _resolve_config(config)
    instance_id = _instance_id(config)
    events = events or []
    important = [e for e in events if _is_important(e)]
    notified = False

    # 1) Episode; always, deterministic, $0.
    with contextlib.suppress(Exception):
        await db.init_db()
        ep = Episode.begin(despertar_por="monitor", instance_id=instance_id)
        # Attribution: the alert below deliberately does NOT ride org/deliver's
        # per-hop chain; the monitor is Lhassa's own $0 ear (same documented
        # exception class as health/checks.py), and wrapping every alert in
        # deliver hops would refill the episode log with the very paperwork
        # recall demotes. The audit trail is kept the cheap way instead: this
        # episode names the observing department via dominio, so "who told the
        # owner" is answerable without the extra hops.
        ep.dominio = department
        ep.finish(
            decision="monitor: watch",
            accion=f"monitor:{department}",
            resultado=f"{len(events)} event(s): {_summarize(events)}"[:2000],
            costo_usd=0.0,
        )
        await ep.append()

    # 1.5) Delegate; findings become work. Reuses whatever report monitor_pass
    # already produced for "think" (live) below when available; otherwise pulls
    # the OFFLINE report fresh; deterministic, $0, no extra model call.
    delegate_notes: list[str] = []
    live_report: Optional[dict] = None
    if important and getattr(config, "monitor_think", False) and _live_enabled():
        with contextlib.suppress(Exception):
            live_report = await monitor_pass(department=department, config=config)

    if getattr(config, "monitor_delegate", False):
        with contextlib.suppress(Exception):
            report = live_report if live_report is not None else await monitor_pass(
                department=department, config=config
            )
            delegate_notes = await _delegate_findings(
                report, department=department, instance_id=instance_id
            )

    # 2) Notify; important only, de-duped against the last alerted fingerprint.
    if important:
        fp = _fingerprint(important)
        if getattr(config, "monitor_notify", True) and fp != _STATE.get("last_alert_fp"):
            from lhassa.persona import format_alert

            # The message says WHAT happened, not which internal seat noticed
            # it. "monitor · interns: " prefixed every alert with the watcher's
            # own name and its department, which is Lhassa's org plumbing and
            # read as noise to the one person receiving it; the attribution
            # still lives where it belongs, on the episode logged above
            # (dominio=<department>, despertar_por="monitor").
            body = _summarize(important)
            message = format_alert(
                body,
                level="alert" if any(e.get("level") == "alert" for e in important) else "warn",
            )
            if delegate_notes:
                message += " | " + "; ".join(delegate_notes)
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

                notified = await notify(message, level=level, config=config, kind="alert", **extra)
            _STATE["last_alert_fp"] = fp
    else:
        # Condition no longer present → forget it so a recurrence alerts afresh.
        _STATE["last_alert_fp"] = None

    return {
        "events": len(events),
        "important": len(important),
        "notified": notified,
        "delegated": len(delegate_notes),
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
    """The adaptive watch loop; gather, diff, react, sleep; never crashes.

    Each iteration: capture a $0 snapshot, :func:`detect_events` against the previous
    one, :func:`react` if anything is notable, then choose the sleep interval;
    ``monitor_active_interval`` when :func:`is_active`, else ``monitor_idle_interval``.
    Any error is logged and the loop backs off exponentially (capped at
    :data:`_BACKOFF_CAP`); it never raises out.

    ``once=True`` runs exactly ONE tick and RETURNS the interval it would have slept
    (for tests + a manual poke; no real sleeping). The continuous loop persists the
    previous snapshot across iterations at module level so consecutive pokes chain.
    """
    config = _resolve_config(config)
    backoff = 1.0

    while True:
        interval = getattr(config, "monitor_idle_interval", 300.0)
        try:
            curr = _capture(config)
            events = detect_events(_STATE.get("prev"), curr)
            now = asyncio.get_running_loop().time()
            last_integrity = float(_STATE.get("last_integrity_at") or 0.0)
            integrity_due = (
                getattr(config, "integrity_enabled", False)
                and (
                    not last_integrity
                    or now - last_integrity
                    >= max(
                        60.0,
                        float(getattr(config, "integrity_interval", 3600.0)),
                    )
                )
            )
            if integrity_due:
                from lhassa import actionpack
                from lhassa.actionpack._temple import temple_run

                # The watcher is only the clock/sensor. The work itself follows
                # Lhassa → temple → guardian, then returns through org.deliver.
                actionpack.register_all()
                record = await temple_run(
                    {
                        "action": "check_integrity",
                        "params": {"config": config, "trigger": "watcher"},
                        "config": config,
                        "instruction": "scheduled conformance pass",
                    },
                    return_record=True,
                )
                # Read the structured verdict from the conformance_runs table;
                # the source of truth; instead of parsing the action's return
                # string (the string is what reaches the owner's screen, and a
                # raw JSON dump is not a notification). One guard first: when
                # the action ERRORED, no completed run row was written, so the
                # latest row is a stale, possibly-green PREVIOUS run; reading
                # it would mask the failure as a pass. An error therefore
                # fails closed here and never consults the table.
                integrity = {
                    "status": "error",
                    "run_id": "unknown",
                    "findings_critical": 1,
                    "findings_warn": 0,
                }
                action_errored = "check_integrity: error:" in str(
                    (record or {}).get("result") or ""
                )
                if not action_errored:
                    try:
                        from lhassa.core import db as _db

                        row = await _db.latest_conformance_run(config.instance_id)
                        if row:
                            integrity = {
                                "status": str(row.get("status") or "error"),
                                "run_id": row.get("id") or "unknown",
                                "findings_critical": int(row.get("findings_critical") or 0),
                                "findings_warn": int(row.get("findings_warn") or 0),
                            }
                    except Exception as exc:  # the watcher never crashes on a read
                        logger.debug(f"watcher: could not read conformance run: {exc}")
                _STATE["last_integrity_at"] = now
                if integrity["status"] != "pass":
                    critical = int(integrity.get("findings_critical") or 0)
                    warnings = int(integrity.get("findings_warn") or 0)
                    events.append(
                        {
                            "kind": _KIND_CONFORMANCE,
                            "level": "alert" if critical else "warn",
                            "summary": (
                                f"integrity={integrity['status']}: "
                                f"{critical} critical, {warnings} warning finding(s) "
                                f"(run {integrity.get('run_id') or 'unknown'})"
                            ),
                            # The watcher is the CLOCK here, not the discoverer:
                            # it delegated this run down the chart, and the
                            # result came back up through `org.deliver`, which
                            # already told the owner in the owner's own words
                            # (`notify.voice.phrase`). Pushing this line too
                            # sent a second toast about one run, worded by the
                            # machine and carrying a run id: the owner saw both
                            # side by side and read them as contradicting.
                            "already_delivered": True,
                        }
                    )
                # The episodes that run just wrote (temple-execute + the deliver
                # hops) are OURS. Re-capturing folds them into the baseline the
                # tail of this loop stores, so the next tick does not rediscover
                # them as a brand-new failure and send a THIRD message about one
                # run. The watcher reports what it finds, never what it caused.
                curr = _capture(config)
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
            logger.error("monitor watcher: tick failed; {}", exc)
            interval = min(backoff, _BACKOFF_CAP)
            backoff = min(backoff * 2, _BACKOFF_CAP)

        if once:
            return interval
        await asyncio.sleep(interval)


def run_forever(config: Optional[Config] = None) -> int:
    """Entry point for ``python -m lhassa.org.watcher``: the always-on ear.

    Gated by ``monitor_enabled`` (default OFF): when disabled it logs once and exits
    cleanly (return 0) without touching signals or the network; the systemd service
    is how the watcher is opted in. Otherwise it runs :func:`watch_loop` forever.
    """
    from lhassa import actionpack
    actionpack.register_all()

    config = _resolve_config(config)
    if not getattr(config, "monitor_enabled", False):
        logger.info(
            "monitor watcher disabled (LHASSA_MONITOR_ENABLED off); exiting."
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
