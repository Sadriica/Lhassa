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
import os
import re
import stat
import uuid

import typer

action_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Run a registered action through the allowlist (deny-by-default).",
)

# The CLI signs as the owner (HUMANO trust), so it must not be a general door: any
# process with a shell could run every allowlisted action that does not need an
# approval. Only the actions listed here may be run from it. The owner can widen the
# list ONLY through the file ``~/.lhassa/action-run-allow`` (one action name per line,
# ``#`` comments). It is never read from the environment, because any process that
# starts this command controls its environment; and it is ignored unless it is a
# regular file owned by the user that nobody else can write (no group or other write
# bit), so the widening is a decision the owner took on disk, not a switch an agent
# can flip while invoking the command.
RUNNABLE = frozenset({"power_mode"})
ALLOW_FILE = ".lhassa/action-run-allow"


def _home() -> str:
    """The user's home from the password database, not ``$HOME`` (which a caller sets)."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError):
        return os.path.expanduser("~")


def allow_file_path() -> str:
    return os.path.join(_home(), ALLOW_FILE)


def extra_allowed(path: "str | None" = None) -> frozenset:
    """The names the owner's file adds, or nothing when the file is absent or unsafe."""
    path = path or allow_file_path()
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            return frozenset()
        with open(path, encoding="utf-8") as fh:
            names = {ln.split("#", 1)[0].strip() for ln in fh}
    except OSError:
        return frozenset()
    return frozenset(n for n in names if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", n or ""))


def runnable() -> frozenset:
    return RUNNABLE | extra_allowed()


def _parent() -> str:
    """The parent process name (``/proc/<ppid>/comm``), cheap and best-effort."""
    try:
        with open(f"/proc/{os.getppid()}/comm", encoding="utf-8") as fh:
            return fh.read().strip()[:40]
    except OSError:
        return ""


def _audit(name: str, params: dict, outcome: dict) -> None:
    """Record every ``action run`` in the review log (never raises, never logs values)."""
    try:
        from lhassa.review.log import record_run

        record_run(
            "action-run", name, [],
            outcome="ok" if outcome.get("ok") else "denied",
            params=sorted(params), parent=_parent(), ppid=os.getppid(),
            error=str(outcome.get("error") or "")[:120],
        )
    except Exception:
        pass


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

        if name not in runnable():
            return {"ok": False, "error": f"denied: {name!r} is not runnable from the CLI (the owner lists more in ~/.lhassa/action-run-allow)"}
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
    _audit(name, params, outcome)
    if json_out:
        typer.echo(json.dumps(outcome))
    else:
        typer.echo(outcome.get("result") or outcome.get("error") or "", err=not outcome["ok"])
    # An action reports failure as text, never by raising.
    failed = not outcome["ok"] or _FAILURE.search(outcome.get("result") or "") is not None
    raise typer.Exit(1 if failed else 0)
