# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small, offline-testable executor for work-flows.

:func:`run_flow` walks the graph from its single trigger along the edges, executing
one node at a time and threading each node's output to the next ("receive
information" along the wire). It NEVER raises — a node that errors becomes a failed
step in the run record, and the path stops there.

Execution order
---------------
A breadth-first walk from the trigger. The frontier carries ``(node_id, data)``
pairs; ``data`` is the output of the upstream node. Each node executes at most once
(the graph is a validated DAG), guarded by a visited set and a hard step cap.

Per-kind behaviour
------------------
  * ``trigger`` — records the inputs and passes them downstream unchanged.
  * ``task``    — resolves a brain by **HYBRID** routing (a pinned ``config.brain``
    wins; otherwise the node's ``skill``/domain auto-routes through the org
    topology), then runs the work through the INJECTABLE ``executor`` (default
    :func:`lhassa.org.executor.brain_executor`, offline unless ``LHASSA_ORG_LIVE``).
    The upstream ``data`` is passed in as the task's input; the executor's result
    becomes the data that flows downstream. A ``task`` node may declare
    ``config.retry`` (``max_attempts``/``backoff``/``base_seconds``/``max_seconds``/
    ``retry_on``, all optional — the default is a single attempt) and
    ``config.timeout_seconds``: a timeout counts as ``retry_on: "timeout"``. In a
    DURABLE run a retryable failure PARKS the run (``WAITING_RETRY``, releasing the
    lease exactly like a completed run) until ``next_wakeup_at``; in an in-memory
    run it retries inline with no real sleep (the deterministic $0 plane never
    blocks on wall-clock time).
  * ``gate``    — ``verify`` runs :func:`lhassa.agents.verify.verify`; ``approval``
    parks a request via :mod:`lhassa.security.approval`. The step records the
    verdict (``passed`` / ``needs_approval``). On a non-pass the path STOPS unless an
    outgoing edge's ``when`` explicitly handles the failure.
  * ``output``  — records the output (notify/doc/log); best-effort, never raises. The
    ``tex`` type additionally EXPORTS a tailored ``.tex`` file (``lhassa.cv.export``)
    to ``~/.lhassa/cv/out/{id}_{company}.tex`` and records its path.
  * ``branch``  — evaluates the outgoing edges' ``when`` and follows the FIRST match
    (an unconditional edge is the ``else``).

