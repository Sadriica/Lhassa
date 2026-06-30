"""
The router — decides, in code, *how* the brain should think (§2.4).

Two axes:
  1. ``pick_provider`` — by cost and **data sensitivity**. Sensitive data must go
     to a ``zero_retention`` provider; otherwise the cheapest usable one.
  2. ``pick_model`` — a cheap model for routine judgment, a smart model for hard
     judgment.

``decide(trigger, ctx)`` turns a watchlist trigger into a ``Decision``. Phase 2
only produces ``kind="think"`` (run a ModelProvider directly); the ``"delegate"``
branch — handing agentic code work to a harness — is Phase 4 (see the TODO).

``cascade`` runs the cheap model first and escalates to the smart model once if a
caller-supplied ``verify`` rejects the answer. The outcome is logged so the router
can learn later.

Manual overrides (``$think`` / ``$justdo``) are exposed as ``force_think`` /
``force_justdo`` flags the CLI/REPL can pass for the next turn — they do NOT
replace the autonomous watchlist.

Nothing here is a token cost in itself: the decisions are rules-based and free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

from lhassa.llm.provider import Completion, ModelProvider, get_model_provider

if TYPE_CHECKING:
    from lhassa.config import Config
    from lhassa.core.watchlist import Trigger
    from lhassa.memory.load import Context


@dataclass
class Decision:
    """The router's verdict for one trigger.

    ``kind``     — "think" (run a ModelProvider) or "delegate" (Phase 4 harness).
    ``provider`` — the chosen ModelProvider (None only for an unresolved delegate).
    ``model``    — the chosen model id (None for delegate).
    ``reason``   — a human-readable trace, stored on the episode's ``decision``.
    """

    kind: str
    provider: Optional[ModelProvider]
    model: Optional[str]
    reason: str


# ── Axis 1: provider (cost + data sensitivity) ────────────────────────────────


def pick_provider(*, sensitive: bool, config: "Config") -> ModelProvider:
    """Pick a provider. Sensitive data → a zero-retention provider if available.

    If the data is sensitive but no zero-retention provider is configured, fall
    back to the default and log a warning (the call still happens, but the
    operator is told their data may be retained).
    """
    default = get_model_provider(config)
    if not sensitive:
        return default
    if default.zero_retention:
        return default
    logger.warning(
        "router: sensitive data but no zero-retention provider available; "
        f"falling back to {default.name!r} (may retain data)."
    )
    return default


# ── Axis 2: model (cheap routine vs smart hard) ───────────────────────────────


def pick_model(provider: ModelProvider, *, hard: bool, config: "Config") -> str:
    """Cheap model for routine judgment, smart model for hard judgment.

    With ``LHASSA_ROUTER=off`` routing is disabled and ``config.model`` is always
    used. ``provider`` is accepted for a future per-provider model map.
    """
    if getattr(config, "router_mode", "rules") == "off":
        return config.model
    return config.model_smart if hard else config.model_cheap


# ── Sensitivity / hardness heuristics (rules-based, Phase 0/1) ─────────────────


def _is_sensitive(trigger: "Trigger", ctx: "Context | None") -> bool:
    """Whether this judgment touches sensitive data. Simple, explicit signals only."""
    params = getattr(trigger, "params", {}) or {}
    return bool(params.get("sensitive") or params.get("sensible"))


def _is_hard(trigger: "Trigger", ctx: "Context | None") -> bool:
    """Whether this judgment is hard enough to warrant the smart model."""
    params = getattr(trigger, "params", {}) or {}
    return bool(params.get("hard"))


# ── decide ────────────────────────────────────────────────────────────────────


def decide(
    trigger: "Trigger",
    ctx: "Context | None" = None,
    *,
    config: "Config",
    force_think: bool = False,
    force_justdo: bool = False,
) -> Decision:
    """Turn a trigger into a Decision (rules-based, free).

    ``force_think`` (``$think``) forces the smart model — deliberate this turn.
    ``force_justdo`` (``$justdo``) forces the cheap model — act without
    deliberation this turn. Both override the routine/hard heuristic.
    """
    sensitive = _is_sensitive(trigger, ctx)
    provider = pick_provider(sensitive=sensitive, config=config)

    if force_think:
        hard = True
    elif force_justdo:
        hard = False
    else:
        hard = _is_hard(trigger, ctx)

    # Phase 4 TODO: a "delegate" axis — when the trigger is agentic code work,
    # return Decision(kind="delegate", provider=None, model=None, ...) and let a
    # HarnessAdapter run it. Phase 2 always thinks directly.
    model = pick_model(provider, hard=hard, config=config)

    forced = "think" if force_think else "justdo" if force_justdo else ""
    reason = (
        f"think: provider={provider.name} model={model} "
        f"sensitive={sensitive} hard={hard}"
        + (f" forced={forced}" if forced else "")
    )
    return Decision(kind="think", provider=provider, model=model, reason=reason)


# ── cascade: cheap → verify → escalate ────────────────────────────────────────

# A verifier inspects a cheap Completion and returns True if it is good enough.
VerifyFn = Callable[[Completion], bool]


async def cascade(
    messages: list[dict],
    *,
    config: "Config",
    provider: Optional[ModelProvider] = None,
    verify: Optional[VerifyFn] = None,
    max_tokens: int = 1024,
    **opts,
) -> Completion:
    """Run the cheap model first; escalate to the smart model once if ``verify`` fails.

    ``verify`` defaults to None — no verification, so the cheap answer is returned
    as-is (no escalation). The cascade outcome is logged so the router can learn.
    """
    provider = provider or get_model_provider(config)
    cheap = pick_model(provider, hard=False, config=config)

    cheap_result = await provider.complete(messages, model=cheap, max_tokens=max_tokens, **opts)

    if verify is None or verify(cheap_result):
        logger.info(f"cascade: cheap accepted (provider={provider.name} model={cheap})")
        return cheap_result

    smart = pick_model(provider, hard=True, config=config)
    logger.info(
        f"cascade: cheap rejected → escalate (provider={provider.name} "
        f"{cheap} → {smart})"
    )
    return await provider.complete(messages, model=smart, max_tokens=max_tokens, **opts)
