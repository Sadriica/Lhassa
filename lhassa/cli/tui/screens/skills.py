# SPDX-License-Identifier: AGPL-3.0-or-later
"""Skills screen — the SKILL.md store, plus which brains carry each skill.

Reads :class:`~lhassa.skills.store.SkillStore` (the filesystem layer of the
3-layer skill) and cross-references the org topology's skill sets, so the screen
shows both the capability and the brains that declare the intent. Read-only and
offline; no API server, no model.
"""

from __future__ import annotations

from lhassa.cli.tui.screens.base import EMPTY, Row, TableScreen


def _load_skills() -> list:
    from lhassa.skills.store import SkillStore

    try:
        return SkillStore().list_skills()
    except Exception:
        return []


def _brains_by_skill() -> dict[str, list[str]]:
    """``skill name → brain ids`` from the topology; ``{}`` if it won't load."""
    from lhassa.org import topology as topo_mod

    out: dict[str, list[str]] = {}
    try:
        topology = topo_mod.load_topology()
    except Exception:
        return out
    for node in topology.nodes():
        for skill in node.skills or ():
            out.setdefault(str(skill), []).append(node.id)
    return out


class SkillsScreen(TableScreen):
    """Every stored skill: name, description, domain, and its carrier brains."""

    COLUMNS = ("name", "description", "domain", "brains")
    TABLE_ID = "skills-table"
    FILTER_PLACEHOLDER = "filter skills…"

    async def load_rows(self) -> list[Row]:
        carriers = _brains_by_skill()
        out: list[Row] = []
        for skill in _load_skills():
            metadata = getattr(skill, "metadata", None) or {}
            domain = metadata.get("domain") or metadata.get("allowed-tools") or EMPTY
            brains = carriers.get(skill.name) or []
            out.append(
                (
                    skill.name,
                    (
                        skill.name,
                        (skill.description or EMPTY).splitlines()[0][:70],
                        str(domain)[:30],
                        ", ".join(brains) or EMPTY,
                    ),
                )
            )
        return out