Branch / edge semantics
-----------------------
After a node runs, its outgoing edges are filtered by :func:`eval_when` against a
context (the node's result + the data). A ``branch`` follows a single edge; other
nodes follow every matching edge (usually one — a linear flow). A ``gate`` that did
not pass drops its unconditional edges, so a non-pass naturally halts the path.

Return value
------------
A structured run record::

    {"flow_id", "status": "completed"|"failed", "steps": [ {node, kind, ok, ...} ],
     "output": <last recorded output / final data>}
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.flows.model import Flow, FlowNode, NodeKind
from lhassa.flows.registries import (
    resolve_context,
    resolve_output_handler,
    resolve_router,
)

# An executor seam: ``async (goal, *, department, config) -> dict``. Defaults to the
# org's department brain (offline + $0 unless LHASSA_ORG_LIVE). Injected in tests.
Executor = Callable[..., Awaitable[dict]]
CheckpointHook = Callable[[dict], Awaitable[None]]

_TRUTHY = {"1", "true", "yes", "on"}
_MAX_STEPS_FACTOR = 4  # hard cap = nodes * factor + 8, defends against runaway walks


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


def _live_enabled() -> bool:
    """Whether live execution is on (``LHASSA_ORG_LIVE``; default OFF)."""
    return (os.getenv("LHASSA_ORG_LIVE", "") or "").strip().lower() in _TRUTHY


# ── branch / edge condition evaluation ───────────────────────────────────────
def eval_when(when: Optional[dict], ctx: dict) -> bool:
    """Evaluate an edge ``when`` condition against ``ctx``.

    ``when=None`` (unconditional) → ``True``. Otherwise ``when`` is a tiny spec::

        {"field": "<key>", "op": "eq|ne|truthy|falsy|contains|gt|lt", "value": <v>}

    ``field`` is looked up in ``ctx`` (e.g. ``"passed"``, ``"summary"``, ``"status"``,
    or anything in the upstream result). An unknown/oversized spec degrades to
    ``False`` (the edge is simply not taken) — evaluation never raises.
    """
    if when is None:
        return True
    if not isinstance(when, dict):
        return False
    try:
        op = str(when.get("op", "truthy")).lower()
        field = when.get("field")
        actual = ctx.get(field) if field is not None else ctx
        expected = when.get("value")
        if op == "truthy":
            return bool(actual)
        if op == "falsy":
            return not bool(actual)
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "contains":
            return expected in actual  # type: ignore[operator]
        if op == "gt":
            return actual > expected  # type: ignore[operator]
        if op == "lt":
            return actual < expected  # type: ignore[operator]
        logger.debug("flows: unknown when op {!r}", op)
        return False
    except Exception as exc:  # a malformed condition is "not taken", never a crash
        logger.debug("flows: when eval failed ({}): {}", when, exc)
        return False


# ── hybrid task routing ──────────────────────────────────────────────────────
def resolve_brain(node: FlowNode, data: Any, config: Config) -> tuple[str, bool]:
    """Resolve which brain a ``task`` node runs on. Returns ``(department, pinned)``.

    A thin, backward-compatible wrapper over :mod:`lhassa.flows.registries` —
    ``TASK_ROUTERS`` (default ``org_topology``, selectable per node via
    ``config.router``). Kept as a public function/import path for existing
    callers and tests.
    """
    return resolve_router(node)(node, data, config)


_DEFAULT_RETRY_ON = frozenset({"error"})


def _retry_config(node: FlowNode) -> dict:
    """Parse a ``task`` node's ``config.retry`` spec; degrades to "no retry".

    ``{"max_attempts": int>=1, "backoff": "fixed"|"exponential", "base_seconds":
    float, "max_seconds": float, "retry_on": ["error", "timeout", ...]}``. Every
    key is optional and a missing/malformed value falls back to a single
    attempt on ``"error"`` only — a node with no ``retry`` config behaves
    exactly as before this feature existed.
    """
    cfg = node.config or {}
    spec = cfg.get("retry") if isinstance(cfg.get("retry"), dict) else {}
    try:
        max_attempts = max(1, int(spec.get("max_attempts") or 1))
    except (TypeError, ValueError):
        max_attempts = 1
    backoff = str(spec.get("backoff") or "fixed").strip().lower()
    base_raw = spec.get("base_seconds")
    try:
        base_seconds = max(0.0, float(base_raw)) if base_raw is not None else 1.0
    except (TypeError, ValueError):
        base_seconds = 1.0
    max_raw = spec.get("max_seconds")
    try:
        max_seconds = max(base_seconds, float(max_raw)) if max_raw is not None else base_seconds
    except (TypeError, ValueError):
        max_seconds = base_seconds
    retry_on_raw = spec.get("retry_on")
    if isinstance(retry_on_raw, (list, tuple, set)):
        retry_on = {str(x).strip().lower() for x in retry_on_raw if str(x).strip()}
    else:
        retry_on = set()
    return {
        "max_attempts": max_attempts,
        "backoff": backoff,
        "base_seconds": base_seconds,
        "max_seconds": max_seconds,
        "retry_on": retry_on or set(_DEFAULT_RETRY_ON),
    }


def _backoff_seconds(retry_cfg: dict, attempt: int) -> float:
    """The wait before the NEXT attempt (``attempt`` is the one that just failed).

    ``"exponential"`` doubles per attempt from ``base_seconds``, capped at
    ``max_seconds``; anything else (including the default ``"fixed"``) waits a
    constant ``base_seconds``.
    """
    base = retry_cfg["base_seconds"]
    cap = retry_cfg["max_seconds"]
    if retry_cfg["backoff"] == "exponential":
        seconds = base * (2 ** max(0, attempt - 1))
    else:
        seconds = base
    return max(0.0, min(seconds, cap))


async def _default_executor(
    goal: str, *, department: str, config: Config, max_tokens: Optional[int] = None, **_: Any
) -> dict:
    """The default task executor — the org department brain (offline + $0 by default)."""
    if not _live_enabled():
        return {
            "ok": True,
            "summary": (
                f"[offline] {department} department handled (live thinking disabled "
                f"— set LHASSA_ORG_LIVE=1 to think): {goal}"
            ),
            "department": department,
            "cost_usd": 0.0,
            "model": "offline",
        }
    from lhassa.org.executor import brain_executor

    return await brain_executor(
        goal, department=department, config=config, max_tokens=max_tokens
    )


# ── per-node execution ───────────────────────────────────────────────────────
async def _run_task(
    node: FlowNode,
    data: Any,
    *,
    config: Config,
    executor: Executor,
    base_cv: str = "",
    company: str = "",
) -> tuple[dict, Any]:
    """Execute a task node; returns ``(step_extra, data_out)``.

    ``base_cv`` and ``company`` are injected into the prompt alongside ``{input}``
    (see :func:`_format_goal`) so a task can build on the fixed base CV and address
    the resolved company by name.
    """
    cfg = node.config or {}
    department, pinned = resolve_brain(node, data, config)
    prompt = cfg.get("prompt") or cfg.get("goal") or ""
    goal = _format_goal(
        prompt,
        data,
        base_cv=base_cv,
        company=company,
        input_mode=str(cfg.get("input_mode") or "summary"),
    ) or department
    max_tokens = cfg.get("max_tokens")
    try:
        max_tokens = int(max_tokens) if max_tokens is not None else None
    except (TypeError, ValueError):
        max_tokens = None
    result = await executor(
        goal, department=department, config=config, max_tokens=max_tokens
    )
    extra = {
        "brain": department,
        "pinned": pinned,
        "goal": goal,
        "live": _live_enabled(),
        "result": result,
    }
    data_out = _append_task_context(data, result, department) if cfg.get(
        "preserve_context"
    ) else result
    return extra, data_out


def _format_input(data: Any, mode: str = "summary") -> str:
    """Render upstream data for ``{input}``.

    Historical flows use ``summary`` mode.  ``full`` is an explicit research/
    audit mode that serialises the complete JSON-safe dossier so a later worker
    cannot silently lose the original sources or an earlier worker's output.
    """
    if mode.strip().lower() == "full":
        try:
            return json.dumps(data, ensure_ascii=False, default=str, indent=2)
        except Exception:
            return str(data)
    summary = data
    if isinstance(data, dict):
        summary = data.get("summary", data.get("output", data))
    return str(summary)


def _append_task_context(data: Any, result: Any, department: str) -> dict:
    """Append one task result to a bounded cumulative dossier.

    This is opt-in through ``config.preserve_context``.  It deliberately keeps a
    single initial payload plus a flat stage list, avoiding the exponentially
    nested ``input -> result -> input`` shape that a naive accumulator creates.
    """
    if isinstance(data, dict) and isinstance(data.get("research_context"), dict):
        context = _json_safe(data["research_context"])
    else:
        context = {"initial": _json_safe(data), "stages": []}
    stages = list(context.get("stages") or [])
    result_dict = result if isinstance(result, dict) else {"summary": str(result)}
    stages.append(
        {
            "brain": department,
            "summary": str(
                result_dict.get("summary", result_dict.get("output", result_dict))
            ),
            "model": result_dict.get("model"),
            "cost_usd": float(result_dict.get("cost_usd") or 0.0),
        }
    )
    context["stages"] = stages
    return {
        "summary": str(
            result_dict.get("summary", result_dict.get("output", result_dict))
        ),
        "research_context": context,
    }


def _format_goal(
    prompt: str,
    data: Any,
    *,
    base_cv: str = "",
    company: str = "",
    input_mode: str = "summary",
) -> str:
    """Build a task's goal by interpolating the available placeholders into ``prompt``.

    Placeholders (each substituted only if present, so behaviour for a prompt that
    uses none is unchanged):

      * ``{input}``   — the upstream node's output (a dict's ``summary``/``output``).
      * ``{base_cv}`` — the fixed base CV text (``cv.load_base()`` at run start).
      * ``{company}`` — the resolved company (override → context → ``"untitled"``).
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return ""
    replacements = {
        "{input}": _format_input(data, input_mode),
        "{base_cv}": base_cv or "",
        "{company}": company or "",
    }
    try:
        out = prompt
        for token, value in replacements.items():
            if token in out:
                out = out.replace(token, value)
        return out
    except Exception:
        return prompt


async def _run_gate(
    node: FlowNode,
    data: Any,
    *,
    config: Config,
    base_cv: str = "",
    run_id: Optional[str] = None,
    flow_id: Optional[str] = None,
    approval_context: Optional[dict] = None,
    approval_decisions: Optional[dict[str, bool]] = None,
) -> tuple[dict, Any, bool]:
    """Execute a gate node; returns ``(step_extra, data_out, passed)``.

    ``passed`` drives edge-following: a non-pass drops unconditional outgoing edges.
    ``base_cv`` is passed to the verifier via ``context`` so an objective check (e.g.
    contact-detail preservation) can compare the artifact against the fixed base CV.
    """
    cfg = node.config or {}
    gate_type = str(cfg.get("type") or "verify").lower()

    if gate_type == "approval":
        if approval_decisions and approval_decisions.get(node.id) is True:
            return (
                {
                    "gate": "approval",
                    "passed": True,
                    "needs_approval": False,
                    "approved": True,
                },
                data,
                True,
            )

        instance_id = getattr(config, "instance_id", None) or Config.instance_id
        payload = {
            "accion": "flow_gate",
            "node": node.id,
            "data": _safe(data),
        }
        approval_id = None
        if run_id is not None or flow_id is not None:
            if not run_id or not flow_id or not node.id:
                raise RuntimeError("durable approval gate is missing run, flow, or node identity")
            payload.update(approval_context or {})
            # Cursor identity is authoritative; callers may only add context.
            payload.update(
                {"run_id": run_id, "flow_id": flow_id, "node_id": node.id}
            )
            digest = hashlib.sha256(
                f"{instance_id}:{run_id}:{flow_id}:{node.id}".encode()
            ).hexdigest()[:24]
            approval_id = f"ap_flow_{digest}"
        else:
            try:
                from lhassa.security import approval

                approval_id = await approval.request_approval(
                    instance_id, tipo="flow_gate", payload=payload
                )
            except Exception as exc:  # legacy parking remains best-effort
                logger.debug("flows: approval gate could not park ({}): {}", node.id, exc)
        # A parked approval is NOT a pass — the path waits (records needs_approval).
        return (
            {
                "gate": "approval",
                "passed": False,
                "needs_approval": True,
                "approval_id": approval_id,
                **({"approval_payload": payload} if run_id is not None else {}),
            },
            data,
            False,
        )

    # verify gate
    from lhassa.agents.verify import verify

    spec = cfg.get("spec") or {}
    artifact = data
    if isinstance(data, dict):
        artifact = data.get("summary", data.get("output", data))
    verdict = verify(artifact, spec=spec, context={"base_cv": base_cv})
    return (
        {"gate": "verify", "passed": bool(verdict.passed), "detail": verdict.detail},
        data,
        bool(verdict.passed),
    )


def resolve_company(override: Optional[str], context: Optional[dict]) -> str:
    """Resolve the company/convocatoria name for a run.

    Precedence: a run-level ``override`` WINS; else the value the pipeline put in
    ``context`` (e.g. what the jd-analyst extracted, keyed ``"company"``); else
    ``"untitled"``. Whitespace-only values are treated as absent.
    """
    for candidate in (override, (context or {}).get("company")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return "untitled"


def _run_output(
    node: FlowNode, data: Any, *, company: str = "untitled", run_id: Optional[str] = None
) -> dict:
    """Record an output node — best-effort, never raises.

    ``notify`` / ``doc`` / ``log`` (and any other unregistered type) record the
    payload as a plain log entry. A type registered in
    :mod:`lhassa.flows.registries` (``OUTPUT_HANDLERS`` — ``tex`` by default,
    which EXPORTS a tailored ``.tex`` file for the CV work-flow via
    :func:`lhassa.cv.export`) additionally runs that handler and merges its
    record fields (e.g. ``{"output": "tex", "path": "…/out/{id}_{company}.tex"}``).
    An unregistered type degrades to the plain log record — never a ``KeyError``.
    """
    cfg = node.config or {}
    out_type = str(cfg.get("type") or "log").lower()
    target = cfg.get("target")
    payload = data
    if isinstance(data, dict):
        payload = data.get("summary", data.get("output", data))
    record = {"output_type": out_type, "target": target, "payload": _safe(payload)}

    handler = resolve_output_handler(out_type)
    if handler is not None:
        try:
            record.update(handler(node, payload, company=company, run_id=run_id))
            return record
        except Exception as exc:  # a handler is best-effort; never crash the run
            record["output"] = out_type
            record["error"] = str(exc)
            logger.warning("flows: output[{}] {} handler failed: {}", out_type, node.id, exc)
            return record

    logger.info("flows: output[{}] {} → {}", out_type, node.id, target)
    return record


def _safe(value: Any) -> Any:
    """A JSON-safe shallow view of a value for the run record."""
    if isinstance(value, (str, int, float, bool, type(None), dict, list)):
        return value
    return str(value)


def _json_safe(value: Any) -> Any:
    """Return a detached JSON value suitable for a durable checkpoint."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _cursor_snapshot(
    *,
    frontier: list[tuple[str, Any]],
    visited: set[str],
    steps: list[dict],
    final_data: Any,
    last_output: Any,
    resolved_company: str,
    base_cv: str,
    run_started: str,
    pending_approval: Optional[dict],
    approval_decisions: dict[str, bool],
    status: str,
    pending_retry: Optional[dict] = None,
    retry_state: Optional[dict[str, int]] = None,
) -> dict:
    """Build the versioned, JSON-only cursor consumed by a later resume."""
    return _json_safe(
        {
            "schema_version": 1,
            "status": status,
            "frontier": [
                {"node": node_id, "data": data} for node_id, data in frontier
            ],
            "visited": sorted(visited),
            "steps": steps,
            "final_data": final_data,
            "last_output": last_output,
            "resolved_company": resolved_company,
            "base_cv": base_cv,
            "run_started": run_started,
            "pending_approval": pending_approval,
            "approval_decisions": approval_decisions,
            "pending_retry": pending_retry,
            "retry_state": dict(retry_state or {}),
        }
    )


