# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The router — decides, in code, *how* the brain should think (§2.4).

Two axes:
  1. ``pick_provider`` — by cost and **data sensitivity**. Sensitive data must go
     to a ``zero_retention`` provider; otherwise the cheapest usable one.
  2. ``pick_model`` — a cheap model for routine judgment, a smart model for hard
     judgment.

``decide(trigger, ctx)`` turns a watchlist trigger into a ``Decision`` —
``kind="think"`` (run a ModelProvider directly) or ``kind="delegate"`` (hand
agentic code work to a harness via ``pick_harness``; ``core/step.py:_delegate``
launches it fire-and-forget).

``cascade`` runs the cheap model first and escalates to the smart model once if a
caller-supplied ``verify`` rejects the answer. The outcome is logged so the router
can learn later.

Manual overrides (``$think`` / ``$justdo``) are exposed as ``force_think`` /
``force_justdo`` flags the CLI/REPL can pass for the next turn — they do NOT
replace the autonomous watchlist.

Nothing here is a token cost in itself: the decisions are rules-based and free.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

from lhassa.llm.provider import (
    Completion,
    ModelProvider,
    PseudonymizingProvider,
    get_model_provider,
)

if TYPE_CHECKING:
    from lhassa.config import Config
    from lhassa.core.watchlist import Trigger
    from lhassa.harness.adapter import HarnessAdapter
    from lhassa.memory.load import Context


@dataclass
class Decision:
    """The router's verdict for one trigger.

    ``kind``     — "think" (run a ModelProvider) or "delegate" (a harness, Phase 4).
    ``provider`` — the chosen ModelProvider (None for a delegate).
    ``model``    — the chosen model id (None for a delegate).
    ``reason``   — a human-readable trace, stored on the episode's ``decision``.
    ``harness``  — the chosen harness name (set only when ``kind=="delegate"``).
    """

    kind: str
    provider: Optional[ModelProvider]
    model: Optional[str]
    reason: str
    harness: Optional[str] = None


# ── Axis 1: provider (cost + data sensitivity) ────────────────────────────────


def _maybe_pseudonymize(provider: ModelProvider, config: "Config") -> ModelProvider:
    """Wrap a non-local provider in the pseudonymization hook when enabled.

    The hook RELAXES for local providers: a local model runs on the machine, so
    the data never leaves the box and there is nothing to scrub (§3-Fase 6).
    """
    if getattr(provider, "is_local", False):
        return provider
    if getattr(config, "pseudonymize_remote", False):
        return PseudonymizingProvider(provider)
    return provider


def pick_provider(*, sensitive: bool, config: "Config", hard: bool = False) -> ModelProvider:
    """Pick a provider by data sensitivity and routine-vs-hard signal (§2.4, §3-Fase 6).

    Routine/heartbeat thinking (``hard=False``) prefers the LOCAL model when it is
    configured — its ``is_local`` keeps the data on the box, so pseudonymization is
    not applied. ``config.heartbeat_provider`` steers this: ``cloud`` never uses
    local/opencode; ``local`` forces local for the routine path (falling back to
    cloud with a warning if unconfigured); ``opencode`` forces the opencode CLI;
    ``auto`` prefers local when ``local_model_url`` is set, else the opencode CLI
    when its binary is on PATH, else cloud.

    The HARD path always goes to the cloud smart model. Sensitive data still
    routes to a zero-retention provider (and warns if none is configured). The
    cloud path — including opencode, which is a cloud subscription — is wrapped in
    the pseudonymization hook when ``pseudonymize_remote`` is on; the local path is
    never wrapped.
    """
    heartbeat = (getattr(config, "heartbeat_provider", "auto") or "auto").lower()
    local_url = (getattr(config, "local_model_url", "") or "").strip()
    routine = not hard

    # Routine/heartbeat path → prefer local, then opencode, before falling to cloud.
    if routine and heartbeat != "cloud":
        # Explicit CLI-subscription providers: opencode / claude (Claude Code CLI) /
        # codex (Codex CLI). They degrade at call time if the binary/credentials are
        # missing. All three are cloud-ish (not local) → the privacy hook applies.
        if heartbeat in ("opencode", "claude", "codex"):
            return _maybe_pseudonymize(
                get_model_provider(config, name=heartbeat), config
            )

        wants_local = heartbeat == "local" or (heartbeat == "auto" and bool(local_url))
        if wants_local:
            if local_url:
                # is_local=True → not wrapped; the privacy hook relaxes.
                return get_model_provider(config, name="local")
            if heartbeat == "local":
                logger.warning(
                    "router: heartbeat_provider='local' but local_model_url is "
                    "unset — falling back to the cloud provider."
                )

        # auto + no local backend → prefer the cheap opencode CLI when present.
        # ``opencode_bin`` defaults to "" here so incomplete config doubles never
        # trip this; the real Config always sets it ("opencode").
        if heartbeat == "auto" and not local_url:
            opencode_bin = (getattr(config, "opencode_bin", "") or "").strip()
            if opencode_bin and shutil.which(opencode_bin):
                return _maybe_pseudonymize(
                    get_model_provider(config, name="opencode"), config
                )

    # Cloud path (hard, cloud-forced, or no local backend available).
    default = get_model_provider(config)
    if sensitive and not default.zero_retention:
        logger.warning(
            "router: sensitive data but no zero-retention provider available; "
            f"falling back to {default.name!r} (may retain data)."
        )
    return _maybe_pseudonymize(default, config)


