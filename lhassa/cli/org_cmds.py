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


# ── parsing helpers (skills / limits from the CLI) ─────────────────────────────────


def _parse_skills(raw: str | None) -> tuple[str, ...]:
    """Parse a ``s1,s2`` skills option into an ordered tuple (empty when blank)."""
    if not raw:
        return ()
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def _coerce_scalar(value: str):
    """Coerce a ``k=v`` scalar to int/float/bool where it looks like one."""
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_limits(raw: str | None) -> dict:
    """Parse a ``--limits`` option: a JSON object, or ``k=v,k2=v2`` pairs.

    JSON is tried first (so nested values like ``allowed_actions`` work); otherwise
    a simple comma-separated ``k=v`` list is parsed with scalar coercion. A blank
    value is an empty mapping.
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        raise typer.BadParameter("--limits JSON must be an object")
    except json.JSONDecodeError:
        pass
    out: dict = {}
    for part in raw.split(","):
        if "=" not in part:
            raise typer.BadParameter(f"--limits pair {part!r} is not k=v or JSON")
        key, val = part.split("=", 1)
        out[key.strip()] = _coerce_scalar(val.strip())
    return out


# ── tree ──────────────────────────────────────────────────────────────────────────


def _node_label(node, *, is_admin: bool = False) -> str:
    bits = ["★" if is_admin else None, node.id, f"[{node.role}]"]
    bits = [b for b in bits if b]
    if is_admin:
        bits.append("admin")
    if node.department:
        bits.append(f"dept={node.department}")
    if node.skills:
        bits.append("skills=" + ",".join(node.skills))
    if node.limits:
        bits.append("limits=" + json.dumps(node.limits, ensure_ascii=False))
    return " ".join(bits)


def tree_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the chart as JSON."),
) -> None:
    """Print the org hierarchy as a tree (root → … → leaves).

    The permanent administrator (Lhassa) is the root and is marked ``★ … admin``.
    """
    topology = _load()
    if json_out:
        _dump_json(topology.to_list())
        return

    root = topology.root()
    rich_tree = Tree(_node_label(root, is_admin=topology.is_admin(root.id)))

    def _attach(parent_node, parent_widget):
        for child in topology.children_of(parent_node.id):
            widget = parent_widget.add(
                _node_label(child, is_admin=topology.is_admin(child.id))
            )
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

    admin = topology.root().id  # the permanent administrator is pinned to the root
    table = Table(title="Org brains")
    for col in ("id", "role", "department", "parent", "skills", "limits"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            ("★ " if r["id"] == admin else "") + str(r["id"]),
            str(r["role"]),
            str(r["department"] or "—"),
            str(r["parent"] or "—  (root)"),
            ", ".join(r.get("skills") or []) or "—",
            json.dumps(r.get("limits") or {}, ensure_ascii=False)
            if r.get("limits")
            else "—",
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
    skills: str = typer.Option(
        None, "--skills", help="Skills this brain handles, e.g. 'optimization,finance'."
    ),
    limits: str = typer.Option(
        None,
        "--limits",
        help="Limitations as JSON ('{\"budget_usd\":1.0}') or k=v pairs.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the new node as JSON."),
) -> None:
    """Create a brain and persist it to the topology file."""
    from lhassa.org import management, topology as topo

    config = _config()
    try:
        updated = management.add_brain(
            node_id,
            role=role,
            parent=parent,
            department=department,
            skills=_parse_skills(skills),
            limits=_parse_limits(limits),
            config=config,
        )
    except (topo.TopologyError, ValueError) as exc:
        _err().print(f"Cannot add brain: {exc}", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(updated.node(node_id).to_dict())
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
    from lhassa.org import management, topology as topo

    config = _config()
    before = {n.id for n in topo.load_topology(config).nodes()}
    try:
        updated = management.remove_brain(node_id, force=force, config=config)
    except (topo.TopologyError, ValueError) as exc:
        _err().print(f"Cannot remove brain: {exc}", style="red")
        raise typer.Exit(code=1)
    removed = sorted(before - {n.id for n in updated.nodes()})
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


# ── set-skills / set-limits (define what a brain handles + its limitations) ────────


def set_skills_cmd(
    node_id: str = typer.Argument(..., help="Brain id to assign skills to."),
    skills: str = typer.Option(
        ..., "--skills", help="Skills, e.g. 'optimization,finance' ('' clears them)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the node as JSON."),
) -> None:
    """Define the SKILLS a brain handles (replaces the set)."""
    from lhassa.org import management, topology as topo

    config = _config()
    try:
        updated = management.set_skills(node_id, _parse_skills(skills), config=config)
    except (topo.TopologyError, ValueError, KeyError) as exc:
        _err().print(f"Cannot set skills: {exc}", style="red")
        raise typer.Exit(code=1)
    node = updated.node(node_id)
    if json_out:
        _dump_json(node.to_dict())
    else:
        _out().print(f"{node_id} skills = {', '.join(node.skills) or '—'}")


def set_limits_cmd(
    node_id: str = typer.Argument(..., help="Brain id to set limits on."),
    limits: str = typer.Option(
        ..., "--limits", help="Limits as JSON ('{\"budget_usd\":1.0}') or k=v pairs."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the node as JSON."),
) -> None:
    """Set a brain's LIMITATIONS (replaces the mapping)."""
    from lhassa.org import management, topology as topo

    config = _config()
    try:
        updated = management.set_limits(node_id, _parse_limits(limits), config=config)
    except (topo.TopologyError, ValueError, KeyError) as exc:
        _err().print(f"Cannot set limits: {exc}", style="red")
        raise typer.Exit(code=1)
    node = updated.node(node_id)
    if json_out:
        _dump_json(node.to_dict())
    else:
        _out().print(f"{node_id} limits = {json.dumps(node.limits, ensure_ascii=False)}")


