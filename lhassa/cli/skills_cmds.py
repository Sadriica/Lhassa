# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa skills``: the admin surface for first-class SKILLS.

A small, mostly-OFFLINE management sub-app over the skill store
(``config.skills_dir``): create skills (blank template or drafted WITH AI),
improve a skill with AI, see which brains have a skill, get recommended skills,
assign a skill to a brain, and import/export skill folders. Every command takes
``--json`` for the dashboard/automation.

Only the AI commands (``create --ai`` / ``improve --ai``) touch a model, and they
DEGRADE with a clear message when no provider/creds are available (they never
crash the CLI). Everything else; list/show/recommend/assign/who-has/import/
export/delete/seed; is deterministic and offline.

The integrator registers this onto the main Typer ``app`` as a sub-app
(``app.add_typer(skills_cmds.skills_app, name="skills")``).
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace

import typer
from rich.console import Console

skills_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage first-class skills: create (with AI), assign, recommend, import/export.",
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
    from lhassa.skills.store import SkillStore

    return SkillStore(_config())


def _skill_payload(skill) -> dict:
    from lhassa.skills.skillmd import skill_to_dict

    payload = skill_to_dict(skill)
    payload["error"] = skill.error
    return payload


# ── list ────────────────────────────────────────────────────────────────────────


def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the skills as JSON."),
) -> None:
    """List every skill in the store."""
    skills = _store().list_skills()
    if json_out:
        _dump_json([_skill_payload(s) for s in skills])
        return
    from rich.table import Table

    table = Table(title="Skills")
    for col in ("name", "description", "domain", "tags", "resources"):
        table.add_column(col)
    for s in skills:
        meta = s.metadata or {}
        table.add_row(
            s.name or "—",
            (s.description or "—"),
            str(meta.get("domain") or "—"),
            ", ".join(meta.get("tags") or []) or "—",
            ", ".join(s.resources or []) or "—",
        )
    _out().print(table)


# ── show ────────────────────────────────────────────────────────────────────────


