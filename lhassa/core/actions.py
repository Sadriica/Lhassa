# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Fixed, deterministic actions; the "do" half of "hace y duerme" (§2.4).

A trigger that maps to one of these runs in code with ZERO tokens: no model,
no judgment. The router/think branch (§2.4, Phase 2) is the *other* path; this
module is only the closed set of typed actions the brain can execute directly.

Each action is a callable ``(params: dict) -> str`` that returns a short outcome
string and NEVER raises; failures are captured and reported as text so the
episode always records *what happened*. The registry ``ACTIONS`` is the closed
set; ``watchlist.Trigger.maps_to_fixed_action`` checks membership against it.

First typed action: ``sync_repo`` (``git pull --ff-only``), per §2.5; a check-in
reconciliation step, not a push.
"""

import inspect
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable, Union

from lhassa.registry import Registry


# ── subprocess seam (mockable) ───────────────────────────────────────────────────

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """
    Thin, mockable wrapper around the subprocess call so tests can monkeypatch
    ``lhassa.core.actions._run`` and stay fully offline (no real git/network).
    Never invoked with ``check=True``; callers inspect ``returncode`` themselves.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _short(text: str, limit: int = 300) -> str:
    """Collapse subprocess output to a single trimmed, length-capped line-ish blob."""
    text = (text or "").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


# ── actions (deterministic, never raise) ─────────────────────────────────────────

def sync_repo(params: dict) -> str:
    """
    Reconcile a local git repo to its remote with ``git -C <dir> pull --ff-only``
    (§2.5: ``git pull`` is reconciliation). Fast-forward only; never creates a
    merge commit, so a diverged repo fails loudly instead of guessing.

    ``params``:
        repo_dir / objetivo / dir / path; the repository directory (required).

    Returns a short outcome string. Guards a missing/blank dir and a dir that is
    not a git repo; captures stdout/stderr; never raises.
    """
    repo_dir = (
        params.get("repo_dir")
        or params.get("objetivo")
        or params.get("dir")
        or params.get("path")
        or ""
    ).strip() if isinstance(params, dict) else ""

    if not repo_dir:
        return "sync_repo: no repo_dir given"

    path = Path(repo_dir).expanduser()
    if not path.is_dir():
        return f"sync_repo: directory not found: {repo_dir}"
    if not (path / ".git").exists():
        return f"sync_repo: not a git repo: {repo_dir}"

    cmd = ["git", "-C", str(path), "pull", "--ff-only"]
    try:
        proc = _run(cmd)
    except FileNotFoundError:
        return "sync_repo: git not installed"
    except subprocess.TimeoutExpired:
        return f"sync_repo: timed out on {repo_dir}"
    except Exception as exc:  # defensive: an action must never raise
        return f"sync_repo: error on {repo_dir}: {exc}"

    if proc.returncode == 0:
        out = _short(proc.stdout) or "ok"
        return f"sync_repo ok ({repo_dir}): {out}"
    err = _short(proc.stderr) or _short(proc.stdout) or f"exit {proc.returncode}"
    return f"sync_repo failed ({repo_dir}): {err}"


