# SPDX-License-Identifier: AGPL-3.0-or-later
"""What's wired and installed — /capabilities /tools /skills.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

from loguru import logger

from ._registry import register_command
from ._shared import _config, _oops


def _flag(value: bool) -> str:
    return "on" if value else "off"


@register_command("capabilities")
async def _cmd_capabilities(args: str, *, config=None) -> str:
    """``/capabilities`` — a compact, LLM-free wired/on-off summary.

    A phone-sized subset of ``lhassa capabilities`` (:mod:`lhassa.cli.capabilities`):
    providers wired, channels active, org shape, and the monitor/voice/org-live
    toggles. Reads config + registries only — never constructs a provider.
    """
    try:
        cfg = _config(config)
        lines = ["Capabilities:"]

        try:
            from lhassa.llm.provider import PROVIDERS

            lines.append(f"Providers: {', '.join(PROVIDERS.names()) or '(none)'}")
        except Exception:
            pass

        try:
            from lhassa.notify.channels import active_channels

            active = [c.name for c in active_channels(cfg)]
            lines.append(f"Channels active: {', '.join(active) or '(none)'}")
        except Exception:
            pass

        try:
            from lhassa.org.topology import load_topology

            topology = load_topology(cfg)
            shape = (
                "standalone (1 brain)"
                if topology.is_standalone()
                else f"{len(topology.nodes())}-brain org"
            )
            lines.append(f"Org: {shape}")
        except Exception:
            lines.append("Org: unavailable")

        try:
            from lhassa.org.coordinator import is_live

            org_live = is_live()
        except Exception:
            org_live = False
        monitor_on = bool(getattr(cfg, "monitor_enabled", False))
        voice_on = bool(getattr(cfg, "voice_enabled", False))
        lines.append(
            f"Org live: {_flag(org_live)} · Monitor: {_flag(monitor_on)} · "
            f"Voice: {_flag(voice_on)}"
        )
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /capabilities failed: {exc}")
        return _oops("read my capabilities")


@register_command("tools")
async def _cmd_tools(args: str, *, config=None) -> str:
    """``/tools`` — the self-written tools and their gate/usage state, LLM-free.

    Reads the same ``tools`` table :func:`lhassa.cli.commands.tools_cmd` renders
    (:func:`lhassa.core.db.list_tools`): name, dominio, estado, and usos/fallos.
    """
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db

        await _db.init_db()
        rows = await _db.list_tools(instance_id)
        if not rows:
            return "No self-written tools yet."
        lines = [f"Tools ({len(rows)}):"]
        for r in rows[:10]:
            name = r.get("nombre") or "?"
            estado = r.get("estado") or "—"
            dominio = r.get("dominio") or "—"
            usos = r.get("usos") or 0
            fallos = r.get("fallos") or 0
            lines.append(f"- {name} [{dominio}] {estado} · {usos} uses, {fallos} fails")
        if len(rows) > 10:
            lines.append(f"…+{len(rows) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /tools failed: {exc}")
        return _oops("list my tools")


@register_command("skills")
async def _cmd_skills(args: str, *, config=None) -> str:
    """``/skills`` — installed skills, LLM-free.

    Reads :class:`lhassa.skills.store.SkillStore` (list) and, where cheap,
    :func:`lhassa.skills.curator.get_usage` for the pinned/state flag per skill.
    """
    try:
        cfg = _config(config)
        from lhassa.skills.store import SkillStore

        skills = SkillStore(cfg).list_skills()
        if not skills:
            return "No skills installed."

        try:
            from lhassa.skills import curator

            usage = curator.get_usage(cfg)
        except Exception:
            usage = {}

        lines = [f"Skills ({len(skills)}):"]
        for s in skills[:10]:
            name = s.name or "?"
            desc = (s.description or "").strip()
            if len(desc) > 60:
                desc = desc[:57] + "…"
            entry = usage.get(name) or {}
            pinned = " (pinned)" if entry.get("pinned") else ""
            state = entry.get("state")
            state_str = f" [{state}]" if state and state != "active" else ""
            lines.append(f"- {name}{pinned}{state_str}{f' — {desc}' if desc else ''}")
        if len(skills) > 10:
            lines.append(f"…+{len(skills) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /skills failed: {exc}")
        return _oops("list my skills")