# ── Axis 2 (delegate): pick a harness by cost / capability / plan ─────────────


# Lower rank = preferred. Free and metered beat subscription when equivalent
# (avoid the paths the subscription policy made expensive — §2.4).
_BILLING_RANK = {"free": 0, "metered": 1, "subscription": 2}


def pick_harness(
    *, config: "Config", available: "Optional[list[HarnessAdapter]]" = None
) -> "Optional[HarnessAdapter]":
    """Choose a coding muscle among the available adapters (§2.4).

    Order of selection:
      1. ``LHASSA_HARNESS`` / ``config.harness`` override — honored if it names a
         known adapter (availability is enforced later by the runner, which records
         an error job for a missing CLI; this keeps the override predictable).
      2. otherwise the cheapest available adapter by billing rank (free < metered <
         subscription), ties broken by registry/preference order.
    Returns ``None`` only when nothing is available and no override is set.
    """
    from lhassa.harness.adapter import available_adapters, get_adapter

    override = (getattr(config, "harness", "") or os.getenv("LHASSA_HARNESS", "")).strip()
    if override:
        try:
            chosen = get_adapter(override)
            logger.info(f"router: harness override → {chosen.name}")
            return chosen
        except ValueError:
            logger.warning(f"router: LHASSA_HARNESS={override!r} is unknown — ignoring")

    avail = available_adapters() if available is None else list(available)
    if not avail:
        return None
    return min(avail, key=lambda a: _BILLING_RANK.get(a.billing, 9))


# ── Axis 1: model (cheap routine vs smart hard) ───────────────────────────────


def pick_model(provider: ModelProvider, *, hard: bool, config: "Config") -> str:
    """Cheap model for routine judgment, smart model for hard judgment.

    With ``LHASSA_ROUTER=off`` routing is disabled and ``config.model`` is always
    used. A local provider serves a single model, so it always uses
    ``config.local_model`` (its served name) regardless of the cheap/smart axis.
    """
    if getattr(provider, "is_local", False):
        return (getattr(config, "local_model", "") or "") or config.model
    # CLI-SUBSCRIPTION providers run their OWN subscription model, NOT the API
    # ``model_cheap``/``model_smart`` (which are Groq/OpenAI ids the CLI never uses
    # — the source of the "provider=claude model=llama-…" ledger lie). Report the
    # configured model for that CLI, else a truthful ``<cli>-cli`` marker meaning
    # "the CLI picks its own model". These names also proxy through the
    # pseudonymizing wrapper, so a wrapped provider is caught too.
    name = getattr(provider, "name", "")
    if name == "opencode":
        return (getattr(config, "opencode_model", "") or "") or config.model
    if name == "claude":
        return (getattr(config, "claude_model", "") or "").strip() or "claude-cli"
    if name == "codex":
        return (getattr(config, "codex_model", "") or "").strip() or "codex-cli"
    if getattr(config, "router_mode", "rules") == "off":
        return config.model
    return config.model_smart if hard else config.model_cheap


# ── Fallback provider CHAIN (moved from notify/telegram_inbound.py) ───────────
#
# Originally telegram_inbound-only; the heartbeat's judgment path (core/step.py
# ``_judge``) needs the exact same "walk the owner's CLI subscriptions before
# paying for cloud" behavior on a ProviderNotConfigured/BudgetExceeded, so this
# lives in the interface layer (llm/) where both a shell (notify/) and the spine
# (core/) can reach it. ``notify/telegram_inbound.py`` keeps a thin re-export so
# existing imports/tests are unaffected.

