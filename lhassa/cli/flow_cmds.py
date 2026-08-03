# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa flows`` — inspect stored work-flows and apply the flow-templates gallery.

A small, mostly-OFFLINE management surface over the flows file
(``config.flows_file``) and the flow-templates catalog (``lhassa.flows.templates``).
No LLM and no network are touched by any of these commands — instantiating a
template just writes a validated :class:`~lhassa.flows.model.Flow` to the store.

Registered onto the main Typer ``app`` as a sub-app — see ``lhassa.cli.app``
(``app.add_typer(flow_cmds.flow_app, name="flows")``), the same pattern as
``lhassa org``.
"""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console
from rich.table import Table

flow_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Inspect stored work-flows and apply the flow-templates gallery.",
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
        out.print(f"  - {n.id} [{n.kind.value}] {n.label or ''}".rstrip())
    out.print("edges:")
    for e in flow.edges:
        when = f" when={json.dumps(e.when, ensure_ascii=False)}" if e.when else ""
        out.print(f"  - {e.from_node} -> {e.to_node}{when}")


# ── registration ──────────────────────────────────────────────────────────────────

flow_app.command("list")(list_cmd)
flow_app.command("templates")(templates_cmd)
flow_app.command("apply")(apply_cmd)
flow_app.command("show")(show_cmd)
