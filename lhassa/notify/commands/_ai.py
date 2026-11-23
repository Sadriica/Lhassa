# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/ai`` and ``/moa``: the paid direct doors.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

import re

from loguru import logger

from ._registry import register_command
from ._shared import _config, _oops


async def _resolve_chat_provider(cfg):
    """The (provider, model) attempts for a chat-seat call; pin/router + chain."""
    from lhassa.llm import router
    from lhassa.llm.provider import get_model_provider
    from lhassa.llm.router import fallback_chain

    pinned = (getattr(cfg, "telegram_provider", "") or "").strip().lower()
    if pinned and pinned != "auto":
        primary = get_model_provider(cfg, name=pinned)
    else:
        primary = router.pick_provider(sensitive=False, hard=False, config=cfg)
    model = router.pick_model(primary, hard=False, config=cfg)
    return [(primary, model), *fallback_chain(cfg, primary)]


@register_command("ai")
async def _cmd_ai(args: str, *, config=None) -> str:
    """``/ai <question>``: search the live web AND let the model SYNTHESIZE a
    concrete answer from what it finds.

    The difference from a plain question: free-text chat (tool-less) can only
    hand back links via the research bridge. ``/ai`` does the search itself,
    feeds the real results to the active model (with persona + the owner's
    durable facts as context), and asks it to ANSWER; a specific figure, a
    direct conclusion; citing the sources it used. It DOES spend one model
    call (the one paid direct door, like ``/cv``); kept explicit so it never
    fires on every message. Never raises.
    """
    text = (args or "").strip()
    if not text:
        return "Ask me to look something up and answer it: /ai how much is a flight from Madrid to Bogotá?"
    try:
        from lhassa.llm.gateway import BudgetExceeded, KillSwitchActive
        from lhassa.llm.provider import ProviderNotConfigured
        from lhassa.persona import load_identity
        from lhassa.tools import search as _search

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"

        # 1) Live search; news vertical for news-intent, else the web.
        results: list[dict] = []
        try:
            if re.search(r"\b(noticias?|news|titulares|headlines?)\b", text, re.IGNORECASE):
                results = await _search.news_search(text, max_results=6) or []
            if not results:
                results = await _search.web_search(text, max_results=6) or []
        except Exception as exc:
            logger.debug(f"commands: /ai search failed: {exc}")

        sources = "\n".join(
            f"- {str(r.get('title') or '')[:100]}; {str(r.get('body') or '')[:200]} "
            f"({r.get('url') or ''})"
            for r in results[:6]
        ) or "(no web results; answer from your own knowledge and say it's not live-sourced)"

        # 2) Persona + durable facts as grounding context.
        identity = load_identity(instance_id)
        facts_block = ""
        try:
            from lhassa.memory import facts as _facts

            rows = await _facts.load_facts(instance_id, pinned=True)
            if rows:
                facts_block = "What you know about your owner:\n" + "\n".join(
                    f"- {getattr(r, 'contenido', '')}" for r in rows[:8]
                )
        except Exception:
            facts_block = ""

        system = "\n\n".join(
            p for p in (
                identity.persona_block,
                facts_block,
                "You are given live web results. ANSWER the question directly and "
                "specifically (a figure, a range, a concrete conclusion) using them; "
                "cite the source name in-line. If the results don't cover it, say so "
                "plainly. Be brief, plain, and honest about uncertainty.",
            ) if p
        )
        user = f"Question: {text}\n\nLive web results:\n{sources}"

        # 3) Synthesize through the active chat provider (with the fallback chain).
        # Looked up through the package (not the module-local name) so a test
        # can monkeypatch ``lhassa.notify.commands._resolve_chat_provider``;
        # the same public seam the flat module used to expose directly.
        from lhassa.notify import commands as _commands

        last_exc = None
        for prov, mdl in await _commands._resolve_chat_provider(cfg):
            try:
                out = await prov.complete(
                    [{"role": "user", "content": user}],
                    model=mdl, max_tokens=1024, system=system,
                )
            except KillSwitchActive as exc:
                # A manual kill is a hard stop, independent of configured
                # provider order.  Never continue into a later CLI/cloud seat.
                last_exc = exc
                break
            except (ProviderNotConfigured, BudgetExceeded) as exc:
                last_exc = exc
                continue
            except Exception as exc:
                logger.warning(f"commands: /ai provider {getattr(prov,'name','?')!r} failed: {exc}")
                last_exc = exc
                continue
            answer = (out.text or "").strip()
            return answer or "The model returned nothing."
        return _oops(f"reach a model for that ({last_exc})")
    except Exception as exc:
        logger.warning(f"commands: /ai failed: {exc}")
        return _oops("look that up and answer it")


# Provider name -> the label shown in the panel footer. "default" is the
# cost-gateway seat (deepseek, in practice); the owner-facing name is more
# honest than the internal registry key.
_MOA_LABELS: dict[str, str] = {"default": "deepseek"}


def _moa_label(name: str) -> str:
    return _MOA_LABELS.get(name, name)


def _moa_footer(result) -> str:
    """A compact 'panel: claude ✓, codex ✓, deepseek ✗ (unavailable) · $0.0007' line."""
    parts = []
    for entry in result.panel:
        label = _moa_label(entry.get("provider", "?"))
        if entry.get("ok"):
            parts.append(f"{label} ✓")
        else:
            err = (entry.get("error") or "failed")[:60]
            parts.append(f"{label} ✗ ({err})")
    panel_str = ", ".join(parts) if parts else "(no panelists)"
    return f"panel: {panel_str} · ${result.total_cost_usd:.4f}"


@register_command("moa")
async def _cmd_moa(args: str, *, config=None) -> str:
    """``/moa <question>``: mixture-of-agents: ask a panel, get a synthesized verdict.

    For a decision that is expensive to get wrong, one model's opinion isn't
    enough: this fans ``<question>`` out to the owner's configured fleet
    (:func:`lhassa.llm.moa.moa`, default ``claude,codex,default``) in
    parallel and has ONE more (cheap) completion compare the answers and
    synthesize a single verdict; honestly naming real disagreement. This is
    an explicit, opt-in spend: it is never wired into the automatic think
    path, only this command (and the forge's ``moa_verdict`` review). Never
    raises.
    """
    question = (args or "").strip()
    if not question:
        return (
            "Usage: /moa <question>; asks a panel of models (not just one) and "
            "synthesizes their answers; costs more than a normal chat, so use it "
            "for decisions that are expensive to get wrong."
        )
    try:
        cfg = _config(config)
        from lhassa.llm.moa import moa as _moa

        result = await _moa([{"role": "user", "content": question}], config=cfg)
    except Exception as exc:
        logger.warning(f"commands: /moa failed: {exc}")
        return _oops("run that through the panel")

    return f"{result.text}\n\n{_moa_footer(result)}"
