# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa action run``: run one registered action through the governed gate.

The same path a PTC script or a remote task takes: a signed ``Task`` is built,
the allowlist decides (action listed, trust high enough, signature valid), an
action that needs human approval is refused here (approve it in the queue
instead), and only then does ``core.actions.run`` execute it. Nothing here
runs a script directly, so the Waybar menu can switch the power profile
(``lhassa action run power_mode --param mode=saver``) without bypassing the
allowlist.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid

import typer

action_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Run a registered action through the allowlist (deny-by-default).",
)

_FAILURE = re.compile(r"(: failed|error:|errored|timed out|is required|is invalid|not found|unknown action)")


def parse_params(pairs: list[str]) -> dict:
    """``["mode=saver"]`` -> ``{"mode": "saver"}``; a pair without ``=`` is an error."""
    params: dict = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"bad --param {pair!r}: expected key=value")
        params[key] = value
    return params


async def run_governed(name: str, params: dict, instance_id: str) -> dict:
    """Dispatch ``name`` through the allowlist. Returns ``{"ok", "result" | "error"}``; never raises."""
    try:
        from lhassa.actionpack import register_all
        from lhassa.core import actions as _actions
        from lhassa.core.task import Task
        from lhassa.security import allowlist as _allowlist
        from lhassa.security import signing as _signing
        from lhassa.security.trust import Origin, TrustLevel

        register_all()
        if _actions.ACTIONS.get(name) is None:
            return {"ok": False, "error": f"unknown action {name!r}"}
        task = Task(
            id=f"cli_{uuid.uuid4().hex[:12]}",
            instance_id=instance_id,
            accion=name,
            objetivo=str(params.get("objetivo") or ""),
            params=params,
            origen=Origin(trust=TrustLevel.HUMANO, actor_id="owner-cli"),
        )
        task.firma = _signing.sign_task(task)
        decision = _allowlist.allow(task)
        if not decision.allowed:
            return {"ok": False, "error": f"denied: {decision.reason}"}
        if decision.requires_approval:
            return {"ok": False, "error": f"denied: action {name!r} requires human approval; use the approvals queue"}
        result = await _actions.run(name, params)
        return {"ok": True, "result": str(result)}
    except Exception as exc:  # the CLI seam must degrade to a message
        return {"ok": False, "error": f"dispatch error: {exc}"}


@action_app.command("run")
def run_cmd(
    name: str = typer.Argument(..., help="Registered action name, e.g. power_mode."),
    param: list[str] = typer.Option([], "--param", "-p", help="key=value (repeatable)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the outcome as JSON."),
) -> None:
    """Run NAME through the allowlist; exit 1 when it is denied or fails."""
    from lhassa.config import Config

    try:
        params = parse_params(param)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    outcome = asyncio.run(run_governed(name, params, Config.instance_id))
    if json_out:
        typer.echo(json.dumps(outcome))
    else:
        typer.echo(outcome.get("result") or outcome.get("error") or "", err=not outcome["ok"])
    # An action reports failure as text, never by raising.
    failed = not outcome["ok"] or _FAILURE.search(outcome.get("result") or "") is not None
    raise typer.Exit(1 if failed else 0)