async def tracking_postulaciones(params: dict) -> str:
    """
    Track job/internship applications (CVs sent) in the brain DB; the second
    typed task (§2.5) and the first concrete MCP-backed one. Deterministic and
    offline: all add/update/list work hits the local ``postulaciones`` table.

    ``params``:
        op / accion; "add" | "update" | "list" (default "list").
        instance_id; isolation scope (default ``Config.instance_id``).
        add:    empresa (required), rol, estado, fuente, link, notas.
        update: id (required), estado (required); rol, notas, link, categoria,
                prioridad (all optional, only supplied keys are written).
        list:   estado (optional filter).
        mcp_pull / mcp_tool. OPTIONAL, guarded external top-up (see below).

    Optional MCP pull (guarded): if ``mcp_pull`` (a sync/async callable returning
    a list of ``{empresa, rol, estado, fuente, link, notas}`` dicts) is supplied,
    or ``mcp_tool`` names a registered MCP tool, the action MAY pull updates from
    an external source (email/sheet) and insert them before answering. The pull is
    wrapped so a missing SDK / unreachable server NEVER breaks the deterministic
    core; it just adds nothing. Tests inject ``mcp_pull`` to stay fully offline.

    Returns a short summary string and NEVER raises.
    """
    from lhassa.config import Config
    from lhassa.core import db

    try:
        if not isinstance(params, dict):
            params = {}
        instance_id = (params.get("instance_id") or Config.instance_id)
        op = str(params.get("op") or params.get("accion") or "list").strip().lower()

        # ── optional, guarded MCP top-up ──────────────────────────────────────
        pulled = await _pull_postulaciones(instance_id, params)
        pulled_note = f" (+{pulled} de MCP)" if pulled else ""

        if op == "add":
            empresa = str(params.get("empresa") or "").strip()
            if not empresa:
                return "tracking_postulaciones: 'empresa' is required to add"
            pid = await db.add_postulacion(
                instance_id,
                empresa,
                str(params.get("rol") or ""),
                estado=str(params.get("estado") or "pendiente"),
                fuente=str(params.get("fuente") or ""),
                link=str(params.get("link") or ""),
                notas=str(params.get("notas") or ""),
                categoria=str(params.get("categoria") or ""),
                prioridad=str(params.get("prioridad") or ""),
            )
            rol = params.get("rol")
            rol_str = f" ({rol})" if rol else ""
            return f"tracking_postulaciones: añadida {empresa}{rol_str} [{pid}]{pulled_note}"

        if op == "update":
            pid = str(params.get("id") or params.get("postulacion_id") or "").strip()
            estado = str(params.get("estado") or "").strip()
            if not pid or not estado:
                return "tracking_postulaciones: 'id' and 'estado' are required to update"
            notas = params.get("notas")
            categoria = params.get("categoria")
            prioridad = params.get("prioridad")
            link = params.get("link")
            rol = params.get("rol")
            ok = await db.update_postulacion_estado(
                instance_id, pid, estado,
                notas=str(notas) if notas is not None else None,
                categoria=str(categoria) if categoria is not None else None,
                prioridad=str(prioridad) if prioridad is not None else None,
                link=str(link) if link is not None else None,
                rol=str(rol) if rol is not None else None,
            )
            if not ok:
                return f"tracking_postulaciones: no encontrada {pid}{pulled_note}"
            return f"tracking_postulaciones: {pid} → {estado}{pulled_note}"

        # default: list
        estado_filter = params.get("estado")
        rows = await db.list_postulaciones(
            instance_id,
            estado=str(estado_filter) if estado_filter else None,
        )
        if not rows:
            return f"tracking_postulaciones: 0 postulaciones{pulled_note}"
        head = ", ".join(
            f"{r['empresa']}{(' ' + r['rol']) if r['rol'] else ''}={r['estado']}"
            + (f" [{r['categoria']}]" if r.get("categoria") else "")
            + (f" p={r['prioridad']}" if r.get("prioridad") else "")
            for r in rows[:5]
        )
        more = f" …(+{len(rows) - 5})" if len(rows) > 5 else ""
        return f"tracking_postulaciones: {len(rows)} postulaciones{pulled_note}: {head}{more}"
    except Exception as exc:  # an action must NEVER raise
        return f"tracking_postulaciones: error: {exc}"


async def _pull_postulaciones(instance_id: str, params: dict) -> int:
    """Guarded external top-up. Returns the count of rows inserted (0 if none).

    Never raises: a missing/failing source contributes nothing. ``mcp_pull`` (an
    injectable callable) takes precedence so tests stay offline; otherwise a named
    ``mcp_tool`` is invoked via the MCP client if one is configured.
    """
    from lhassa.core import db

    source = params.get("mcp_pull")
    try:
        if source is None:
            tool_name = params.get("mcp_tool")
            if not tool_name:
                return 0
            from lhassa.tools import mcp_client
            items = await mcp_client.pull_postulaciones(
                tool_name, params.get("mcp_args") or {}
            )
        else:
            items = source() if callable(source) else source
            if inspect.isawaitable(items):
                items = await items
        count = 0
        for it in items or []:
            if not isinstance(it, dict):
                continue
            empresa = str(it.get("empresa") or "").strip()
            if not empresa:
                continue
            await db.add_postulacion(
                instance_id,
                empresa,
                str(it.get("rol") or ""),
                estado=str(it.get("estado") or "pendiente"),
                fuente=str(it.get("fuente") or "mcp"),
                link=str(it.get("link") or ""),
                notas=str(it.get("notas") or ""),
            )
            count += 1
        return count
    except Exception:  # guarded: the deterministic core must survive any pull error
        return 0


# ── registry + dispatch ──────────────────────────────────────────────────────────

