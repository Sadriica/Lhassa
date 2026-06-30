"""``lhassa org`` — manage the org TOPOLOGY (1..N brains, any hierarchy).

A small, OFFLINE management surface over the topology file
(``config.org_topology_file``): inspect the chart, add/remove brains, preview where
a goal would route, and run a goal end-to-end. No LLM and no network are touched by
the management commands; ``org run`` only thinks for real when ``LHASSA_ORG_LIVE=1``
(otherwise the department executor returns its deterministic offline result).

It is registered onto the main Typer ``app`` as a sub-app — see ``lhassa.cli.app``
(``app.add_typer(org_cmds.org_app, name="org")``) — alongside, and without
disturbing, the default chat/REPL and the existing power subcommands.
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console
from rich.tree import Tree

org_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage the org topology: 1..N brains and their hierarchy.",
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


def _load():
    from lhassa.org import topology as topo

    return topo.load_topology(_config())


# ── tree ──────────────────────────────────────────────────────────────────────────


def _node_label(node) -> str:
    bits = [node.id, f"[{node.role}]"]
    if node.department:
        bits.append(f"dept={node.department}")
    return " ".join(bits)


def tree_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the chart as JSON."),
) -> None:
    """Print the org hierarchy as a tree (root → … → leaves)."""
    topology = _load()
    if json_out:
        _dump_json(topology.to_list())
        return

    root = topology.root()
    rich_tree = Tree(_node_label(root))

    def _attach(parent_node, parent_widget):
        for child in topology.children_of(parent_node.id):
            widget = parent_widget.add(_node_label(child))
            _attach(child, widget)

    _attach(root, rich_tree)
    _out().print(rich_tree)


# ── list ──────────────────────────────────────────────────────────────────────────


def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the nodes as JSON."),
) -> None:
    """List every brain (node) in the topology."""
    topology = _load()
    rows = topology.to_list()
    if json_out:
        _dump_json(rows)
        return
    from rich.table import Table

    table = Table(title="Org brains")
    for col in ("id", "role", "department", "parent"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]),
            str(r["role"]),
            str(r["department"] or "—"),
            str(r["parent"] or "—  (root)"),
        )
    _out().print(table)


# ── add ───────────────────────────────────────────────────────────────────────────


def add_cmd(
    node_id: str = typer.Option(..., "--id", help="Logical brain id (unique)."),
    role: str = typer.Option("department", "--role", help="Role label (open)."),
    parent: str = typer.Option(
        None, "--parent", help="Parent brain id (omit only for the root)."
    ),
    department: str = typer.Option(
        None, "--department", help="Department name/identity this brain owns."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the new node as JSON."),
) -> None:
    """Create a brain and persist it to the topology file."""
    from lhassa.org import topology as topo

    config = _config()
    current = topo.load_topology(config)
    new_node = topo.BrainNode(
        id=node_id, role=role, department=department, parent=parent
    )
    try:
        updated = topo.Topology([*current.nodes(), new_node])
    except topo.TopologyError as exc:
        _err().print(f"Cannot add brain: {exc}", style="red")
        raise typer.Exit(code=1)
    topo.save_topology(updated, config)
    if json_out:
        _dump_json(new_node.to_dict())
    else:
        _out().print(f"Added brain {node_id!r} (role={role}, parent={parent or '—'}).")


# ── remove ────────────────────────────────────────────────────────────────────────


def remove_cmd(
    node_id: str = typer.Option(..., "--id", help="Brain id to remove."),
    force: bool = typer.Option(
        False, "--force", help="Remove even if it orphans children / is the root."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Remove a brain (refuses to orphan children or drop a populated root)."""
    from lhassa.org import topology as topo

    config = _config()
    current = topo.load_topology(config)
    if not current.has(node_id):
        _err().print(f"No brain {node_id!r} in the topology.", style="red")
        raise typer.Exit(code=1)

    children = current.children_of(node_id)
    if children and not force:
        kids = ", ".join(c.id for c in children)
        _err().print(
            f"Brain {node_id!r} has children ({kids}); refusing to orphan them. "
            "Re-run with --force to remove the whole subtree.",
            style="red",
        )
        raise typer.Exit(code=1)

    # Drop the node and (with --force) its whole subtree.
    drop = {node_id} | {d.id for d in current.descendants(node_id)}
    remaining = [n for n in current.nodes() if n.id not in drop]
    try:
        updated = topo.Topology(remaining)
    except topo.TopologyError as exc:
        _err().print(f"Cannot remove brain: {exc}", style="red")
        raise typer.Exit(code=1)
    topo.save_topology(updated, config)
    removed = sorted(drop)
    if json_out:
        _dump_json({"removed": removed})
    else:
        _out().print(f"Removed {', '.join(removed)}.")


# ── route (preview) ───────────────────────────────────────────────────────────────


def _route_path(topology, goal: str) -> list[str]:
    """The root→leaf id path a goal would take through the tree (deterministic)."""
    from lhassa.org.coordinator import route_in_topology

    node = topology.root()
    path = [node.id]
    while not topology.is_leaf(node.id):
        child_id = route_in_topology(topology, node.id, goal)
        path.append(child_id)
        node = topology.node(child_id)
    return path


def route_cmd(
    goal: str = typer.Argument(..., help="The goal to route through the tree."),
    json_out: bool = typer.Option(False, "--json", help="Emit the path as JSON."),
) -> None:
    """Show WHERE a goal would be routed (root → … → leaf), no execution."""
    topology = _load()
    path = _route_path(topology, goal)
    leaf = topology.node(path[-1])
    payload = {
        "goal": goal,
        "path": path,
        "leaf": leaf.id,
        "department": leaf.department or leaf.id,
    }
    if json_out:
        _dump_json(payload)
    else:
        _out().print(" → ".join(path))
        _out().print(f"leaf: {leaf.id} (department={leaf.department or leaf.id})", style="dim")


# ── run ───────────────────────────────────────────────────────────────────────────


def run_cmd(
    goal: str = typer.Argument(..., help="The goal to run end-to-end."),
    by_actor: str = typer.Option("owner", "--by", help="Who proposes the goal."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Run a goal end-to-end through the topology (offline unless LHASSA_ORG_LIVE=1)."""
    from lhassa.org.coordinator import run_goal

    config = _config()
    result = asyncio.run(run_goal(goal, by_actor=by_actor, config=config))
    if json_out:
        _dump_json(result)
        return
    out = _out()
    out.print(" → ".join(result["path"]))
    summary = (result.get("result") or {}).get("summary")
    if summary:
        out.print(str(summary))
    out.print(
        f"leaf: {result['leaf']} (department={result['department']})", style="dim"
    )


# ── registration ──────────────────────────────────────────────────────────────────

org_app.command("tree")(tree_cmd)
org_app.command("list")(list_cmd)
org_app.command("add")(add_cmd)
org_app.command("remove")(remove_cmd)
org_app.command("route")(route_cmd)
org_app.command("run")(run_cmd)
