# SPDX-License-Identifier: AGPL-3.0-or-later
"""Brain/cross-cutting actions: notify, add_task, set_task_state, list_approvals,
check_health, git_status_repos, compile_cv.
"""

from __future__ import annotations

from lhassa.config import Config
from lhassa.core import db as _db
from lhassa.core import dispatch as _dispatch
from lhassa.cv.compile import compile_pdf
from lhassa.cv.paths import base_path
from lhassa.health.checks import check_git_status, format_digest, run_all
from lhassa.notify.dispatch import notify as _notify
from lhassa.security import approval as _approval
from lhassa.security.trust import Origin, TrustLevel, degrade

# ── Closed set of final task states that an action may set ───────────────────
# "pendiente" is intentionally excluded: allowing an action to move a task back
# to pendiente would let a judgment loop (dispatch→mint→think→dispatch→…)
# resurrect completed work endlessly and burn tokens without bound.
_ALLOWED_ESTADOS = frozenset({"hecha", "rechazada"})

# Cap on check_health digest output returned as a string.
_HEALTH_OUTPUT_CAP = 1500


async def notify(params: dict) -> str:
    """Fan out a notification to every configured channel.

    Wraps ``lhassa.notify.dispatch.notify``. Never raises; a delivery failure
    is reported as a string, not an exception.

    params:
        text  (required) ; the notification message.
        level            ; severity hint: "info" | "warning" | "error"
                            (default "info").

    Returns "notify: delivered" or "notify: no channel delivered" or an error
    string.
    """
    try:
        text = str(params.get("text") or "").strip()
        if not text:
            return "notify: 'text' param is required"

        level = str(params.get("level") or "info").strip()
        delivered = await _notify(text, level=level)
        return "notify: delivered" if delivered else "notify: no channel delivered"

    except Exception as exc:
        return f"notify: error: {exc}"


async def add_task(params: dict) -> str:
    """Mint a new fixed-action task through the security machinery.

    Uses ``core.dispatch.mint_task`` (extracted refactor) so the full approval
    frontier + signing + DB write path is applied; exactly the same as the
    judgment dispatcher. accion must be in ACTIONS; unknown names are refused.

    Origin trust: TrustLevel.INTERNO, actor_id="action:add_task", degraded by
    the calling task's trust if params carry "origen_trust".

    params:
        accion       (required) ; a registered action name.
        objetivo                ; task objective string.
        params                  ; dict of params for the minted task.
        instance_id             ; defaults to Config.instance_id.
        origen_trust            ; int trust level (for contagion degradation).

    Never raises.
    """
    try:
        accion = str(params.get("accion") or "").strip()
        if not accion:
            return "add_task: 'accion' param is required"

        objetivo = str(params.get("objetivo") or "").strip()
        sub_params = params.get("params") or {}
        if not isinstance(sub_params, dict):
            sub_params = {}

        instance_id = params.get("instance_id") or Config.instance_id

        # Degrade trust from the calling context if supplied.
        caller_trust_raw = params.get("origen_trust")
        try:
            caller_trust = (
                TrustLevel(int(caller_trust_raw))
                if caller_trust_raw is not None
                else TrustLevel.INTERNO
            )
        except (ValueError, TypeError):
            caller_trust = TrustLevel.INTERNO

        origin = Origin(
            trust=degrade(TrustLevel.INTERNO, caller_trust),
            actor_id="action:add_task",
        )

        task_id, estado = await _dispatch.mint_task(
            instance_id,
            accion,
            objetivo=objetivo,
            params=sub_params,
            origin=origin,
        )

        if not task_id:
            return f"add_task: {estado}"  # error message from mint_task

        return f"add_task: minted {task_id} ({accion}) → {estado}"

    except Exception as exc:
        return f"add_task: error: {exc}"