# ── move (re-parent) / promote (make root) ─────────────────────────────────────────


def move_cmd(
    node_id: str = typer.Argument(..., help="Brain id to re-parent."),
    parent: str = typer.Option(..., "--parent", help="The new parent brain id."),
    json_out: bool = typer.Option(False, "--json", help="Emit the chart as JSON."),
) -> None:
    """Re-parent a brain — change WHO COMMANDS WHOM.

    The permanent administrator (Lhassa) cannot be moved/demoted — that is rejected.
    """
    from lhassa.org import management, topology as topo

    config = _config()
    try:
        updated = management.set_parent(node_id, parent, config=config)
    except (topo.TopologyError, ValueError, KeyError) as exc:
        _err().print(f"Cannot move brain: {exc}", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(updated.to_list())
    else:
        _out().print(f"Moved {node_id!r} under {parent!r}.")


def promote_cmd(
    node_id: str = typer.Argument(..., help="Brain id to promote."),
    json_out: bool = typer.Option(False, "--json", help="Emit the chart as JSON."),
) -> None:
    """Promote a brain to a TOP-LEVEL coordinator under the administrator.

    Lhassa stays the permanent absolute root; the promoted brain becomes a direct
    child of the administrator (it commands everything in its own subtree), never
    unseating Lhassa.
    """
    from lhassa.org import management, topology as topo

    config = _config()
    try:
        updated = management.set_root(node_id, config=config)
    except (topo.TopologyError, ValueError, KeyError) as exc:
        _err().print(f"Cannot promote brain: {exc}", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(updated.to_list())
    else:
        _out().print(
            f"Promoted {node_id!r} to a top-level coordinator under "
            f"{updated.admin().id!r} (Lhassa stays the permanent root)."
        )


# ── show (one brain) ───────────────────────────────────────────────────────────────


def show_cmd(
    node_id: str = typer.Argument(..., help="Brain id to inspect."),
    json_out: bool = typer.Option(False, "--json", help="Emit the brain as JSON."),
) -> None:
    """Show one brain's role/parent/department/skills/limits."""
    topology = _load()
    if not topology.has(node_id):
        _err().print(f"No brain {node_id!r} in the topology.", style="red")
        raise typer.Exit(code=1)
    node = topology.node(node_id)
    payload = node.to_dict()
    payload["children"] = [c.id for c in topology.children_of(node_id)]
    payload["is_leaf"] = topology.is_leaf(node_id)
    payload["is_admin"] = topology.is_admin(node_id)
    if json_out:
        _dump_json(payload)
        return
    out = _out()
    out.print(f"id:         {node.id}")
    out.print(f"role:       {node.role}")
    out.print(f"parent:     {node.parent or '— (root)'}")
    out.print(f"department: {node.department or '—'}")
    out.print(f"skills:     {', '.join(node.skills) or '—'}")
    out.print(f"limits:     {json.dumps(node.limits, ensure_ascii=False)}")
    out.print(f"children:   {', '.join(payload['children']) or '— (leaf)'}")
    out.print(f"admin:      {'★ yes (permanent root)' if payload['is_admin'] else 'no'}")


# ── skills (what CAN be assigned) ──────────────────────────────────────────────────


def skills_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the skills as JSON."),
) -> None:
    """List the skills that CAN be assigned (the specialist registry's domains)."""
    from lhassa.org import management

    skills = management.available_skills()
    if json_out:
        _dump_json(skills)
        return
    from rich.table import Table

    table = Table(title="Available skills (specialist domains)")
    for col in ("domain", "name", "metric", "tools"):
        table.add_column(col)
    for s in skills:
        table.add_row(
            str(s["domain"]),
            str(s["name"]),
            str(s.get("metric") or "—"),
            ", ".join(s.get("tools") or []) or "—",
        )
    _out().print(table)


# ── templates (use-case org presets) ───────────────────────────────────────────────


def templates_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the catalog as JSON."),
) -> None:
    """List the use-case org templates (Code Review, Software Factory, …)."""
    from lhassa.org import templates

    catalog = templates.list_templates()
    if json_out:
        _dump_json(catalog)
        return
    from rich.table import Table

    table = Table(title="Org templates")
    for col in ("id", "name", "category", "brains", "description"):
        table.add_column(col)
    for t in catalog:
        table.add_row(
            str(t["id"]),
            str(t["name"]),
            str(t["category"]),
            str(t["brain_count"]),
            str(t["description"]),
        )
    _out().print(table)


