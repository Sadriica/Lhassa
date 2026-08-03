# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Mixture-of-Agents (MoA) — the same prompt to N providers in parallel, an
aggregator synthesizes a single answer (Wave D, §doc/plan.md).

For decisions that are expensive to get wrong, one model's opinion isn't
enough: fan a prompt out to the owner's chosen fleet (default
``claude,codex,default`` — the two subscription CLIs plus the cost-gateway
seat), let each panelist answer independently, then spend ONE more
completion — the routine/cheap path, never the hard one, this is a
comparison-and-synthesis task — comparing the answers and returning a single
verdict that names real disagreement honestly instead of papering over it.

This is an explicit, opt-in consultation: the ``/moa`` chat command and
:func:`lhassa.agents.author.propose_tool`'s ``moa_verdict`` kwarg are the only
callers. It is never wired into the automatic think path — the extra spend of
running N models is always something the owner (or an explicit code path)
asked for.

Every sub-call and the aggregation call are fully try/excepted: a dead CLI, a
missing binary, a budget-exceeded provider, or a malformed/empty reply all
become one honest panel entry (or an honest aggregator fallback) — never sink
the rest of the panel, never raise out of :func:`moa`.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from lhassa.llm import router as _router
from lhassa.llm.provider import ModelProvider, get_model_provider

# Owner-decided default fleet: the two subscription CLIs + the cost-gateway
# seat (deepseek, in practice today) — see doc/plan.md Wave D.
_DEFAULT_PROVIDERS = "claude,codex,default"

# Fewer than this many honest panel answers isn't a "mixture" — degrade to the
# single answer instead of paying for an aggregation pass over one opinion.
_MIN_PANEL = 2

_AGGREGATOR_SYSTEM = (
    "You are reviewing {n} independent answers to the SAME question from "
    "different models, each labeled by its provider name. Compare them: "
    "identify where they agree, identify any real disagreement, and "
    "synthesize the single best answer. If the panel genuinely disagrees on "
    "something material, say so plainly instead of papering over it — "
    "honesty about disagreement is more useful than false consensus. Answer "
    "the original question directly; do not just describe the panel."
)


@dataclass
class MoaResult:
    """One MoA consultation's outcome.

    ``panel`` is one dict per fleet member: ``{"provider": name, "ok": bool,
    "text": str, "cost_usd": float}`` on success, or ``{"provider": name,
    "ok": False, "error": str, "cost_usd": 0.0}`` on a degrade. ``text`` is
    the FINAL answer to show the caller (the aggregator's synthesis, or a
    degrade fallback). ``aggregator_provider`` is ``None`` when no
    aggregation pass ran (too few panelists, or the aggregator itself
    degraded).
    """

    text: str
    panel: list[dict] = field(default_factory=list)
    aggregator_provider: Optional[str] = None
    total_cost_usd: float = 0.0


def _fleet(providers: Optional[list[str]], config: Any) -> list[str]:
    """Resolve the panel's provider names — explicit arg, else config, else default."""
    if providers:
        return [p.strip() for p in providers if p and p.strip()]
    raw = getattr(config, "moa_providers", "") or _DEFAULT_PROVIDERS
    return [p.strip() for p in raw.split(",") if p.strip()]


def _binary_missing(name: str, config: Any) -> bool:
    """True iff ``name`` is a CLI provider whose binary is not on PATH.

    Mirrors :func:`lhassa.llm.router.fallback_chain`'s own skip-if-missing
    discipline so a dead CLI never even gets attempted — an honest panel
    entry is cheaper than a doomed subprocess spawn.
    """
    if name not in _router.CLI_PROVIDERS:
        return False
    binary = (getattr(config, f"{name}_bin", "") or "") or name
    return shutil.which(binary) is None


async def _call_one(
    name: str, config: Any, messages: list[dict], system: Optional[str], max_tokens: int
) -> dict:
    """Resolve + call one panelist. Never raises — returns an honest panel entry."""
    if _binary_missing(name, config):
        return {"provider": name, "ok": False, "error": "unavailable", "cost_usd": 0.0}

    try:
        provider: ModelProvider = get_model_provider(config, name=name)
    except Exception as exc:
        return {
            "provider": name,
            "ok": False,
            "error": f"unresolvable: {exc}"[:200],
            "cost_usd": 0.0,
        }

    try:
        model = _router.pick_model(provider, hard=False, config=config)
        completion = await provider.complete(
            messages, model=model, max_tokens=max_tokens, system=system
        )
    except Exception as exc:
        return {"provider": name, "ok": False, "error": str(exc)[:200], "cost_usd": 0.0}

    text = (completion.text or "").strip()
    if not text:
        return {
            "provider": name,
            "ok": False,
            "error": "empty answer",
            "cost_usd": float(completion.cost_usd or 0.0),
        }
    return {
        "provider": name,
        "ok": True,
        "text": text,
        "cost_usd": float(completion.cost_usd or 0.0),
    }


