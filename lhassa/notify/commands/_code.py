# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/code``: launch an agentic coding run, gated behind an explicit approval.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

from loguru import logger

from ._registry import register_command
from ._shared import _config, _oops

__all__ = ["_CODE_APPROVAL_MARKER"]

# A chat-borne task is capped so a pasted wall of text can't balloon the payload
# or the eventual CLI invocation.
_CODE_MAX_TASK = 500

# Appended to a successful /code reply, then stripped and parsed by the inbound
# poller (:mod:`lhassa.notify.telegram_inbound`) to attach the inline Approve/Deny
# keyboard to THIS reply; the same "hidden marker" discipline as the research/
# remember markers in that module. Never shown to the owner.
_CODE_APPROVAL_MARKER = "[[approval_id: {ap_id}]]"


def _code_cwd(config) -> str:
    """The working directory an approved /code run would launch in.

    Mirrors :func:`lhassa.api.code_routes._agent_cwd` (the dashboard Run
    section's own resolution: ``config.agent_dir`` if set, else the process
    cwd); for DISPLAY purposes at park time only; the actual launch re-resolves
    it the same way. Never raises.
    """
    try:
        from lhassa.api.code_routes import _agent_cwd

        return str(_agent_cwd())
    except Exception:
        return "?"


@register_command("code")
async def _cmd_code(args: str, *, config=None) -> str:
    """``/code <task>``: park an agentic coding run for approval, never run it here.

    Launching a coding agent means write access to the owner's files, so it NEVER
    runs directly off a chat message: this parks a ``tipo="code_run"`` approval
    (payload: ``accion``, the capped ``task`` text, the auto-detected ``cli``: the
    same detection :func:`lhassa.agents.local_cli.detect_ai_cli` the dashboard's Run
    section uses; and the resolved ``cwd``) and replies in Lhassa's own voice that
    it is waiting on the owner. The inbound poller attaches an inline Approve/Deny
    keyboard to this exact reply (see :data:`_CODE_APPROVAL_MARKER`); a tap on
    Approve is what actually launches the run.
    """
    task = (args or "").strip()
    if not task:
        return (
            "Usage: /code <task>; I'll park it for your approval before touching "
            "any files; tap Approve (or /approve <id>) to launch it."
        )
    task = task[:_CODE_MAX_TASK]

    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.agents import local_cli
        from lhassa.core import db as _db
        from lhassa.security import approval as _approval

        cli = local_cli.detect_ai_cli()
        cwd = _code_cwd(cfg)

        await _db.init_db()
        ap_id = await _approval.request_approval(
            instance_id,
            tipo="code_run",
            payload={
                "accion": "code_run",
                "task": task,
                "cli": cli,
                "cwd": cwd,
                "actor_id": "telegram",
                "origen_trust": "humano",
            },
        )
    except Exception as exc:
        logger.warning(f"commands: /code failed: {exc}")
        return _oops("park that coding task")

    reply = (
        f"Parked: I'll run '{cli}' on your files for \"{task}\" once you approve; "
        f"nothing runs yet. A button is coming; or /approve {ap_id} / /deny {ap_id}."
    )
    return reply + "\n" + _CODE_APPROVAL_MARKER.format(ap_id=ap_id)
