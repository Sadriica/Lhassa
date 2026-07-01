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
    becomes the data that flows downstream.
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

import os
import uuid
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.flows.model import Flow, FlowNode, NodeKind

# An executor seam: ``async (goal, *, department, config) -> dict``. Defaults to the
# org's department brain (offline + $0 unless LHASSA_ORG_LIVE). Injected in tests.
Executor = Callable[..., Awaitable[dict]]

_TRUTHY = {"1", "true", "yes", "on"}
_MAX_STEPS_FACTOR = 4  # hard cap = nodes * factor + 8, defends against runaway walks


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

    HYBRID: a pinned ``config.brain`` wins (``pinned=True``). Otherwise the node's
    ``skill``/``domain`` (falling back to the prompt or the upstream data) auto-routes
    through the org topology to a leaf department, reusing the org's deterministic
    keyword routing. Any routing failure degrades to the skill text (or ``"general"``)
    so a task always has a target and never raises here.
    """
    cfg = node.config or {}
    pinned = (cfg.get("brain") or "").strip()
    if pinned:
        return pinned, True

    skill = (cfg.get("skill") or cfg.get("domain") or "").strip()
    routing_text = skill or str(cfg.get("prompt") or data or "").strip()
    try:
        from lhassa.org import topology as topo_mod
        from lhassa.org.coordinator import route_in_topology

        topology = topo_mod.load_topology(config)
        node_id = topology.root().id
        # Walk down one level at a time until a leaf — route_in_topology is single-hop.
        for _ in range(64):
            if topology.is_leaf(node_id):
                break
            node_id = route_in_topology(topology, node_id, routing_text)
        leaf = topology.node(node_id)
        return (leaf.department or leaf.id), False
    except Exception as exc:
        logger.debug("flows: auto-route degraded to skill text ({}): {}", routing_text, exc)
        return (skill or "general"), False


async def _default_executor(goal: str, *, department: str, config: Config, **_: Any) -> dict:
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

    return await brain_executor(goal, department=department, config=config)


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
    goal = _format_goal(prompt, data, base_cv=base_cv, company=company) or department
    result = await executor(goal, department=department, config=config)
    extra = {
        "brain": department,
        "pinned": pinned,
        "goal": goal,
        "live": _live_enabled(),
        "result": result,
    }
    return extra, result


def _format_goal(prompt: str, data: Any, *, base_cv: str = "", company: str = "") -> str:
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
    summary = data
    if isinstance(data, dict):
        summary = data.get("summary", data.get("output", data))
    replacements = {
        "{input}": str(summary),
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


async def _run_gate(node: FlowNode, data: Any, *, config: Config) -> tuple[dict, Any, bool]:
    """Execute a gate node; returns ``(step_extra, data_out, passed)``.

    ``passed`` drives edge-following: a non-pass drops unconditional outgoing edges.
    """
    cfg = node.config or {}
    gate_type = str(cfg.get("type") or "verify").lower()

    if gate_type == "approval":
        approval_id = None
        try:
            from lhassa.security import approval

            instance_id = getattr(config, "instance_id", None) or Config.instance_id
            approval_id = await approval.request_approval(
                instance_id,
                tipo="flow_gate",
                payload={"accion": "flow_gate", "node": node.id, "data": _safe(data)},
            )
        except Exception as exc:  # parking is best-effort; record it, don't crash
            logger.debug("flows: approval gate could not park ({}): {}", node.id, exc)
        # A parked approval is NOT a pass — the path waits (records needs_approval).
        return (
            {"gate": "approval", "passed": False, "needs_approval": True,
             "approval_id": approval_id},
            data,
            False,
        )

    # verify gate
    from lhassa.agents.verify import verify

    spec = cfg.get("spec") or {}
    artifact = data
    if isinstance(data, dict):
        artifact = data.get("summary", data.get("output", data))
    verdict = verify(artifact, spec=spec)
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

    Types: ``notify`` / ``doc`` / ``log`` record the payload; ``tex`` additionally
    EXPORTS a tailored ``.tex`` file for the CV work-flow via :func:`lhassa.cv.export`
    and records the written path (``{"output": "tex", "path": "…/out/{id}_{company}.tex"}``).
    """
    cfg = node.config or {}
    out_type = str(cfg.get("type") or "log").lower()
    target = cfg.get("target")
    payload = data
    if isinstance(data, dict):
        payload = data.get("summary", data.get("output", data))
    record = {"output_type": out_type, "target": target, "payload": _safe(payload)}

    if out_type == "tex":
        try:
            from lhassa import cv

            artifact = payload if isinstance(payload, str) else str(payload)
            path = cv.export(artifact, company=company, run_id=run_id)
            record["output"] = "tex"
            record["path"] = str(path)
            logger.info("flows: output[tex] {} → {}", node.id, path)
            return record
        except Exception as exc:  # writing a file is best-effort; never crash the run
            record["output"] = "tex"
            record["error"] = str(exc)
            logger.warning("flows: output[tex] {} could not export: {}", node.id, exc)
            return record

    logger.info("flows: output[{}] {} → {}", out_type, node.id, target)
    return record