def apply_cmd(
    template_id: str = typer.Argument(..., help="Template id (see `lhassa org templates`)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the updated chart as JSON."),
) -> None:
    """Apply a template — create its brains under Lhassa (idempotent)."""
    from lhassa.org import templates

    config = _config()
    try:
        result = templates.instantiate(template_id, config=config)
    except ValueError as exc:
        _err().print(f"Cannot apply template: {exc}", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(result)
        return
    out = _out()
    out.print(
        f"Applied {template_id!r}: created {', '.join(result['created']) or '—'}"
        + (f"; skipped {', '.join(result['skipped'])}" if result["skipped"] else "")
    )


# ── registration ──────────────────────────────────────────────────────────────────

org_app.command("tree")(tree_cmd)
org_app.command("list")(list_cmd)
org_app.command("add")(add_cmd)
org_app.command("remove")(remove_cmd)
org_app.command("route")(route_cmd)
org_app.command("run")(run_cmd)
org_app.command("set-skills")(set_skills_cmd)
org_app.command("set-limits")(set_limits_cmd)
org_app.command("move")(move_cmd)
org_app.command("promote")(promote_cmd)
org_app.command("show")(show_cmd)
org_app.command("skills")(skills_cmd)
org_app.command("templates")(templates_cmd)
org_app.command("apply")(apply_cmd)
