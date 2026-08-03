# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Programmatic Tool Calling (PTC) — Fase 3.

The idea (Hermes' PTC), made safe by Lhassa's deny-by-default machinery: instead
of one LLM turn -> one tool call, one LLM turn writes a small Python SCRIPT that
calls N deterministic Lhassa ACTIONS in a loop/branch/whatever control flow it
needs. The script itself is powerless — it runs inside the existing quarantine
sandbox (no network, no real filesystem, a scrubbed env) and can reach the outer
world through exactly one door: a JSON-lines RPC over a Unix domain socket. Every
RPC request is dispatched by the PARENT process, which re-checks the SAME
security allowlist (:mod:`lhassa.security.allowlist`) a normal ``Task`` dispatch
would (see :func:`lhassa.remote.local_agent.run_tasks` for the sibling seam this
mirrors). The script therefore gains ZERO privileges beyond what a normal action
dispatch already grants — it is a cheaper, more expressive *shape* for the same
set of $0-marginal-risk operations, not a new capability.

Contract:
  - :func:`generate_stub` renders ``lhassa_actions.py``: one plain function per
    registered action, each a thin RPC call. This is the ONLY module the script
    is told about; its docstring says so explicitly so the model does not try to
    reach for anything else.
  - :func:`run_script` is the orchestrator: write the script + the generated stub
    into a fresh workdir, open a Unix-domain-socket RPC server in the PARENT,
    launch the script through :mod:`lhassa.security.quarantine`'s strongest
    available isolation (env scrubbed, ``LHASSA_PTC_SOCKET`` is the one variable
    added on top), and answer every RPC call by re-running the same allowlist
    check :mod:`lhassa.remote.local_agent` uses for a signed ``Task``. Unknown
    actions, actions outside the allowlist, and actions that require human
    approval are all refused (never executed) — a script cannot escalate past
    what an ordinary dispatch could do. **Never raises**: every failure mode
    (bad script, timeout, launch failure) degrades to a returned ``ok: False``
    dict, matching the do-and-sleep "degrade, never crash" rule.

Layering: ``agents/`` is the Adapters layer. This module imports ``core.actions``
(spine) and ``security.{allowlist,signing,trust,quarantine}`` (interfaces) —
both allowed downward imports. It must not import any shell (``cli``, ``api``,
``voice``, ``notify``, ``org``, ``actionpack``, ``dashboard``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from lhassa.core import actions as _actions
from lhassa.core.task import Task
from lhassa.security import allowlist as _allowlist
from lhassa.security import quarantine as _quarantine
from lhassa.security import signing as _signing
from lhassa.security.trust import Origin, TrustLevel

# Caps mirrored from quarantine's own discipline (§2.6): bound what a script can
# make the parent hold in memory, independent of the sandbox's own output cap.
_STDOUT_CAP = 50_000
_STDERR_CAP = 10_000

# The actor id stamped on every Task built for an RPC call — lets the allowlist /
# approval / audit trail tell a PTC-originated action apart from other origins.
_PTC_ACTOR = "ptc"

_SOCKET_ENV_VAR = "LHASSA_PTC_SOCKET"
_STUB_MODULE_NAME = "lhassa_actions.py"
_SCRIPT_MODULE_NAME = "main.py"

_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]")


def _sanitize_ident(name: str) -> str:
    """Turn an arbitrary action name into a valid, unique-enough Python identifier."""
    ident = _IDENT_RE.sub("_", name or "")
    if not ident or ident[0].isdigit():
        ident = f"a_{ident}"
    return ident


def _truncate(text: str, cap: int) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    return text[:cap] + f"… (+{len(text) - cap} chars)"


# ── 1. the generated stub ────────────────────────────────────────────────────


def generate_stub() -> str:
    """Render ``lhassa_actions.py`` — one function per registered ACTION.

    Each function is a thin wrapper that RPCs the ORIGINAL (unsanitized) action
    name to the parent process; the parent re-validates it against the security
    allowlist before running anything. The module docstring tells the model, in
    plain terms, that this is the full extent of what the script can do.
    """
    lines: list[str] = []
    lines.append('"""')
    lines.append("Generated PTC action stubs (lhassa_actions).")
    lines.append("")
    lines.append(
        "Auto-generated — do not hand-edit. Each function below is the ONLY "
        "capability available to this script: calling one deterministic Lhassa"
    )
    lines.append(
        "action, dispatched by the PARENT process over a Unix domain socket. "
        "The parent re-checks the same security allowlist a normal task"
    )
    lines.append(
        "dispatch would; an action that is not allowed is refused, never run. "
        "There is no other door out of this sandbox — no network, no real"
    )
    lines.append('filesystem, nothing beyond calling these functions.')
    lines.append('"""')
    lines.append("")
    lines.append("import json")
    lines.append("import os")
    lines.append("import socket")
    lines.append("")
    lines.append(f"_SOCKET_ENV_VAR = {_SOCKET_ENV_VAR!r}")
    lines.append("")
    lines.append("")
    lines.append("def _rpc(accion, params=None):")
    lines.append('    """Call ``accion`` on the parent via the PTC RPC socket. Raises on refusal."""')
    lines.append("    sock_path = os.environ.get(_SOCKET_ENV_VAR)")
    lines.append("    if not sock_path:")
    lines.append(
        '        raise RuntimeError("LHASSA_PTC_SOCKET not set — not running under the PTC harness")'
    )
    lines.append("    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:")
    lines.append("        s.connect(sock_path)")
    lines.append(
        '        s.sendall((json.dumps({"accion": accion, "params": params or {}}) + "\\n").encode())'
    )
    lines.append("        s.shutdown(socket.SHUT_WR)")
    lines.append("        chunks = []")
    lines.append("        while True:")
    lines.append("            chunk = s.recv(65536)")
    lines.append("            if not chunk:")
    lines.append("                break")
    lines.append("            chunks.append(chunk)")
    lines.append('    raw = b"".join(chunks).decode("utf-8", "replace") or "{}"')
    lines.append("    try:")
    lines.append("        reply = json.loads(raw)")
    lines.append("    except ValueError as exc:")
    lines.append('        raise RuntimeError(f"{accion}: bad reply from parent: {exc}") from exc')
    lines.append("    if not reply.get(\"ok\"):")
    lines.append(
        '        raise RuntimeError(f"{accion}: " + str(reply.get("error") or "refused"))'
    )
    lines.append('    return reply.get("result")')
    lines.append("")

    seen: set[str] = set()
    for name in sorted(_actions.ACTIONS.keys()):
        fn = _actions.ACTIONS.get(name)
        doc = (getattr(fn, "__doc__", None) or "No description available.").strip()
        doc = doc.splitlines()[0].strip() if doc else "No description available."
        ident = _sanitize_ident(name)
        base = ident
        suffix = 2
        while ident in seen:
            ident = f"{base}_{suffix}"
            suffix += 1
        seen.add(ident)

        lines.append("")
        lines.append(f"def {ident}(**params):")
        lines.append(f'    """{doc}"""')
        lines.append(f"    return _rpc({name!r}, params)")

    lines.append("")
    return "\n".join(lines)


# ── 2. the RPC dispatch seam (mirrors remote.local_agent.run_tasks) ──────────


async def _dispatch_action(accion: Any, params: Any, *, instance_id: str) -> dict:
    """Build a signed ``Task`` for ``accion`` and run it through the SAME
    deny-by-default gate ``remote.local_agent.run_tasks`` uses for a normal
    ``Task`` dispatch: allowlist -> (refuse if it needs human approval, PTC
    cannot grant that synchronously) -> ``core.actions.run``. Never raises.
    """
    try:
        if not isinstance(accion, str) or not accion:
            return {"ok": False, "error": "missing or invalid 'accion'"}
        if not isinstance(params, dict):
            params = {}

        if _actions.ACTIONS.get(accion) is None:
            names = sorted(_actions.ACTIONS.keys())
            return {"ok": False, "error": f"unknown action {accion!r}. Registered: {names}"}

        task = Task(
            id=f"ptc_{uuid.uuid4().hex[:12]}",
            instance_id=instance_id,
            accion=accion,
            objetivo=str(params.get("objetivo") or ""),
            params=params,
            origen=Origin(trust=TrustLevel.INTERNO, actor_id=_PTC_ACTOR),
        )
        task.firma = _signing.sign_task(task)

        decision = _allowlist.allow(task)
        if not decision.allowed:
            return {"ok": False, "error": f"denied: {decision.reason}"}
        if decision.requires_approval:
            return {
                "ok": False,
                "error": (
                    f"denied: action {accion!r} requires human approval, "
                    "which PTC cannot grant synchronously"
                ),
            }

        result = await _actions.run(accion, params)
        return {"ok": True, "result": result}
    except Exception as exc:  # the RPC seam must never crash the server loop
        return {"ok": False, "error": f"dispatch error: {exc}"}


# ── 3. the orchestrator ───────────────────────────────────────────────────────


def _kill_group(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_script(
    script: str,
    *,
    instance_id: str,
    config: Any = None,
    timeout: float = 300.0,
    max_calls: int = 50,
) -> dict:
    """Run ``script`` (Python source) inside the quarantine sandbox, letting it
    call deterministic actions over an RPC socket the parent alone answers.

    Every call is gated exactly as a normal ``Task`` dispatch would be (see
    ``_dispatch_action``): unknown actions, disallowed actions, and
    approval-gated actions are all refused. ``max_calls`` bounds how many RPC
    round-trips one script may make; ``timeout`` bounds wall-clock time and
    kills the whole process group (never leaves a zombie/hung child).

    Returns ``{ok, stdout, stderr, calls, elapsed_s, error}``. ``error`` is
    ``None`` on a clean run. **Never raises.**
    """
    start = time.monotonic()
    del config  # accepted for interface symmetry with other agent entrypoints; unused today
    workdir: Optional[Path] = None
    server = None
    calls: list[dict] = []
    try:
        workdir = Path(tempfile.mkdtemp(prefix="lhassa_ptc_"))
        (workdir / _SCRIPT_MODULE_NAME).write_text(script, encoding="utf-8")
        (workdir / _STUB_MODULE_NAME).write_text(generate_stub(), encoding="utf-8")
        sock_path = str(workdir / "ptc.sock")

        async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                line = await reader.readline()
                if not line:
                    return
                try:
                    req = json.loads(line.decode("utf-8", "replace"))
                except ValueError as exc:
                    reply = {"ok": False, "error": f"bad request: {exc}"}
                    accion, params = None, None
                else:
                    accion = req.get("accion")
                    params = req.get("params") or {}
                    if len(calls) >= max_calls:
                        reply = {
                            "ok": False,
                            "error": f"max_calls ({max_calls}) exceeded — refused",
                        }
                        writer.write((json.dumps(reply) + "\n").encode("utf-8"))
                        await writer.drain()
                        return
                    reply = await _dispatch_action(accion, params, instance_id=instance_id)
                calls.append({"accion": accion, "params": params, "ok": bool(reply.get("ok"))})
                writer.write((json.dumps(reply) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception as exc:  # a single bad connection must never kill the server
                logger.debug("ptc: rpc connection error: {}", exc)
                try:
                    writer.write((json.dumps({"ok": False, "error": str(exc)}) + "\n").encode())
                    await writer.drain()
                except Exception:
                    pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        server = await asyncio.start_unix_server(_handle, path=sock_path)

        env = _quarantine._scrub_env(workdir)
        env[_SOCKET_ENV_VAR] = sock_path
        prefix, isolation = _quarantine._select_isolation(workdir)
        cmd = [*prefix, sys.executable, _SCRIPT_MODULE_NAME]

        timed_out = False
        exit_code: Optional[int] = None
        launch_error: Optional[str] = None
        stdout, stderr = "", ""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workdir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                exit_code = proc.returncode
            except asyncio.TimeoutError:
                timed_out = True
                _kill_group(proc)
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=5)
                except asyncio.TimeoutError:
                    stdout_b, stderr_b = b"", b""
                exit_code = proc.returncode
            stdout = _truncate((stdout_b or b"").decode("utf-8", "replace"), _STDOUT_CAP)
            stderr = _truncate((stderr_b or b"").decode("utf-8", "replace"), _STDERR_CAP)
        except (OSError, ValueError) as exc:
            launch_error = f"could not launch script: {exc}"

        elapsed_s = time.monotonic() - start

        if launch_error is not None:
            return {
                "ok": False,
                "stdout": stdout,
                "stderr": stderr,
                "calls": calls,
                "elapsed_s": elapsed_s,
                "error": launch_error,
            }
        if timed_out:
            return {
                "ok": False,
                "stdout": stdout,
                "stderr": stderr,
                "calls": calls,
                "elapsed_s": elapsed_s,
                "error": f"timed out after {timeout}s (isolation={isolation})",
            }

        ok = exit_code == 0
        return {
            "ok": ok,
            "stdout": stdout,
            "stderr": stderr,
            "calls": calls,
            "elapsed_s": elapsed_s,
            "error": None if ok else f"script exited {exit_code} (isolation={isolation})",
        }
    except Exception as exc:  # belt-and-suspenders: this function must never raise
        return {
            "ok": False,
            "stdout": "",
            "stderr": "",
            "calls": calls,
            "elapsed_s": time.monotonic() - start,
            "error": f"run_script error: {exc}",
        }
    finally:
        if server is not None:
            try:
                server.close()
                await server.wait_closed()
            except Exception:
                pass
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
