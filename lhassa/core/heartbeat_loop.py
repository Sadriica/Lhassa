# SPDX-License-Identifier: AGPL-3.0-or-later
"""The resident heartbeat loop: import once, beat forever (§2.1).

The oneshot ``scripts/lhassa-heartbeat`` pays the ~400ms interpreter + import
cost on every wake, which is the right trade for a systemd timer firing every
few minutes on a laptop (the process simply doesn't exist between beats). In a
container, though, the shell ``while true; do lhassa-heartbeat; sleep N; done``
loop in ``docker/entrypoint.sh`` pays that same import cost every single
iteration for no reason: nothing about the interpreter or the import graph
changes between beats. This module is the fix: a single warm process that
imports the brain once and then just ticks (``beat()``, sleep, repeat).

Each beat still runs through the ordinary ``core.heartbeat.beat()``, which
runs ``core.step.step()``, so it is exactly as deterministic and $0 as the
oneshot (§2.4); this only changes how often the interpreter starts, never
what a beat does.

The oneshot mode remains the default everywhere (laptop systemd timer,
``docker/entrypoint.sh`` with ``LHASSA_HEARTBEAT_INTERVAL<=0``); this module is
opt-in via ``--loop`` / a positive container interval.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Optional

from loguru import logger

from lhassa.core.heartbeat import beat


def summarize_beat(summary: dict) -> str:
    """Render ``beat()``'s summary dict as the one-line log the oneshot prints.

    Shared by the oneshot script and the resident loop so both surfaces log an
    identical line per beat.
    """
    triggers = summary.get("triggers", 0)
    if not triggers:
        return "heartbeat: nothing to do, sleeping."
    actions = summary.get("actions", 0)
    deferred = summary.get("deferred", 0)
    return f"heartbeat: {triggers} trigger(s), {actions} action(s), {deferred} deferred."


async def run_loop(
    interval: float,
    *,
    instance_id: Optional[str] = None,
    quiet: bool = False,
) -> None:
    """Beat, sleep ``interval`` seconds, repeat, until SIGTERM/SIGINT.

    A signal handler flips a stop flag rather than tearing the loop down mid
    tick: the current beat always finishes, the summary is always logged, and
    the loop then exits instead of sleeping. This is the clean shutdown
    behaviour a container orchestrator (or Ctrl-C) expects on stop.

    A beat never dies for lack of a provider (``beat()``/``step()`` already
    degrade internally per §2.4), but an exception escaping ``beat()`` itself
    is still caught here, logged, and does not stop the loop: a single bad
    tick must not take the whole resident process down with it.
    """
    stop = asyncio.Event()

    def _request_stop(sig_name: str) -> None:
        if not stop.is_set():
            logger.info(f"heartbeat loop: {sig_name} received, stopping after this tick.")
            stop.set()

    loop = asyncio.get_running_loop()
    installed: list[int] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop, sig.name)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            # Some platforms/loops don't support signal handlers; the loop still
            # exits on an unhandled KeyboardInterrupt in that case.
            pass

    try:
        logger.info(f"heartbeat loop: starting, interval={interval}s.")
        while not stop.is_set():
            try:
                summary = await beat(instance_id)
                if not quiet:
                    print(summarize_beat(summary))
            except Exception as exc:  # a single bad tick must not kill the loop
                logger.error(f"heartbeat loop: beat failed (continuing): {exc}")

            if stop.is_set():
                break

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)
        logger.info("heartbeat loop: stopped.")
    finally:
        for sig in installed:
            with contextlib.suppress(Exception):
                loop.remove_signal_handler(sig)