def _safe(value: Any) -> Any:
    """A JSON-safe shallow view of a value for the run record."""
    if isinstance(value, (str, int, float, bool, type(None), dict, list)):
        return value
    return str(value)


# ── the walk ─────────────────────────────────────────────────────────────────
async def run_flow(
    flow: Flow,
    *,
    inputs: Optional[dict] = None,
    config: Optional[Config] = None,
    executor: Optional[Executor] = None,
    company: Optional[str] = None,
) -> dict:
    """Execute ``flow`` and return a structured run record. Never raises.

    ``inputs`` seed the trigger; ``executor`` overrides the default org department
    brain (the test/offline seam); ``company`` is an optional override for the CV
    export filename/prompts (override → context → ``"untitled"``). See the module
    docstring for execution order and branch semantics.
    """
    if config is None:
        # Local import: stay correct if a test reloads ``lhassa.config`` (a stale
        # module-level binding would resolve the real default paths).
        from lhassa.config import Config as _Config

        config = _Config()
    executor = executor or _default_executor

    # Load the fixed base CV once (for {base_cv} interpolation) and resolve the
    # company (override → context → "untitled"). Both degrade gracefully offline.
    try:
        from lhassa import cv

        base_cv = cv.load_base() or ""
    except Exception as exc:  # a broken CV seam must never crash a run
        logger.debug("flows: base CV unavailable ({})", exc)
        base_cv = ""
    resolved_company = resolve_company(company, inputs)
    run_id = uuid.uuid4().hex

    # Validate up front — an invalid graph is a single failed run record, not a crash.
    try:
        flow.validate()
        start = flow.trigger()
    except Exception as exc:
        return {
            "flow_id": getattr(flow, "id", None),
            "status": "failed",
            "steps": [{"node": None, "kind": None, "ok": False, "error": str(exc)}],
            "output": None,
        }

    steps: list[dict] = []
    visited: set[str] = set()
    frontier: list[tuple[str, Any]] = [(start.id, inputs or {})]
    cap = len(flow.nodes) * _MAX_STEPS_FACTOR + 8
    last_output: Any = None
    final_data: Any = inputs or {}
    failed = False

    while frontier and len(steps) < cap:
        node_id, data = frontier.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        node = flow.node(node_id)

        step: dict = {"node": node.id, "kind": node.kind.value, "ok": True}
        data_out: Any = data
        passed = True  # only gates can set this False
        try:
            if node.kind == NodeKind.TRIGGER:
                step["trigger"] = node.config.get("type", "manual")
                step["inputs"] = _safe(data)
            elif node.kind == NodeKind.TASK:
                extra, data_out = await _run_task(
                    node, data, config=config, executor=executor,
                    base_cv=base_cv, company=resolved_company,
                )
                step.update(extra)
            elif node.kind == NodeKind.GATE:
                extra, data_out, passed = await _run_gate(node, data, config=config)
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
            step["error"] = str(exc)
            failed = True
            steps.append(step)
            logger.warning("flows: node {} failed: {}", node.id, exc)
            continue

        steps.append(step)
        final_data = data_out

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

    status = "failed" if failed else "completed"
    output = last_output if last_output is not None else _safe(final_data)
    return {"flow_id": flow.id, "status": status, "steps": steps, "output": output}


def _edge_ctx(step: dict, data_out: Any) -> dict:
    """The context an edge ``when`` is evaluated against: the step + the data fields."""
    ctx = dict(step)
    if isinstance(data_out, dict):
        for k, v in data_out.items():
            ctx.setdefault(k, v)
    ctx.setdefault("data", _safe(data_out))
    return ctx