def _restore_frontier(value: Any) -> list[tuple[str, Any]]:
    """Decode a cursor frontier, ignoring malformed entries defensively."""
    out: list[tuple[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and item.get("node"):
            out.append((str(item["node"]), item.get("data")))
    return out


# ── the walk ─────────────────────────────────────────────────────────────────
async def run_flow(
    flow: Flow,
    *,
    inputs: Optional[dict] = None,
    config: Optional[Config] = None,
    executor: Optional[Executor] = None,
    company: Optional[str] = None,
    base_cv: Optional[str] = None,
    context: Optional[str] = None,
    run_id: Optional[str] = None,
    resume_state: Optional[dict] = None,
    checkpoint_hook: Optional[CheckpointHook] = None,
    approval_context: Optional[dict] = None,
    approval_decisions: Optional[dict[str, bool]] = None,
) -> dict:
    """Execute ``flow`` and return a structured run record. Never raises.

    ``inputs`` seed the trigger; ``executor`` overrides the default org department
    brain (the test/offline seam); ``company`` is an optional override for the CV
    export filename/prompts (override → context → ``"untitled"``). See the module
    docstring for execution order and branch semantics.

    ``base_cv`` optionally OVERRIDES the base CV used for ``{base_cv}`` interpolation
    and the contact-preservation gate: when non-``None`` it is used verbatim (e.g. a
    CV a caller extracted from an uploaded document), instead of reading
    ``cv.load_base()`` from disk. ``None`` (the default) keeps the historical
    load-from-disk behaviour, so existing callers are unchanged.

    ``context`` selects the :mod:`lhassa.flows.registries` context provider that
    resolves ``{base_cv}``/``{company}`` (default ``"cv"`` — today's behaviour,
    unchanged). A flow may instead set ``config.context`` on the trigger node
    (checked first); an unknown provider degrades to the default, never raises.

    Durable execution is opt-in. ``run_id`` fixes the execution identity;
    ``resume_state`` restores a cursor previously returned as ``record["resume_state"]``;
    and ``checkpoint_hook`` receives a detached JSON cursor after every node.
    ``approval_decisions`` lets a resumed approval gate pass without parking another
    request. Calls that omit these arguments retain the historical in-memory behavior.
    """
    if config is None:
        # Local import: stay correct if a test reloads ``lhassa.config`` (a stale
        # module-level binding would resolve the real default paths).
        from lhassa.config import Config as _Config

        config = _Config()
    executor = executor or _default_executor

    # Resolve the fixed base CV (for {base_cv} interpolation + the contact gate)
    # and the company (override → context → "untitled") through the registered
    # context provider (default "cv" — today's behaviour, unchanged). Degrades
    # gracefully on any failure so a broken CV seam never crashes a run.
    provider_name = context
    if provider_name is None:
        try:
            trigger_cfg = flow.trigger().config or {}
            provider_name = trigger_cfg.get("context")
        except Exception:
            provider_name = None
    ctx = await resolve_context(
        provider_name,
        inputs=inputs,
        company_override=company,
        base_cv_override=base_cv,
        config=config,
    )
    base_cv = ctx.get("base_cv", base_cv or "")
    resolved_company = str(ctx.get("company") or resolve_company(company, inputs))
    durable = any(
        value is not None
        for value in (
            run_id,
            resume_state,
            checkpoint_hook,
            approval_context,
            approval_decisions,
        )
    )
    run_id = run_id or uuid.uuid4().hex
    run_started = _iso_now()
    run_started_perf = time.perf_counter()

    # Validate up front — an invalid graph is a single failed run record, not a crash.
    try:
        flow.validate()
        start = flow.trigger()
    except Exception as exc:
        finished = _iso_now()
        return {
            "id": run_id,
            "flow_id": getattr(flow, "id", None),
            "status": "failed",
            "started_at": run_started,
            "finished_at": finished,
            "duration_ms": _elapsed_ms(run_started_perf),
            "cost_usd": 0.0,
            "steps": [{"node": None, "kind": None, "ok": False, "status": "failed", "error": str(exc)}],
            "output": None,
        }

    state = resume_state if isinstance(resume_state, dict) else {}
    if state:
        steps = _json_safe(state.get("steps") or [])
        visited = {str(item) for item in state.get("visited") or []}
        frontier = _restore_frontier(state.get("frontier"))
        last_output = _json_safe(state.get("last_output"))
        final_data = _json_safe(state.get("final_data"))
        resolved_company = str(
            state.get("resolved_company") or resolved_company
        )
        base_cv = str(state.get("base_cv") if "base_cv" in state else base_cv)
        run_started = str(state.get("run_started") or run_started)
        pending_approval = state.get("pending_approval")
        pending_retry = state.get("pending_retry")
        retry_state = {
            str(k): int(v) for k, v in (state.get("retry_state") or {}).items()
        }
    else:
        steps = []
        visited = set()
        frontier = [(start.id, inputs or {})]
        last_output = None
        final_data = inputs or {}
        pending_approval = None
        pending_retry = None
        retry_state = {}

    decisions = {
        str(key): bool(value)
        for key, value in (state.get("approval_decisions") or {}).items()
    }
    decisions.update(
        {str(key): bool(value) for key, value in (approval_decisions or {}).items()}
    )
    cap = len(flow.nodes) * _MAX_STEPS_FACTOR + 8
    failed = False
    waiting_approval = False
    waiting_retry = False

    async def checkpoint(status: str) -> dict:
        cursor = _cursor_snapshot(
            frontier=frontier,
            visited=visited,
            steps=steps,
            final_data=final_data,
            last_output=last_output,
            resolved_company=resolved_company,
            base_cv=base_cv or "",
            run_started=run_started,
            pending_approval=pending_approval,
            approval_decisions=decisions,
            pending_retry=pending_retry,
            retry_state=retry_state,
            status=status,
        )
        if checkpoint_hook is not None:
            await checkpoint_hook(cursor)
        return cursor

    latest_cursor: Optional[dict] = None

    while frontier and len(steps) < cap:
        node_id, data = frontier.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        node = flow.node(node_id)
        replace_step_idx: Optional[int] = None
        if (
            isinstance(pending_approval, dict)
            and pending_approval.get("node_id", pending_approval.get("node")) == node_id
        ):
            candidate = pending_approval.get("step_index")
            if isinstance(candidate, int) and 0 <= candidate < len(steps):
                replace_step_idx = candidate
        elif (
            isinstance(pending_retry, dict)
            and pending_retry.get("node_id") == node_id
        ):
            candidate = pending_retry.get("step_index")
            if isinstance(candidate, int) and 0 <= candidate < len(steps):
                replace_step_idx = candidate

        step_started = _iso_now()
        step_started_perf = time.perf_counter()
        step: dict = {
            "node": node.id,
            "kind": node.kind.value,
            "ok": True,
            "status": "completed",
            "started_at": step_started,
        }
        data_out: Any = data
        passed = True  # only gates can set this False
        node_pending_retry: Optional[dict] = None
        try:
            if node.kind == NodeKind.TRIGGER:
                step["trigger"] = node.config.get("type", "manual")
                step["inputs"] = _safe(data)
            elif node.kind == NodeKind.TASK:
                retry_cfg = _retry_config(node)
                timeout_raw = (node.config or {}).get("timeout_seconds")
                try:
                    timeout_seconds = (
                        float(timeout_raw) if timeout_raw is not None else None
                    )
                except (TypeError, ValueError):
                    timeout_seconds = None
                attempt = int(retry_state.get(node.id, 0))
                while True:
                    attempt += 1
                    try:
                        coro = _run_task(
                            node, data, config=config, executor=executor,
                            base_cv=base_cv, company=resolved_company,
                        )
                        if timeout_seconds is not None:
                            extra, data_out = await asyncio.wait_for(
                                coro, timeout=timeout_seconds
                            )
                        else:
                            extra, data_out = await coro
                        retry_state.pop(node.id, None)
                        extra["attempt"] = attempt
                        step.update(extra)
                        break
                    except Exception as exc:
                        reason = (
                            "timeout"
                            if isinstance(exc, asyncio.TimeoutError)
                            else "error"
                        )
                        retryable = (
                            reason in retry_cfg["retry_on"]
                            and attempt < retry_cfg["max_attempts"]
                        )
                        if not retryable:
                            raise
                        retry_state[node.id] = attempt
                        if durable:
                            wait_seconds = _backoff_seconds(retry_cfg, attempt)
                            next_wakeup_at = datetime.now(timezone.utc) + timedelta(
                                seconds=wait_seconds
                            )
                            node_pending_retry = {
                                "node_id": node.id,
                                "attempt": attempt,
                                "reason": reason,
                                "wait_seconds": wait_seconds,
                                "next_wakeup_at": next_wakeup_at.isoformat(),
                            }
                            step["ok"] = False
                            step["status"] = "waiting_retry"
                            step["attempt"] = attempt
                            step["error"] = str(exc)
                            step["retry_reason"] = reason
                            break
                        # Non-durable (in-memory) run: retry inline, no real sleep —
                        # the deterministic $0 plane never blocks on wall-clock time.
                        logger.debug(
                            "flows: node {} attempt {} failed ({}), retrying inline",
                            node.id,
                            attempt,
                            exc,
                        )
                        continue
            elif node.kind == NodeKind.GATE:
                extra, data_out, passed = await _run_gate(
                    node,
                    data,
                    config=config,
                    base_cv=base_cv,
                    run_id=run_id if durable else None,
                    flow_id=flow.id if durable else None,
                    approval_context=approval_context,
                    approval_decisions=decisions,
                )
                step.update(extra)
            elif node.kind == NodeKind.OUTPUT:
                record = _run_output(
                    node, data, company=resolved_company, run_id=run_id
                )
                step.update(record)
                last_output = record
                data_out = data
            elif node.kind == NodeKind.BRANCH:
                step["branch"] = True
        except Exception as exc:  # a node error is a failed step; the path stops here
            step["ok"] = False
            step["status"] = "failed"
            step["error"] = str(exc)
            step["finished_at"] = _iso_now()
            step["duration_ms"] = _elapsed_ms(step_started_perf)
            failed = True
            steps.append(step)
            final_data = data_out
            logger.warning("flows: node {} failed: {}", node.id, exc)
            if durable:
                try:
                    latest_cursor = await checkpoint("failed")
                except Exception as checkpoint_exc:
                    logger.warning(
                        "flows: checkpoint after failed node {} failed: {}",
                        node.id,
                        checkpoint_exc,
                    )
            continue

        if node.kind == NodeKind.GATE and step.get("needs_approval"):
            step["status"] = "needs_approval"
        step["finished_at"] = _iso_now()
        step["duration_ms"] = _elapsed_ms(step_started_perf)
        if node.kind == NodeKind.TASK and isinstance(step.get("result"), dict):
            step["cost_usd"] = float(step["result"].get("cost_usd") or 0.0)

        if replace_step_idx is None:
            steps.append(step)
        else:
            previous_step = steps[replace_step_idx]
            for field in ("approval_id", "approval_payload"):
                if field in previous_step:
                    step.setdefault(field, previous_step[field])
            steps[replace_step_idx] = step
            pending_approval = None
            pending_retry = None
        final_data = data_out

        if node.kind == NodeKind.GATE and step.get("needs_approval") and durable:
            visited.discard(node.id)
            frontier.insert(0, (node.id, data_out))
            pending_approval = {
                "node_id": node.id,
                "approval_id": step.get("approval_id"),
                "approval_payload": step.get("approval_payload"),
                "step_index": len(steps) - 1,
            }
            waiting_approval = True
            try:
                latest_cursor = await checkpoint("waiting_approval")
            except Exception as exc:
                failed = True
                waiting_approval = False
                step["ok"] = False
                step["status"] = "failed"
                step["error"] = f"checkpoint failed: {exc}"
                logger.warning("flows: approval checkpoint failed: {}", exc)
            break

        if node_pending_retry is not None and durable:
            visited.discard(node.id)
            frontier.insert(0, (node.id, data))
            node_pending_retry["step_index"] = len(steps) - 1
            pending_retry = node_pending_retry
            waiting_retry = True
            try:
                latest_cursor = await checkpoint("waiting_retry")
            except Exception as exc:
                failed = True
                waiting_retry = False
                step["ok"] = False
                step["status"] = "failed"
                step["error"] = f"checkpoint failed: {exc}"
                logger.warning("flows: retry checkpoint failed: {}", exc)
            break

        # ── pick the outgoing edges to follow ────────────────────────────────
        out_edges = flow.edges_from(node.id)
        ctx = _edge_ctx(step, data_out)
        if node.kind == NodeKind.BRANCH:
            chosen = next((e for e in out_edges if eval_when(e.when, ctx)), None)
            step["chosen_edge"] = chosen.to_node if chosen else None
            if chosen:
                frontier.append((chosen.to_node, data_out))
        else:
            for e in out_edges:
                # A gate that did not pass drops its unconditional edges (path stops),
                # but a `when` that explicitly handles the failure may still route.
                if node.kind == NodeKind.GATE and not passed and e.when is None:
                    continue
                if eval_when(e.when, ctx):
                    frontier.append((e.to_node, data_out))

        if durable:
            try:
                latest_cursor = await checkpoint("running")
            except Exception as exc:
                failed = True
                frontier.clear()
                logger.warning("flows: checkpoint after node {} failed: {}", node.id, exc)
                break

    status = (
        "failed"
        if failed
        else (
            "waiting_approval"
            if waiting_approval
            else ("waiting_retry" if waiting_retry else "completed")
        )
    )
    output = last_output if last_output is not None else _safe(final_data)
    cost_usd = sum(float(s.get("cost_usd") or 0.0) for s in steps)
    if durable and not waiting_approval and not waiting_retry:
        latest_cursor = _cursor_snapshot(
            frontier=frontier,
            visited=visited,
            steps=steps,
            final_data=final_data,
            last_output=last_output,
            resolved_company=resolved_company,
            base_cv=base_cv or "",
            run_started=run_started,
            pending_approval=pending_approval,
            approval_decisions=decisions,
            pending_retry=pending_retry,
            retry_state=retry_state,
            status=status,
        )
    record = {
        "id": run_id,
        "flow_id": flow.id,
        "status": status,
        "started_at": run_started,
        "finished_at": None if (waiting_approval or waiting_retry) else _iso_now(),
        "duration_ms": _elapsed_ms(run_started_perf),
        "cost_usd": round(cost_usd, 6),
        "steps": steps,
        "output": output,
    }
    if durable:
        record["resume_state"] = latest_cursor or await checkpoint(status)
    return record


def _edge_ctx(step: dict, data_out: Any) -> dict:
    """The context an edge ``when`` is evaluated against: the step + the data fields."""
    ctx = dict(step)
    if isinstance(data_out, dict):
        for k, v in data_out.items():
            ctx.setdefault(k, v)
    ctx.setdefault("data", _safe(data_out))
    return ctx
