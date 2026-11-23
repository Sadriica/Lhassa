# SPDX-License-Identifier: AGPL-3.0-or-later
"""Memory + personal-agenda commands; /recall /memory /knowledge /checkin
/today /tomorrow.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

from loguru import logger

from ._registry import register_command
from ._shared import _approval_age, _config, _oops


@register_command("recall")
async def _cmd_recall(args: str, *, config=None) -> str:
    """``/recall <text>``: full-text search over the episode log, LLM-free.

    Shows up to 5 matches: relative age, ``despertar_por``, ``decision`` and a
    short ``resultado`` snippet. $0; a deterministic FTS5 query, never a model
    call. No args → a usage hint.
    """
    text = (args or "").strip()
    if not text:
        return "Usage: /recall <text>; searches past episodes for a match."
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db

        await _db.init_db()
        rows = await _db.search_episodes(instance_id, text, 5)
        if not rows:
            # "Nothing matches" and "the index is broken" must not read the
            # same; search_episodes degrades to [] on any failure, which is
            # right for its machine callers but dishonest at the owner's door.
            if not await _db.recall_available(instance_id):
                return (
                    "I can't search my memory right now; the recall index is "
                    "unavailable on this machine."
                )
            return f"No episodes match {text!r}."

        lines = [f"Recall for {text!r} ({len(rows)}):"]
        for i, row in enumerate(rows, start=1):
            age = _approval_age(row.get("creado_en"))
            despertar = row.get("despertar_por") or "?"
            decision = row.get("decision") or "?"
            snippet = (row.get("resultado") or "").replace("\n", " ").strip()
            lines.append("")
            lines.append(f"{i}. {despertar} ({decision}{f') {age}' if age else ''}")
            if snippet:
                lines.append(f"   {snippet}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /recall {text!r} failed: {exc}")
        return _oops("search my episodes")


@register_command("memory")
async def _cmd_memory(args: str, *, config=None) -> str:
    """``/memory <query>``: search durable facts (substring/tag), LLM-free.

    Mirrors ``lhassa memory``'s search (:mod:`lhassa.memory.facts`): a query
    matches a fact's content or any of its tags. Shows up to 5 matches: tipo, a
    short snippet, and confianza. No args → a usage hint.
    """
    query = (args or "").strip()
    if not query:
        return "Usage: /memory <query>; searches durable facts by text or tag."
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.memory import facts

        resolved = facts.resolve(await facts.load_facts(instance_id))
        q = query.lower()
        matches = [
            f
            for f in resolved
            if q in f.contenido.lower() or any(q in t.lower() for t in f.tags)
        ]
        if not matches:
            return f"No facts match {query!r}."
        lines = [f"Facts matching {query!r} ({len(matches)}):"]
        for f in matches[:5]:
            snippet = f.contenido.replace("\n", " ").strip()
            if len(snippet) > 100:
                snippet = snippet[:97] + "…"
            lines.append(f"- [{f.tipo}] {snippet} (conf {f.confianza:.2f})")
        if len(matches) > 5:
            lines.append(f"…+{len(matches) - 5} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /memory {query!r} failed: {exc}")
        return _oops("search my memory")


@register_command("knowledge")
async def _cmd_knowledge(args: str, *, config=None) -> str:
    """``/knowledge <query>``: search the owner's OWN indexed documents, LLM-free.

    A $0 cosine search over :mod:`lhassa.memory.knowledge` (document perception
   ; the owner's opt-in ``LHASSA_KNOWLEDGE_ROOTS``). Shows up to 5 matches:
    the source filename and a short snippet. No args → a usage hint. No roots
    configured / empty index → a plain "nothing indexed yet" message.
    """
    query = (args or "").strip()
    if not query:
        return "Usage: /knowledge <query>; searches your indexed notes/papers."
    try:
        import os as _os

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.memory import knowledge

        hits = await knowledge.search(instance_id, query, limit=5, config=cfg)
        if not hits:
            return f"No indexed documents match {query!r}."
        lines = [f"Your documents matching {query!r} ({len(hits)}):"]
        for h in hits:
            name = _os.path.basename(h.get("source_path", "")) or "?"
            snippet = (h.get("text") or "").replace("\n", " ").strip()
            if len(snippet) > 150:
                snippet = snippet[:147] + "…"
            lines.append(f"- [{name}] {snippet}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /knowledge {query!r} failed: {exc}")
        return _oops("search my documents")


@register_command("checkin")
async def _cmd_checkin(args: str, *, config=None) -> str:
    """``/checkin <text>``: persist an owner-authored daily check-in.

    This is the explicit capture path for the personal digest questions. It is
    deterministic and $0: append one tagged fact to the grande, then acknowledge.
    """
    text = (args or "").strip()
    if not text:
        return (
            "Usage: /checkin <how you are / how you slept / what I should remember>"
        )
    try:
        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        from lhassa.core import db as _db
        from lhassa.memory import checkins

        await _db.init_db()
        item = await checkins.record(instance_id, text, source="telegram-checkin")
        if item.slot == "morning":
            return f"Noted for the morning trail: {item.id}"
        if item.slot == "evening":
            return f"Noted for tomorrow: {item.id}"
        return f"Noted: {item.id}"
    except Exception as exc:
        logger.warning(f"commands: /checkin failed: {exc}")
        return _oops("save that check-in")


async def _render_personal_agenda(config, *, slot: str) -> str:
    cfg = _config(config)
    from lhassa.personal import agenda as personal_agenda

    summary = await personal_agenda.build(cfg, slot=slot)
    lang = getattr(cfg, "digest_language", "") or "en"
    return personal_agenda.render(summary, language=lang)


@register_command("today")
async def _cmd_today(args: str, *, config=None) -> str:
    """``/today``: personal agenda for the current day, LLM-free."""
    try:
        return await _render_personal_agenda(config, slot="today")
    except Exception as exc:
        logger.warning(f"commands: /today failed: {exc}")
        return _oops("read today's agenda")


@register_command("tomorrow")
async def _cmd_tomorrow(args: str, *, config=None) -> str:
    """``/tomorrow``: personal agenda for the next day, LLM-free."""
    try:
        return await _render_personal_agenda(config, slot="tomorrow")
    except Exception as exc:
        logger.warning(f"commands: /tomorrow failed: {exc}")
        return _oops("read tomorrow's agenda")