async def set_task_state(params: dict) -> str:
    """Update a task's state to one of the allowed terminal values.

    Allowed states: "hecha", "rechazada". "pendiente" is explicitly forbidden
   ; an action must not be able to resurrect completed work.

    params:
        id          (required) ; the task id.
        estado      (required) ; "hecha" or "rechazada".
        instance_id            ; defaults to Config.instance_id.

    Never raises.
    """
    try:
        task_id = str(params.get("id") or params.get("task_id") or "").strip()
        if not task_id:
            return "set_task_state: 'id' param is required"

        estado = str(params.get("estado") or "").strip()
        if not estado:
            return "set_task_state: 'estado' param is required"

        if estado not in _ALLOWED_ESTADOS:
            allowed = ", ".join(sorted(_ALLOWED_ESTADOS))
            return (
                f"set_task_state: estado {estado!r} is not allowed "
                f"(allowed: {allowed})"
            )

        instance_id = params.get("instance_id") or Config.instance_id
        await _db.set_task_state(instance_id, task_id, estado)
        return f"set_task_state: {task_id} → {estado}"

    except Exception as exc:
        return f"set_task_state: error: {exc}"


async def list_approvals(params: dict) -> str:
    """List pending approvals for this brain instance.

    Calls ``security.approval.pending(instance_id)`` and returns a short
    human-readable summary.

    params:
        instance_id ; defaults to Config.instance_id.

    Never raises.
    """
    try:
        instance_id = params.get("instance_id") or Config.instance_id
        rows = await _approval.pending(instance_id)

        if not rows:
            return "list_approvals: no pending approvals"

        lines: list[str] = []
        for row in rows[:10]:  # cap at 10 items in the summary
            ap_id = row.get("id", "?")
            tipo = row.get("tipo", "?")
            payload = row.get("payload") or {}
            if isinstance(payload, dict):
                accion = payload.get("accion", "")
                label = f"{tipo}:{accion}" if accion else tipo
            else:
                label = tipo
            lines.append(f"{ap_id} [{label}]")

        more = f" …+{len(rows) - 10}" if len(rows) > 10 else ""
        return f"list_approvals: {len(rows)} pending; " + ", ".join(lines) + more

    except Exception as exc:
        return f"list_approvals: error: {exc}"


def check_health(params: dict) -> str:
    """Run all health checks and return a formatted digest (capped at 1500 chars).

    Calls ``health.checks.run_all(notify=False)`` (no desktop notification) and
    formats the result via ``health.checks.format_digest``.

    params:
        out_dir ; optional snapshot directory override (default ~/.lhassa/health).

    Never raises.
    """
    try:
        out_dir = params.get("out_dir") or None
        digest = run_all(out_dir=out_dir, notify=False)
        text = format_digest(digest)
        if len(text) > _HEALTH_OUTPUT_CAP:
            text = text[:_HEALTH_OUTPUT_CAP] + "…"
        return text

    except Exception as exc:
        return f"check_health: error: {exc}"


def git_status_repos(params: dict) -> str:
    """Report uncommitted/unpushed work across project repos.

    Wraps ``health.checks.check_git_status()`` and returns its summary string.

    params: (none required)

    Never raises.
    """
    try:
        result = check_git_status()
        return f"git_status_repos: {result['summary']}"

    except Exception as exc:
        return f"git_status_repos: error: {exc}"


def compile_cv(params: dict) -> str:
    """Compile the CV .tex to PDF via the configured LaTeX engine.

    Uses ``cv.compile.compile_pdf``. The tex path comes from params or falls
    back to the CV seam default (``cv.paths.base_path()``). Returns the PDF
    path on success, or an explanatory message if the engine is missing / the
    compile fails. Never raises.

    params:
        tex_path ; path to a .tex file (default: ~/.lhassa/cv/cv_largo.tex).
    """
    try:
        tex_path_raw = params.get("tex_path") or None
        tex_path = str(tex_path_raw) if tex_path_raw else str(base_path())

        pdf = compile_pdf(tex_path)
        if pdf is None:
            return f"compile_cv: compile failed or engine not found for {tex_path}"
        return f"compile_cv: {pdf}"

    except Exception as exc:
        return f"compile_cv: error: {exc}"
