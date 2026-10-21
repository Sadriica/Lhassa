# SPDX-License-Identifier: AGPL-3.0-or-later
"""Button taps: the inline Approve/Deny an alert or a ``/code`` reply carries.

The callback data encoding is ``"approve:<id>"`` / ``"deny:<id>"`` (the monitor
watcher's alert buttons use the same one, see
``lhassa.org.watcher._approval_keyboard``). A tap resolves the approval through
the same seam as the ``/approve`` / ``/deny`` chat commands
(:func:`lhassa.security.approval.resolve`) and, on Approve, starts whatever the
approval was holding back, as :attr:`~lhassa.chat.types.Reply.followups`:

* a ``code_run`` approval launches the coding run (:func:`launch_code_run`);
* an approved ``mint_task`` task runs now and reports back
  (:func:`lhassa.chat.markers.run_action_and_report`);
* a ``mint_route`` approval turns that capability's route on.

Whether the tap came from the owner is the transport's call; by the time a tap
reaches :func:`handle_callback` it is the owner's.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Optional

from loguru import logger

from lhassa.chat import markers
from lhassa.chat.outbound import resolve_sender, say
from lhassa.chat.types import Button, Reply, Sender

__all__ = ["approval_buttons", "handle_callback", "launch_code_run", "start_followups"]

_CALLBACK_RE = re.compile(r"^(approve|deny):(.+)$")

# Hard safety cap around an approved /code run: the CLI has its own timeout
# (LHASSA_AI_CLI_TIMEOUT, default 600s), this is just a belt-and-braces ceiling so
# a fire-and-forget launch can never hang the process forever.
_CODE_RUN_TIMEOUT = 1800.0  # 30 min

# Tail of a finished run's output included in the completion message.
_CODE_OUTPUT_TAIL = 500


def approval_buttons(ap_id: str) -> tuple[Button, ...]:
    """One Approve/Deny row for exactly one approval id."""
    return (
        Button(f"✅ Approve {ap_id}", f"approve:{ap_id}"),
        Button(f"❌ Deny {ap_id}", f"deny:{ap_id}"),
    )


async def launch_code_run(approval_row: dict, *, config, send: Optional[Sender] = None) -> None:
    """Launch an approved ``code_run`` approval's task. Fire-and-forget; never raises.

    Reuses the EXACT seam the dashboard's Run section launches through
    (:func:`lhassa.agents.code_run.stream_run`: it broadcasts ``agent_started``/
    ``agent_token``/``agent_done`` over ``/ws`` and persists the run through
    ``lhassa.state.state``), so a chat-launched run is indistinguishable from a
    dashboard one. Says "started" immediately, awaits completion (capped by
    :data:`_CODE_RUN_TIMEOUT` on top of the CLI's own timeout), then a final
    status + output-tail message.
    """
    out = resolve_sender(send, config)
    payload = approval_row.get("payload") or {}
    task = str(payload.get("task") or "").strip()
    if not task:
        await say(out, "That approval had no task text on it, nothing to run.")
        return

    try:
        import uuid

        from lhassa.agents import code_run, local_cli
        from lhassa.state import AgentRun, state

        cli = str(payload.get("cli") or "").strip() or local_cli.detect_ai_cli()
        if not local_cli.is_installed(cli):
            installed = ", ".join(local_cli.available_clis()) or "none installed"
            await say(out, f"Can't launch, the {cli!r} CLI isn't available ({installed}).")
            return

        agent = local_cli.make_agent(cli)
        run_id = uuid.uuid4().hex[:8]
        run = AgentRun(id=run_id, name=f"ai-cli ({cli})", status="running", task=task, cli=cli)
        state.register_agent(run)

        await say(out, f"Started: run #{run_id} ({cli}), {task[:80]}")

        try:
            await asyncio.wait_for(
                code_run.stream_run(agent, run_id, run, task), timeout=_CODE_RUN_TIMEOUT
            )
        except asyncio.TimeoutError:
            await say(
                out,
                f"Run #{run_id} is still going after a while, check the "
                "dashboard's Run section.",
            )
            return

        tail = (run.output or "")[-_CODE_OUTPUT_TAIL:]
        await say(out, f"Run #{run_id} {run.status}." + (f"\n{tail}" if tail else ""))
    except Exception as exc:  # a launch failure must never crash the door
        logger.warning(f"chat: code_run launch failed: {exc}")
        await say(out, "I couldn't launch that coding run.")


async def handle_callback(data: str, *, config, send: Optional[Sender] = None) -> Reply:
    """Resolve one tap's ``data``; the :class:`Reply` carries the ack. Never raises.

    * unrecognised data: ``Reply(text="", ack="Unrecognised action.")`` (the
      button is acknowledged, nothing is posted in the chat);
    * a recognised tap: the resolution line is both the ack and the chat
      message, and anything the approval was holding back rides
      ``followups`` (the transport starts them once the ack is out);
    * any failure: ``Reply(text="", ack="")``, a bare acknowledgment.
    """
    try:
        m = _CALLBACK_RE.match(str(data or ""))
        if not m:
            logger.debug(f"chat: unrecognised callback data {data!r}")
            return Reply(text="", ack="Unrecognised action.")

        action, ap_id = m.group(1), m.group(2)
        approved = action == "approve"

        from lhassa.core import db as _db
        from lhassa.security import approval as _approval

        instance_id = getattr(config, "instance_id", None) or "inst_personal"
        await _db.init_db()
        existing = await _db.get_approval(instance_id, ap_id)
        if existing is None:
            ack_text = f"No pending approval {ap_id}."
        else:
            try:
                await _approval.resolve(
                    instance_id,
                    ap_id,
                    approved=approved,
                    by_actor="telegram",
                    approver=_approval.TrustedApprover("owner", "telegram-owner"),
                )
            except _approval.ApprovalResolutionConflict:
                # A double-tap (or a race with another surface): the approval is
                # no longer pending; acknowledge honestly and launch NOTHING.
                existing = None
                ack_text = f"Approval {ap_id} was already resolved."
            except _approval.ApprovalAuthorizationError as exc:
                existing = None
                ack_text = f"Approval refused: {exc}"
            except KeyError:
                existing = None
                ack_text = f"No pending approval {ap_id}."
            else:
                ack_text = f"{'Approved' if approved else 'Denied'} {ap_id}."

        followups: list = []
        if existing is not None and approved:
            tipo = existing.get("tipo")
            # A resolved code_run approval LAUNCHES the run; Approve only, Deny is
            # a calm acknowledgment and nothing runs. Fire-and-forget so the door
            # is never blocked on the CLI's runtime.
            if tipo == "code_run":
                row = existing
                followups.append(lambda: launch_code_run(row, config=config, send=send))

            # An approved mint_task (e.g. a protected-root read) runs NOW and
            # reports back: resolve() just released the task to 'pendiente', so
            # this is the same early-runner+report the [[action:]] marker uses;
            # if it fails, the task stays pending for the heartbeat.
            if tipo == "task":
                pl = existing.get("payload") or {}
                if pl.get("source") == "mint_task" and pl.get("task_id") and pl.get("accion"):
                    args = (
                        instance_id,
                        str(pl["task_id"]),
                        str(pl["accion"]),
                        dict(pl.get("params") or {}),
                        config,
                    )
                    followups.append(lambda: markers.run_action_and_report(*args, send=send))

            # A resolved mint_route approval TURNS ON that capability's route:
            # deterministic, $0, DATA only (no code is generated or executed).
            if tipo == "mint_route":
                from lhassa.notify import capabilities as _capabilities

                _capabilities.apply_approved_route(existing.get("payload") or {}, config=config)

        return Reply(text=ack_text, ack=ack_text, followups=tuple(followups))
    except Exception as exc:  # a malformed tap must never crash the door
        logger.warning(f"chat: callback handling failed: {exc}")
        return Reply(text="", ack="")


def start_followups(reply: Optional[Reply]) -> None:
    """Start a reply's follow-ups, fire and forget. Never raises."""
    if reply is None:
        return
    for factory in reply.followups:
        with contextlib.suppress(Exception):
            asyncio.ensure_future(factory())
