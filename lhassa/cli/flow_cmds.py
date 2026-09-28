# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa flows`` / ``lhassa flow``: the work-flow surface of the CLI.

Two sub-apps live here, deliberately:

``lhassa flows`` (plural, read-mostly)
    The gallery view: ``list`` / ``templates`` / ``show`` / ``apply``. Unchanged.

``lhassa flow`` (singular, the full lifecycle)
    ``create`` / ``edit`` / ``validate`` / ``run`` / ``watch`` / ``logs`` /
    ``delete``: the CLI half of what the dashboard canvas and
    ``api/flow_routes.py`` already do, over the SAME seams:

      * writes go through :class:`~lhassa.flows.store.FlowStore` (which validates
        via :meth:`~lhassa.flows.model.Flow.validate`, exactly like the API's
        ``_parse``);
      * a **run** goes through :func:`lhassa.org.delegate.delegate_flow`: never
        :func:`lhassa.flows.engine.run_flow` directly. A flow is never run bare;
        it flows down the command chain with a $0 audit episode at the hop.
      * ``watch`` / ``logs`` read the durable kernel
        (:class:`~lhassa.flows.runtime.FlowRuntimeRepository`) directly, so they
        work with the API server down; the same virtue as ``lhassa tui``.

No LLM and no network are touched by anything except ``run`` (and that only if
``LHASSA_ORG_LIVE`` makes the flow's own task nodes think).

Both sub-apps are registered onto the main Typer ``app``: see ``lhassa.cli.app``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

flow_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Inspect stored work-flows and apply the flow-templates gallery.",
)

one_flow_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Author, run and follow a single work-flow (create/edit/run/watch/logs).",
)


def _out() -> Console:
    return Console()


def _err() -> Console:
    return Console(stderr=True)


def _dump_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _config():
    from lhassa.config import Config

    return Config()


def _store():
    from lhassa.flows.store import FlowStore

    return FlowStore(_config())


# ── list ─────────────────────────────────────────────────────────────────────────


def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the flows as JSON."),
) -> None:
    """List every stored work-flow (id, name, node count)."""
    flows = _store().list_flows()
    if json_out:
        _dump_json(
            [{"id": f.id, "name": f.name, "nodes": len(f.nodes)} for f in flows]
        )
        return
    table = Table(title="Work-flows")
    for col in ("id", "name", "nodes", "description"):
        table.add_column(col)
    for f in flows:
        table.add_row(f.id, f.name, str(len(f.nodes)), f.description or "—")
    _out().print(table)


# ── templates (the flow-templates gallery) ───────────────────────────────────────


def templates_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the gallery as JSON."),
) -> None:
    """List the flow-templates gallery (Research Brief, Daily Summary, …)."""
    from lhassa.flows import templates

    catalog = templates.list_templates()
    if json_out:
        _dump_json(catalog)
        return
    table = Table(title="Flow templates")
    for col in ("id", "name", "nodes", "kinds", "teaches"):
        table.add_column(col)
    for t in catalog:
        table.add_row(
            str(t["id"]),
            str(t["name"]),
            str(t["node_count"]),
            ", ".join(t["node_kinds"]),
            str(t["teaches"]),
        )
    _out().print(table)


# ── apply ────────────────────────────────────────────────────────────────────────