# The set of fixed deterministic actions (no LLM) lives in a ``Registry``: the
# shared swap-point primitive (``lhassa/registry.py``). This makes actions a
# **drop-in** capability: a plugin module can ``@register_action("x")`` a new
# typed action WITHOUT editing this file (the elastic/modular principle), and a
# duplicate name is rejected instead of silently clobbering an existing action.
ActionFn = Callable[[dict], Union[str, Awaitable[str]]]
_REGISTRY: Registry[ActionFn] = Registry(kind="action")


def register_action(name: str, fn: "ActionFn | None" = None, *, override: bool = False):
    """Register a deterministic action under ``name``.

    Usable as a direct call (``register_action("x", fn)``) or as a decorator
    (``@register_action("x")``). A duplicate name raises unless ``override=True``.
    This is the drop-in path for plugins to add a typed action.
    """
    return _REGISTRY.register(name, fn, override=override)


# Seed the closed Phase-0/4 set. Membership in ``ACTIONS`` is what makes a Trigger
# "map to a fixed action". An action may be sync (``-> str``) or async
# (``-> Awaitable[str]``); ``run`` awaits the latter so DB-backed actions work
# without touching ``step()``.
register_action("sync_repo", sync_repo)
register_action("tracking_postulaciones", tracking_postulaciones)

# Backward-compatible module-level mapping. It is a LIVE, read-only view of the
# registry's backing store, so existing consumers keep working byte-for-byte:
# ``watchlist`` does ``action in actions.ACTIONS`` and this module's ``run`` reads
# it; both supported (``in``/``[]``/``.get()``/``len()``/iteration); while later
# ``register_action`` calls (incl. from plugins) are reflected automatically. It is
# read-only so nothing can bypass the registry's duplicate-protection by assigning.
ACTIONS: "Any" = _REGISTRY.as_mapping()


# ── preconditions (an action that CANNOT act should not run) ─────────────────────

#: ``name -> callable(params) -> str``. The callable returns "" when the action
#: can do its job, or a SHORT reason why it structurally cannot right now.
_PRECONDITIONS: "dict[str, Callable[[dict], str]]" = {}


def register_precondition(name: str, fn: "Callable[[dict], str]") -> None:
    """Declare when ``name`` is capable of doing anything at all.

    A precondition answers "could this possibly produce a result?", NOT "should
    this run?": it is for the structurally impossible case: a feature flag off,
    nothing configured to read. Four scheduled actions were running on a daily
    cadence with no calendar, no knowledge roots and the graph disabled, and each
    no-op still cost a full down-execute-up delivery trail; between them they were
    13% of the episode log, saying nothing. Silence in the log should mean nothing
    happened, so the fix is to not run them, not to log the emptiness better.

    Registered separately from the action so nothing about the action changes,
    and so the day the owner configures a calendar it simply starts working again
    with no code edit. Applies to any registered action; unregistered names are
    always runnable.
    """
    _PRECONDITIONS[name] = fn


def precondition_unmet(action: str, params: "dict | None" = None) -> str:
    """Why ``action`` cannot act right now, or ``""`` when it can.

    FAIL-OPEN by design: no precondition, or one that raises, means runnable. A
    buggy capability check must never be able to silence a working action;
    running an action that then reports a no-op is a far cheaper mistake than
    silently disabling maintenance.
    """
    fn = _PRECONDITIONS.get(action)
    if fn is None:
        return ""
    try:
        return str(fn(params or {}) or "")
    except Exception:
        return ""


def blocked_actions(params: "dict | None" = None) -> "dict[str, str]":
    """Every registered action that currently cannot act, mapped to its reason.

    The discoverability half of the skip: because a skipped action leaves NO
    episode, "why isn't calendar_sync running?" has to be answerable somewhere,
    and that somewhere is ``lhassa capabilities``.
    """
    out: "dict[str, str]" = {}
    for name in sorted(_REGISTRY.as_mapping()):
        reason = precondition_unmet(name, params)
        if reason:
            out[name] = reason
    return out


async def run(action: str, params: dict, episode: Any = None) -> str:
    """
    Dispatch ``action`` to the deterministic registry. Unknown action → safe
    error string (never raises). Attaches the action name to ``episode`` if one
    is supplied so observability reflects what ran. NO LLM is ever involved here.

    Actions may be sync or async; a returned awaitable is awaited here so the
    caller (``step()``) never changes shape.
    """
    if episode is not None:
        episode.accion = action

    fn = _REGISTRY.get_or_none(action)
    if fn is None:
        return f"unknown action: {action!r}"

    try:
        result = fn(params or {})
        if inspect.isawaitable(result):
            result = await result
        return result
    except Exception as exc:  # belt-and-suspenders: dispatch must never raise
        return f"action {action!r} errored: {exc}"