# Provider names that are backed by a local CLI binary — skipped from the chain
# when that binary is not installed (``shutil.which``), so the chain degrades
# cleanly on a machine that lacks a given subscription CLI. The binary name can be
# overridden per-provider via a ``<name>_bin`` config attr (e.g. ``opencode_bin``).
CLI_PROVIDERS = {"opencode", "claude", "codex"}


def _fallback_model_for(name: str, config: "Config") -> "str | None":
    """The model arg for a chain provider.

    The cloud gateway (``default``) uses the cheap routine model, as before; the
    CLI subscriptions (claude/codex/opencode) and other providers pass ``None`` so
    each CLI/provider picks its own model from the owner's subscription/config.
    """
    if name == "default":
        return (getattr(config, "model_cheap", "") or getattr(config, "model", "") or None)
    return None


def fallback_chain(config: "Config", primary: Optional[ModelProvider]) -> list[tuple]:
    """Build the configured chat/think fallback chain as ``[(provider, model), …]``.

    Walks ``config.fallback_providers`` (comma-separated names, default
    ``"claude,codex,default"``) IN ORDER, resolving each via
    :func:`lhassa.llm.provider.get_model_provider`. This keeps the owner on their
    CLI subscriptions (``opencode → claude → codex``) before paying for the cloud
    gateway (``default``). Each entry is:

    - **skipped if it is the same kind as the primary** (``name == primary.name``) —
      don't retry e.g. opencode→opencode;
    - **skipped if it is a CLI provider whose binary is not installed**
      (``shutil.which``), so the chain degrades cleanly on a machine missing that CLI;
    - **skipped if it cannot be resolved** (unknown/raising factory).

    Never raises — a bad config or a failed resolve yields a shorter chain. This is
    the seam tests patch to assert the chain order / skipping behaviour.
    """
    chain: list[tuple] = []
    try:
        names = [
            n.strip()
            for n in (getattr(config, "fallback_providers", "") or "").split(",")
            if n.strip()
        ]
    except Exception:
        return chain

    # Local (not module-level) import so a caller/test that monkeypatches
    # ``lhassa.llm.provider.get_model_provider`` is picked up fresh on every
    # call — mirrors the original telegram_inbound._fallback_chain behaviour.
    from lhassa.llm.provider import get_model_provider

    primary_name = getattr(primary, "name", "")
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        if name == primary_name:
            continue  # don't retry the same kind as the primary
        if name in CLI_PROVIDERS:
            binary = (getattr(config, f"{name}_bin", "") or "") or name
            if shutil.which(binary) is None:
                continue  # CLI not installed → skip, degrade cleanly
        try:
            provider = get_model_provider(config, name=name)
        except Exception:
            continue
        if provider is not None:
            # Same privacy treatment as pick_provider: a REMOTE fallback seat
            # must be pseudonymized too. Without this, the one turn that
            # happened to fail over carried raw sensitive text to the cloud —
            # the primary being wrapped is no protection for its substitutes.
            chain.append(
                (_maybe_pseudonymize(provider, config), _fallback_model_for(name, config))
            )
    return chain


# ── Budget soft-limit (a brake before BudgetExceeded hits outright) ──────────


# Process-local cache for the persisted-ledger read below — the router's
# ``decide`` is on the hot path (called once per judgment) and the do-and-sleep
# execution model means most callers ARE the process (a heartbeat beat lives
# seconds), so a short TTL still protects against a hot loop of ``decide``
# calls within one beat while staying fresh across beats.
_BUDGET_CACHE_TTL_S = 60.0
_budget_cache: dict[str, tuple[float, int]] = {}  # day -> (monotonic_ts, spent_tokens)