def show_cmd(
    name: str = typer.Argument(..., help="Skill name to inspect."),
    json_out: bool = typer.Option(False, "--json", help="Emit the skill as JSON."),
) -> None:
    """Show one skill (frontmatter + instructions + resources)."""
    skill = _store().get(name)
    if skill is None:
        _err().print(f"No skill named {name!r}.", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(_skill_payload(skill))
        return
    from lhassa.skills.skillmd import to_skill_md

    out = _out()
    out.print(to_skill_md(skill))
    if skill.resources:
        out.print(f"resources: {', '.join(skill.resources)}", style="dim")
    if skill.error:
        out.print(f"note: {skill.error}", style="yellow")


# ── create ──────────────────────────────────────────────────────────────────────


def create_cmd(
    name: str = typer.Argument(..., help="Skill name (kebab-slug)."),
    from_brief: str = typer.Option(
        None, "--from-brief", help="A short brief: the description / AI prompt."
    ),
    ai: bool = typer.Option(
        False, "--ai", help="Draft the skill WITH the model (degrades if unavailable)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the new skill as JSON."),
) -> None:
    """Create a skill; a blank template, or drafted WITH AI from a brief."""
    from lhassa.skills import ai as ai_mod
    from lhassa.skills.skillmd import template_skill

    config = _config()
    store = _store()
    if ai:
        brief = from_brief or name
        skill = asyncio.run(ai_mod.create_skill_with_ai(brief, config=config))
        # Honour the requested name regardless of what the model named it.
        skill = replace(skill, name=name)
    else:
        skill = template_skill(name, from_brief or "")

    try:
        folder = store.create(skill)
    except FileExistsError as exc:
        _err().print(f"Cannot create skill: {exc}", style="red")
        raise typer.Exit(code=1)
    except ValueError as exc:
        _err().print(f"Cannot create skill: {exc}", style="red")
        raise typer.Exit(code=1)

    if json_out:
        _dump_json({"created": skill.name, "path": str(folder), "error": skill.error})
        return
    out = _out()
    out.print(f"Created skill {skill.name!r} at {folder}.")
    if skill.error:
        out.print(f"AI note: {skill.error} (saved a template instead).", style="yellow")


# ── improve ─────────────────────────────────────────────────────────────────────


def improve_cmd(
    name: str = typer.Argument(..., help="Skill name to improve."),
    ai: bool = typer.Option(
        True, "--ai/--no-ai", help="Use the model to improve the skill."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the skill as JSON."),
) -> None:
    """Improve a skill WITH AI (clearer instructions + description), then save it."""
    from lhassa.skills import ai as ai_mod

    config = _config()
    store = _store()
    if store.get(name) is None:
        _err().print(f"No skill named {name!r}.", style="red")
        raise typer.Exit(code=1)
    if not ai:
        _err().print("Nothing to do: improve needs --ai.", style="yellow")
        raise typer.Exit(code=1)

    improved = asyncio.run(ai_mod.improve_skill_with_ai(name, config=config))
    degraded = bool(improved.error)
    if not degraded:
        store.save(improved)

    if json_out:
        _dump_json(
            {"improved": improved.name, "degraded": degraded, "error": improved.error}
        )
        return
    out = _out()
    if degraded:
        out.print(f"AI unavailable: {improved.error}. Skill left unchanged.", style="yellow")
    else:
        out.print(f"Improved skill {improved.name!r} and saved it.")


# ── delete ──────────────────────────────────────────────────────────────────────


def delete_cmd(
    name: str = typer.Argument(..., help="Skill name to delete."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Delete a skill from the store."""
    removed = _store().delete(name)
    if not removed:
        _err().print(f"No skill named {name!r}.", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json({"deleted": name})
    else:
        _out().print(f"Deleted skill {name!r}.")


# ── assign ──────────────────────────────────────────────────────────────────────


def assign_cmd(
    skill: str = typer.Argument(..., help="Skill name to assign."),
    brain: str = typer.Argument(..., help="Brain id to assign it to."),
    json_out: bool = typer.Option(False, "--json", help="Emit the brain as JSON."),
) -> None:
    """Assign a skill to a brain (adds it to the brain's skill set)."""
    from lhassa.skills.store import assign_to_brain

    config = _config()
    try:
        updated = assign_to_brain(skill, brain, config=config)
    except (KeyError, ValueError) as exc:
        _err().print(f"Cannot assign skill: {exc}", style="red")
        raise typer.Exit(code=1)
    node = updated.node(brain)
    if json_out:
        _dump_json(node.to_dict())
    else:
        _out().print(f"{brain} skills = {', '.join(node.skills) or '—'}")


# ── who-has ─────────────────────────────────────────────────────────────────────


def who_has_cmd(
    skill: str = typer.Argument(..., help="Skill name to look up."),
    json_out: bool = typer.Option(False, "--json", help="Emit the brain ids as JSON."),
) -> None:
    """Show which brains HAVE a skill (their topology skill set contains it)."""
    from lhassa.skills.store import brains_with_skill

    brains = brains_with_skill(skill, config=_config())
    if json_out:
        _dump_json({"skill": skill, "brains": brains})
        return
    out = _out()
    if brains:
        out.print(f"{skill}: {', '.join(brains)}")
    else:
        out.print(f"{skill}: no brain has this skill yet.", style="dim")


# ── recommend ───────────────────────────────────────────────────────────────────


def recommend_cmd(
    context: str = typer.Argument(
        None, help="Optional context to rank recommendations by."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the recommendations as JSON."),
) -> None:
    """Recommend skills (catalog entries not yet installed). Offline, no model."""
    from lhassa.skills import ai as ai_mod

    recs = ai_mod.recommend_skills(context, config=_config())
    if json_out:
        _dump_json(recs)
        return
    from rich.table import Table

    table = Table(title="Recommended skills")
    for col in ("name", "description", "domain", "reason"):
        table.add_column(col)
    for r in recs:
        table.add_row(
            str(r["name"]), str(r["description"]), str(r["domain"] or "—"), str(r["reason"])
        )
    _out().print(table)


# ── import ──────────────────────────────────────────────────────────────────────


def import_cmd(
    path: str = typer.Argument(..., help="A skill folder, SKILL.md, or skill.json."),
    json_out: bool = typer.Option(False, "--json", help="Emit the imported skill as JSON."),
) -> None:
    """Import an external skill into the store."""
    skill = _store().import_path(path)
    if skill.error:
        _err().print(f"Cannot import skill: {skill.error}", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json(_skill_payload(skill))
    else:
        _out().print(f"Imported skill {skill.name!r}.")


# ── export ──────────────────────────────────────────────────────────────────────


def export_cmd(
    name: str = typer.Argument(..., help="Skill name to export."),
    to: str = typer.Option(
        None, "--to", help="Destination dir (a copy is made). Omit to print the stored path."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the path as JSON."),
) -> None:
    """Export a skill folder (copy to --to, or report the stored path)."""
    try:
        path = _store().export(name, dest_dir=to)
    except KeyError as exc:
        _err().print(f"Cannot export skill: {exc}", style="red")
        raise typer.Exit(code=1)
    if json_out:
        _dump_json({"name": name, "path": str(path)})
    else:
        _out().print(str(path))


# ── seed ────────────────────────────────────────────────────────────────────────


def seed_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the seeded names as JSON."),
) -> None:
    """Seed the starter skills into the store (idempotent)."""
    from lhassa.skills import ai as ai_mod

    seeded = ai_mod.ensure_seed(_config())
    if json_out:
        _dump_json({"seeded": seeded})
    else:
        _out().print(f"Seeded: {', '.join(seeded) or '— (already present)'}")


# ── registration ────────────────────────────────────────────────────────────────

skills_app.command("list")(list_cmd)
skills_app.command("show")(show_cmd)
skills_app.command("create")(create_cmd)
skills_app.command("improve")(improve_cmd)
skills_app.command("delete")(delete_cmd)
skills_app.command("assign")(assign_cmd)
skills_app.command("who-has")(who_has_cmd)
skills_app.command("recommend")(recommend_cmd)
skills_app.command("import")(import_cmd)
skills_app.command("export")(export_cmd)
skills_app.command("seed")(seed_cmd)