def _aggregator_prompt(question: str, oks: list[dict]) -> list[dict]:
    """The aggregator's one user turn: the original question + each labeled answer."""
    labeled = "\n\n".join(f"[{e['provider']}]\n{e['text']}" for e in oks)
    content = f"Original question:\n{question}\n\nPanel answers:\n\n{labeled}"
    return [{"role": "user", "content": content}]


def _question_text(messages: list[dict]) -> str:
    """Best-effort flatten of the caller's prompt for the aggregator's user turn."""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))


async def moa(
    messages: list[dict],
    *,
    providers: Optional[list[str]] = None,
    config: Any = None,
    system: Optional[str] = None,
    max_tokens: int = 800,
) -> MoaResult:
    """Fan ``messages`` out to a panel of providers, then synthesize one answer.

    ``providers`` overrides the fleet for this call; otherwise
    ``getattr(config, "moa_providers", "claude,codex,default")`` (a CSV) is
    used. Degrades honestly at every step and NEVER raises:

    - a panelist's dead CLI / missing binary / budget-exceeded / malformed
      reply becomes one honest ``panel`` entry, never sinks the others (each
      sub-call is independently try/excepted before ``asyncio.gather``);
    - fewer than 2 successful panel answers SKIPS aggregation entirely (no
      extra spend) — returns the single answer, or an honest failure text if
      nobody answered;
    - an aggregator failure degrades to the first successful panel answer.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    fleet = _fleet(providers, config)
    if not fleet:
        return MoaResult(text="No panel providers configured.", panel=[], total_cost_usd=0.0)

    tasks = [_call_one(name, config, messages, system, max_tokens) for name in fleet]
    try:
        panel = list(await asyncio.gather(*tasks))
    except Exception as exc:  # each task is already defensive; belt-and-braces
        logger.warning(f"moa: fan-out failed unexpectedly: {exc}")
        return MoaResult(
            text="The panel could not be reached right now.", panel=[], total_cost_usd=0.0
        )

    oks = [e for e in panel if e.get("ok")]
    total_cost = sum(float(e.get("cost_usd") or 0.0) for e in panel)

    if not oks:
        return MoaResult(
            text=(
                "The panel didn't come back with an answer this time — "
                "every provider was unavailable."
            ),
            panel=panel,
            total_cost_usd=total_cost,
        )
    if len(oks) < _MIN_PANEL:
        return MoaResult(text=oks[0]["text"], panel=panel, total_cost_usd=total_cost)

    # Aggregation: one more completion, the ROUTINE (cheap) path — never the
    # smart/hard model, this is a comparison-and-synthesis task, not a hard
    # judgment call. Mirrors the router's own cheap-path choice (§router.cascade).
    question = _question_text(messages)
    try:
        agg_provider = _router.pick_provider(sensitive=False, config=config, hard=False)
        agg_model = _router.pick_model(agg_provider, hard=False, config=config)
        agg_completion = await agg_provider.complete(
            _aggregator_prompt(question, oks),
            model=agg_model,
            max_tokens=max_tokens,
            system=_AGGREGATOR_SYSTEM.format(n=len(oks)),
        )
        total_cost += float(agg_completion.cost_usd or 0.0)
        agg_text = (agg_completion.text or "").strip()
        if not agg_text:
            raise ValueError("aggregator returned an empty answer")
        return MoaResult(
            text=agg_text,
            panel=panel,
            aggregator_provider=agg_provider.name,
            total_cost_usd=total_cost,
        )
    except Exception as exc:
        logger.warning(
            f"moa: aggregation degraded, falling back to the first panel answer: {exc}"
        )
        return MoaResult(
            text=oks[0]["text"],
            panel=panel,
            aggregator_provider=None,
            total_cost_usd=total_cost,
        )