def _budget_pct_used(config: "Config") -> float:
    """Best-effort fraction of today's daily token budget already spent.

    The soft brake must trip even though each heartbeat beat is a brand-new
    ``systemd`` process (do-and-sleep, per the architecture): a per-process
    in-memory mirror (``lhassa.state.state.metrics``) resets to zero on every
    such process, so it would never see yesterday's — or even this run's
    earlier — spend and the 80% brake could simply never fire. The source of
    truth here is instead the **persisted gateway ledger**
    (``lhassa.llm.gateway``'s ``daily_spend`` table, the same one
    ``spend_summary()`` reports from) via
    :meth:`LedgerGateway.daily_output_tokens_sync` — a plain, synchronous,
    read-only SQLite read (``decide`` is sync and is called both with and
    without a running event loop, so the async ``spend_summary`` can't just be
    awaited here). The read is cheap but not free, so it is cached per-process
    for ``_BUDGET_CACHE_TTL_S`` (60s) keyed by UTC day.

    Falls back to the in-process mirror (the previous behaviour) if the
    ledger read raises for any reason, and to ``0.0`` when unlimited
    (budget<=0) or if everything fails. Never raises.
    """
    budget = getattr(config, "llm_daily_token_budget", 0) or 0
    if budget <= 0:
        return 0.0
    try:
        import time

        from lhassa.llm.gateway import _today, gateway

        day = _today()
        cached = _budget_cache.get(day)
        now = time.monotonic()
        if cached is not None and (now - cached[0]) < _BUDGET_CACHE_TTL_S:
            spent = cached[1]
        else:
            spent = gateway.daily_output_tokens_sync(day)
            _budget_cache[day] = (now, spent)
        return spent / budget
    except Exception:
        pass
    try:
        from lhassa.state import state

        spent = int(state.metrics.get("llm_output_tokens", 0) or 0)
        return spent / budget
    except Exception:
        return 0.0


# ── Sensitivity / hardness heuristics (rules-based, Phase 0/1) ─────────────────


def _is_sensitive(trigger: "Trigger", ctx: "Context | None") -> bool:
    """Whether this judgment touches sensitive data. Simple, explicit signals only."""
    params = getattr(trigger, "params", {}) or {}
    return bool(params.get("sensitive") or params.get("sensible"))


def _is_hard(trigger: "Trigger", ctx: "Context | None") -> bool:
    """Whether this judgment is hard enough to warrant the smart model."""
    params = getattr(trigger, "params", {}) or {}
    return bool(params.get("hard"))


def _is_delegate(trigger: "Trigger") -> bool:
    """Whether this trigger is delegated *code* work (Axis 2) rather than thinking.

    Signalled by an explicit ``delegate=True`` flag in the trigger params or a
    ``kind=="code"`` trigger. Everything else thinks directly via a provider.
    """
    params = getattr(trigger, "params", {}) or {}
    return bool(params.get("delegate")) or getattr(trigger, "kind", "") == "code"


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
    # Axis 2 (delegate): agentic code work goes to a harness, not the provider.
    # ``$think`` forces direct deliberation, so it suppresses delegation this turn.
    if not force_think and _is_delegate(trigger):
        harness = pick_harness(config=config)
        if harness is not None:
            reason = (
                f"delegate: harness={harness.name} billing={harness.billing} "
                f"model_locked={harness.model_locked}"
            )
            return Decision(
                kind="delegate", provider=None, model=None,
                reason=reason, harness=harness.name,
            )
        logger.info("router: delegation requested but no harness available — thinking")

    sensitive = _is_sensitive(trigger, ctx)

    if force_think:
        hard = True
    elif force_justdo:
        hard = False
    else:
        hard = _is_hard(trigger, ctx)

    # Budget soft-limit: once today's spend crosses ``budget_soft_pct`` of the
    # daily token budget, never pick the smart model — downgrading ``hard`` to
    # False both picks the cheap model AND re-enters the routine provider
    # preference (local/opencode before cloud), so this is also how spend
    # pressure steers the brain toward $0 providers.
    budget_note = ""
    soft_pct = getattr(config, "budget_soft_pct", 0.8)
    if hard:
        pct_used = _budget_pct_used(config)
        if pct_used >= soft_pct:
            hard = False
            budget_note = f" budget_downgrade(pct_used={pct_used:.0%}>=soft={soft_pct:.0%})"

    # Thread the routine/hard signal into provider selection so the heartbeat
    # (routine) path can prefer the local model (§3-Fase 6).
    provider = pick_provider(sensitive=sensitive, config=config, hard=hard)
    model = pick_model(provider, hard=hard, config=config)

    forced = "think" if force_think else "justdo" if force_justdo else ""
    reason = (
        f"think: provider={provider.name} model={model} "
        f"sensitive={sensitive} hard={hard}"
        + (f" forced={forced}" if forced else "")
        + budget_note
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