def apply_cmd(
    template_id: str = typer.Argument(..., help="Template id (see `lhassa flows templates`)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the new flow as JSON."),
) -> None:
    """Instantiate a template into the flow store under a fresh id (never clobbers)."""
    from lhassa.flows import templates

    config = _config()
    try:
        flow = templates.apply_template(template_id, config=config)
    except ValueError as exc:
        _err().print(f"Cannot apply template: {exc}", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(flow.to_dict())
        return
    _out().print(f"Applied {template_id!r} → new flow id {flow.id!r}.")


# ── show (one flow, compact) ──────────────────────────────────────────────────────


def show_cmd(
    flow_id: str = typer.Argument(..., help="Flow id to inspect."),
    json_out: bool = typer.Option(False, "--json", help="Emit the flow as JSON."),
) -> None:
    """Show one flow's nodes/edges (compact)."""
    flow = _store().get(flow_id)
    if flow is None:
        _err().print(f"No flow {flow_id!r} in the store.", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(flow.to_dict())
        return
    out = _out()
    out.print(f"id:          {flow.id}")
    out.print(f"name:        {flow.name}")
    out.print(f"description: {flow.description or '—'}")
    out.print("nodes:")
    for n in flow.nodes:
        # Escape the kind so Rich renders "[task]" as text, not as a markup tag.
        out.print(f"  - {n.id} \\[{n.kind.value}] {n.label or ''}".rstrip())
    out.print("edges:")
    for e in flow.edges:
        when = f" when={json.dumps(e.when, ensure_ascii=False)}" if e.when else ""
        out.print(f"  - {e.from_node} -> {e.to_node}{when}")


# ── the singular `lhassa flow` lifecycle ─────────────────────────────────────────

_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


def _slug(text: str) -> str:
    """A store-safe flow id from a human name (``"My Flow!" → "my-flow"``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return slug or "flow"


def _fresh_id(store, base_id: str) -> str:
    """``base_id`` / ``base_id-2`` / …; the first id not already in the store."""
    if store.get(base_id) is None:
        return base_id
    n = 2
    while store.get(f"{base_id}-{n}") is not None:
        n += 1
    return f"{base_id}-{n}"


def _blank_flow(flow_id: str, name: str):
    """The minimal legal flow: one trigger node, no edges (validates clean)."""
    from lhassa.flows.model import Flow, FlowNode, NodeKind

    return Flow(
        id=flow_id,
        name=name,
        description="",
        nodes=[FlowNode(id="trigger", kind=NodeKind.TRIGGER, label=name)],
        edges=[],
    )


def _parse_inputs(raw: "Optional[str]") -> "Optional[dict]":
    """``--input`` → a dict. Bare text is wrapped as ``{"summary": text}``.

    Mirrors ``api/flow_routes.py``'s convenience: a bare ``{"input": "<text>"}``
    seeds the ``{input}`` placeholder as ``{"summary": ...}``.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"summary": raw}
    if isinstance(parsed, dict):
        if list(parsed) == ["input"]:
            return {"summary": parsed["input"]}
        return parsed
    return {"summary": parsed}


def _instance_id() -> str:
    from lhassa.config import Config

    return Config.instance_id


def _repo():
    from lhassa.flows.runtime import FlowRuntimeRepository

    return FlowRuntimeRepository()


def _require_flow(flow_id: str):
    flow = _store().get(flow_id)
    if flow is None:
        _err().print(f"No flow {flow_id!r} in the store.", style="red")
        raise typer.Exit(code=1)
    return flow


def create_cmd(
    name: str = typer.Argument(..., help="Human name for the new flow."),
    template: Optional[str] = typer.Option(
        None, "--template", "-t", help="Scaffold from a flow template (see `lhassa flows templates`)."
    ),
    blank: bool = typer.Option(
        False, "--blank", help="Scaffold a minimal flow (one trigger node). The default."
    ),
    flow_id: Optional[str] = typer.Option(
        None, "--id", help="Explicit flow id (default: a slug of the name, de-duplicated)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the new flow as JSON."),
) -> None:
    """Create a work-flow; from a template, or a blank one-trigger scaffold."""
    from lhassa.flows import templates
    from lhassa.flows.model import FlowValidationError

    if template and blank:
        _err().print("Pass either --template or --blank, not both.", style="red")
        raise typer.Exit(code=2)

    store = _store()
    base_id = _slug(flow_id or name)
    new_id = base_id if flow_id else _fresh_id(store, base_id)

    if template:
        try:
            flow = templates.build(template, flow_id=new_id)
        except Exception as exc:  # unknown template id / a broken builder
            _err().print(f"Cannot scaffold from template: {exc}", style="red")
            raise typer.Exit(code=1)
        flow.name = name
    else:
        flow = _blank_flow(new_id, name)

    try:
        store.create(flow)
    except FlowValidationError as exc:
        _err().print(f"Cannot create flow: {exc}", style="red")
        raise typer.Exit(code=1)

    if json_out:
        _dump_json(flow.to_dict())
        return
    origin = f"template {template!r}" if template else "a blank scaffold"
    _out().print(
        f"Created flow [bold]{flow.id}[/] ({flow.name!r}) from {origin}: "
        f"{len(flow.nodes)} node(s)."
    )
    _out().print(f"[dim]Edit it with:[/] lhassa flow edit {flow.id}")


def _editor_command() -> list[str]:
    """``$VISUAL`` / ``$EDITOR``, falling back to ``vi``."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    return editor.split()


def edit_cmd(
    flow_id: str = typer.Argument(..., help="Flow id to edit."),
    from_json: Optional[str] = typer.Option(
        None,
        "--from-json",
        help="Replace the flow from a JSON file (or '-' for stdin) instead of opening an editor.",
    ),
) -> None:
    """Open a flow's JSON in ``$EDITOR``; persist only if it still validates.

    Invalid JSON or a failed validation NEVER discards the edits: the buffer stays
    on disk and the command offers to re-open it (or prints its path on abort).

    ``--from-json`` is the same operation without a human at a terminal. It
    exists because Lhassa itself is a caller here: asked to author a work-flow,
    it could scaffold one with ``create`` and then had no way to fill in the
    nodes, since ``$EDITOR`` needs a session it does not have. Validation is
    identical to the interactive path; a rejected document changes nothing.
    """
    from lhassa.flows.model import Flow, FlowValidationError

    flow = _require_flow(flow_id)
    store = _store()
    out, err = _out(), _err()

    if from_json is not None:
        try:
            raw = (
                sys.stdin.read()
                if from_json == "-"
                else Path(from_json).read_text(encoding="utf-8")
            )
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise FlowValidationError("a flow must be a JSON object")
            data["id"] = flow_id  # the addressed id is authoritative, as in PUT
            edited = Flow.from_dict(data)
            edited.validate()
        except OSError as exc:
            err.print(f"Could not read {from_json}: {exc}", style="red")
            raise typer.Exit(code=1)
        except (json.JSONDecodeError, FlowValidationError, ValueError) as exc:
            err.print(f"Not a valid flow: {exc}", style="red")
            err.print("Nothing was written to the store.", style="yellow")
            raise typer.Exit(code=1)
        store.save(edited)
        out.print(f"Saved flow [bold]{edited.id}[/]; {len(edited.nodes)} node(s).")
        return

    fd, tmp_name = tempfile.mkstemp(prefix=f"lhassa-flow-{_slug(flow_id)}-", suffix=".json")
    tmp = Path(tmp_name)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(flow.to_dict(), ensure_ascii=False, indent=2) + "\n")

    try:
        while True:
            try:
                subprocess.call([*_editor_command(), str(tmp)])
            except OSError as exc:
                err.print(f"Could not launch the editor: {exc}", style="red")
                err.print(f"Your buffer is at {tmp}", style="yellow")
                raise typer.Exit(code=1)

            raw = tmp.read_text(encoding="utf-8")
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise FlowValidationError("a flow must be a JSON object")
                data["id"] = flow_id  # the addressed id is authoritative, as in PUT
                edited = Flow.from_dict(data)
                edited.validate()
            except (json.JSONDecodeError, FlowValidationError, ValueError) as exc:
                err.print(f"Not a valid flow: {exc}", style="red")
                err.print(f"Your edits are preserved at {tmp}", style="yellow")
                if not typer.confirm("Re-open the editor?", default=True):
                    err.print("Aborted; nothing was written to the store.", style="yellow")
                    raise typer.Exit(code=1)
                continue

            store.save(edited)
            out.print(f"Saved flow [bold]{edited.id}[/]; {len(edited.nodes)} node(s).")
            tmp.unlink(missing_ok=True)
            return
    except typer.Exit:
        raise
    except BaseException:
        err.print(f"Your edits are preserved at {tmp}", style="yellow")
        raise


def validate_cmd(
    target: str = typer.Argument(..., help="A stored flow id, or a path to a flow JSON file."),
    json_out: bool = typer.Option(False, "--json", help="Emit the verdict as JSON."),
) -> None:
    """Validate a flow WITHOUT persisting it. Exit code 0 = valid, 1 = invalid."""
    from lhassa.flows.model import Flow, FlowValidationError

    path = Path(target)
    source = ""
    try:
        if path.exists() and path.is_file():
            source = str(path)
            flow = Flow.from_dict(json.loads(path.read_text(encoding="utf-8")))
        else:
            source = f"store:{target}"
            stored = _store().get(target)
            if stored is None:
                raise FlowValidationError(
                    f"no flow {target!r} in the store and no file at that path"
                )
            flow = stored
        flow.validate()
    except (json.JSONDecodeError, FlowValidationError, ValueError, OSError) as exc:
        if json_out:
            _dump_json({"source": source or target, "valid": False, "error": str(exc)})
        else:
            _err().print(f"✗ invalid: {exc}", style="red")
        raise typer.Exit(code=1)

    if json_out:
        _dump_json(
            {
                "source": source,
                "valid": True,
                "id": flow.id,
                "nodes": len(flow.nodes),
                "edges": len(flow.edges),
            }
        )
        return
    _out().print(
        f"✓ [bold]{flow.id}[/] is valid; {len(flow.nodes)} node(s), {len(flow.edges)} edge(s)."
    )


def run_cmd(
    flow_id: str = typer.Argument(..., help="Flow id to run."),
    input_json: Optional[str] = typer.Option(
        None, "--input", help='Trigger inputs as JSON (bare text becomes {"summary": text}).'
    ),
    company: Optional[str] = typer.Option(
        None, "--company", help="Company override for flows whose output is a tailored document."
    ),
    idempotency_key: Optional[str] = typer.Option(
        None, "--idempotency-key", help="Reuse an existing durable run instead of starting a new one."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the whole run record as JSON."),
) -> None:
    """Run a stored flow THROUGH the org hierarchy (never bare).

    The run goes through :func:`lhassa.org.delegate.delegate_flow`, the same seam
    ``POST /api/flows/{id}/run`` uses: the flow flows down the command chain with a
    deterministic $0 audit episode at the hop, then executes durably.
    """
    from lhassa.flows.projection import project_flow_run, trigger_from_inputs
    from lhassa.org.delegate import delegate_flow

    flow = _require_flow(flow_id)
    inputs = _parse_inputs(input_json)

    async def _go() -> dict:
        record = await delegate_flow(
            flow,
            inputs=inputs,
            company=company,
            durable=True,
            idempotency_key=idempotency_key or None,
        )
        try:
            await project_flow_run(record, trigger=trigger_from_inputs(inputs))
        except Exception:  # projection is observability; it must never fail a run
            pass
        return record

    record = asyncio.run(_go())
    if json_out:
        _dump_json(record)
        return

    out = _out()
    run_id = str(record.get("id") or "—")
    status = str(record.get("status") or "—")
    delegation = record.get("delegation") or {}
    chain = " → ".join(delegation.get("chain") or []) or "—"
    out.print(f"run:    [bold]{run_id}[/]")
    out.print(f"flow:   {record.get('flow_id') or flow.id}")
    out.print(f"status: {status}")
    out.print(f"chain:  [dim]{chain}[/]")
    steps = record.get("steps") or []
    if steps:
        out.print(f"steps:  {len(steps)}")
    if status not in _TERMINAL_RUN_STATUSES:
        out.print(f"[dim]Follow it with:[/] lhassa flow watch {run_id}")
    if status == "failed":
        raise typer.Exit(code=1)


def _format_event(event: dict) -> str:
    payload = event.get("payload") or {}
    detail = ""
    if isinstance(payload, dict) and payload:
        bits = [f"{k}={payload[k]}" for k in list(payload)[:3] if payload[k] not in (None, "", {})]
        detail = "  " + " ".join(bits) if bits else ""
    return f"[dim]{event.get('created_at') or ''}[/] [bold]{event.get('kind') or '?'}[/]{detail}"


async def _events_since(repo, instance_id: str, run_id: str, seen: int) -> list[dict]:
    events = await repo.list_events(instance_id, run_id)
    return events[seen:]


def watch_cmd(
    run_id: str = typer.Argument(..., help="Durable flow-run id (from `lhassa flow run`)."),
    interval: float = typer.Option(1.0, "--interval", help="Seconds between polls."),
    timeout: float = typer.Option(
        900.0, "--timeout", help="Give up after this many seconds (the run keeps going)."
    ),
) -> None:
    """Tail a durable run's event stream until it reaches a terminal status.

    Reads the durable kernel directly; no API server required.
    """
    instance_id = _instance_id()
    out, err = _out(), _err()

    async def _go() -> str:
        repo = _repo()
        run = await repo.get_run(instance_id, run_id)
        if run is None:
            err.print(f"No flow run {run_id!r} for instance {instance_id!r}.", style="red")
            raise typer.Exit(code=1)
        seen = 0
        deadline = time.monotonic() + max(timeout, 0.0)
        status = str(run.get("status") or "")
        while True:
            for event in await _events_since(repo, instance_id, run_id, seen):
                out.print(_format_event(event))
                seen += 1
            current = await repo.get_run(instance_id, run_id)
            status = str((current or {}).get("status") or status)
            if status in _TERMINAL_RUN_STATUSES:
                return status
            if time.monotonic() >= deadline:
                out.print(f"[yellow]Still {status} after {timeout:.0f}s; stopped watching.[/]")
                return status
            await asyncio.sleep(max(interval, 0.1))

    status = asyncio.run(_go())
    out.print(f"status: [bold]{status}[/]")
    if status == "failed":
        raise typer.Exit(code=1)


def logs_cmd(
    run_id: str = typer.Argument(..., help="Durable flow-run id."),
    json_out: bool = typer.Option(False, "--json", help="Emit the run + its events as JSON."),
) -> None:
    """Dump a durable run's full record and event history (one shot, no polling)."""
    from lhassa.flows.durable import record_for_run

    instance_id = _instance_id()

    async def _go() -> dict:
        repo = _repo()
        if await repo.get_run(instance_id, run_id) is None:
            _err().print(f"No flow run {run_id!r} for instance {instance_id!r}.", style="red")
            raise typer.Exit(code=1)
        record = await record_for_run(repo, instance_id, run_id)
        events = await repo.list_events(instance_id, run_id)
        return {"run": record, "events": events}

    data = asyncio.run(_go())
    if json_out:
        _dump_json(data)
        return

    out = _out()
    record = data["run"]
    out.print(f"run:    [bold]{record.get('id')}[/]")
    out.print(f"flow:   {record.get('flow_id')}")
    out.print(f"status: {record.get('status')}")
    out.print(f"cost:   ${float(record.get('cost_usd') or 0.0):.4f}")
    steps = record.get("steps") or []
    if steps:
        table = Table(title="Steps")
        for col in ("node", "kind", "status", "cost"):
            table.add_column(col)
        for step in steps:
            table.add_row(
                str(step.get("node") or step.get("node_id") or "—"),
                str(step.get("kind") or "—"),
                str(step.get("status") or step.get("error") or "ok"),
                f"${float(step.get('cost_usd') or 0.0):.4f}",
            )
        out.print(table)
    out.print("events:")
    for event in data["events"]:
        out.print("  " + _format_event(event))


def delete_cmd(
    flow_id: str = typer.Argument(..., help="Flow id to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete a stored flow (asks first unless ``--yes``)."""
    flow = _require_flow(flow_id)
    if not yes and not typer.confirm(f"Delete flow {flow.id!r} ({flow.name!r})?", default=False):
        _out().print("Kept.")
        raise typer.Exit(code=1)
    if not _store().delete(flow_id):
        _err().print(f"No flow {flow_id!r} in the store.", style="red")
        raise typer.Exit(code=1)
    _out().print(f"Deleted flow [bold]{flow_id}[/].")


def runs_cmd(
    flow_id: Optional[str] = typer.Option(None, "--flow", help="Only runs of this flow."),
    status: Optional[str] = typer.Option(None, "--status", help="Only runs in this status."),
    limit: int = typer.Option(20, "--limit", help="How many runs to show."),
    json_out: bool = typer.Option(False, "--json", help="Emit the runs as JSON."),
) -> None:
    """List recent durable flow runs (the CLI half of ``GET /api/flow-runs``)."""
    instance_id = _instance_id()

    async def _go() -> list[dict]:
        return await _repo().list_runs(
            instance_id, statuses=[status] if status else None, limit=max(limit, 1)
        )

    rows = asyncio.run(_go())
    if flow_id:
        rows = [r for r in rows if str(r.get("flow_id")) == flow_id]
    if json_out:
        _dump_json(
            [
                {
                    "id": r.get("run_id"),
                    "flow_id": r.get("flow_id"),
                    "status": r.get("status"),
                    "created_at": r.get("created_at"),
                    "updated_at": r.get("updated_at"),
                }
                for r in rows
            ]
        )
        return
    table = Table(title="Flow runs")
    for col in ("run", "flow", "status", "created"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r.get("run_id") or "—"),
            str(r.get("flow_id") or "—"),
            str(r.get("status") or "—"),
            str(r.get("created_at") or "—"),
        )
    _out().print(table)


# ── registration ──────────────────────────────────────────────────────────────────

flow_app.command("list")(list_cmd)
flow_app.command("templates")(templates_cmd)
flow_app.command("apply")(apply_cmd)
flow_app.command("show")(show_cmd)

# `lhassa flow …`: the full lifecycle. `list`/`show`/`templates` are shared with the
# plural sub-app so neither spelling is a dead end.
one_flow_app.command("list")(list_cmd)
one_flow_app.command("show")(show_cmd)
one_flow_app.command("templates")(templates_cmd)
one_flow_app.command("create")(create_cmd)
one_flow_app.command("edit")(edit_cmd)
one_flow_app.command("validate")(validate_cmd)
one_flow_app.command("run")(run_cmd)
one_flow_app.command("watch")(watch_cmd)
one_flow_app.command("logs")(logs_cmd)
one_flow_app.command("runs")(runs_cmd)
one_flow_app.command("delete")(delete_cmd)
