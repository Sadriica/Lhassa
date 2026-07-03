"""
Deterministic policy layer — the brain's "reflexes" (zero-token triage).

Today every trigger that doesn't map to a fixed action falls straight through to
LLM judgment, even when the right response is obvious in code: a finished job
that succeeded cleanly needs no review, a repo change on a trusted path can just
be synced, a "warn"-level health digest just needs a notification. This module is
the deterministic middle step between the watchlist (§2.4, ZERO tokens) and the
judgment/router path (Phase 2, real tokens): a small ordered list of **reflex**
rule functions that either resolve a trigger outright (a fixed action, or a
"nothing to do here" skip) or hand it to judgment with a *sized* context request
(``context_tier``) and a hardness hint (``hard``).

A reflex is a pure function ``(Trigger) -> PolicyDecision | None``; the first
reflex to return non-``None`` wins (``REFLEXES`` preserves registration order via
the shared :class:`~lhassa.registry.Registry`, mirroring ``core/actions.py``).
:func:`evaluate` NEVER raises — a broken reflex (or a totally unexpected error)
degrades to the safe default: think, minimal context, not hard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.registry import Registry

if TYPE_CHECKING:
    from lhassa.core.watchlist import Trigger

# ── the verdict ────────────────────────────────────────────────────────────────


@dataclass
class PolicyDecision:
    """One reflex's verdict for a trigger.

    ``kind``          — "action" (run a fixed deterministic action), "skip" (log
                         the episode and stop, $0, no LLM), or "think" (hand off
                         to the router/judgment path).
    ``action``         — the fixed action name to run, only set when kind=="action".
    ``params``         — params for that fixed action (kind=="action" only).
    ``context_tier``   — "minimal" or "full" — how much memory the judgment path
                         should load (kind=="think" only; ignored otherwise).
    ``hard``           — whether this judgment is hard enough for the smart model
                         (kind=="think" only; ignored otherwise).
    ``reason``         — human-readable trace, folded into the episode's ``decision``.
    """

    kind: str
    action: Optional[str] = None
    params: dict = field(default_factory=dict)
    context_tier: str = "minimal"
    hard: bool = False
    reason: str = ""


# ── reflex registry (mirrors core/actions.py's ACTIONS pattern) ────────────────

ReflexFn = Callable[["Trigger"], "Optional[PolicyDecision]"]
REFLEXES: Registry[ReflexFn] = Registry(kind="reflex")


def register_reflex(name: str, fn: "ReflexFn | None" = None, *, override: bool = False):
    """Register a reflex under ``name``. Usable as a direct call or decorator.

    The drop-in seam for a plugin to add a new zero-token triage rule without
    editing this module. Order matters — reflexes are tried in registration
    order and the first non-``None`` result wins, so a new reflex meant to run
    before ``"default"`` must be registered before it (the built-ins reserve
    ``"default"`` as the catch-all, always registered last).
    """
    return REFLEXES.register(name, fn, override=override)


# ── built-in reflexes ───────────────────────────────────────────────────────────

# Pulls a dollar amount out of the "job N via H: estado ($X.XXXX)" summary string
# built by watchlist._from_finished_jobs. Absent/unparseable → treated as unknown
# cost (conservatively routed to judgment, not skipped).
_COST_RE = re.compile(r"\$([0-9]*\.?[0-9]+)")

# A job cost at or below this is "near-zero" for the auto-triage skip.
_NEAR_ZERO_COST = 0.01


def _job_review(trigger: "Trigger") -> "Optional[PolicyDecision]":
    """A cleanly finished job needs no review; a failed/expensive one needs judgment."""
    if trigger.kind != "job" or trigger.action != "job_review":
        return None

    params = trigger.params or {}
    estado = str(params.get("estado") or "").strip().lower()
    summary = str(params.get("summary") or "")

    error_signal = (
        "error" in estado or "fail" in estado
        or "error" in summary.lower() or "fail" in summary.lower()
    )

    cost: Optional[float] = None
    m = _COST_RE.search(summary)
    if m:
        try:
            cost = float(m.group(1))
        except ValueError:
            cost = None

    clean = (
        estado in ("done", "success", "ok")
        and not error_signal
        and cost is not None
        and cost <= _NEAR_ZERO_COST
    )
    if clean:
        return PolicyDecision(
            kind="skip",
            context_tier="minimal",
            hard=False,
            reason="auto-triaged: job finished clean",
        )
    return PolicyDecision(
        kind="think",
        context_tier="full",
        hard=True,
        reason=f"job_review: needs attention (estado={estado!r}, cost={cost})",
    )


def _parse_csv(raw: str) -> set[str]:
    return {p.strip() for p in (raw or "").split(",") if p.strip()}


def _repo_changed(trigger: "Trigger") -> "Optional[PolicyDecision]":
    """A repo on the auto-sync allowlist reconciles itself; anything else thinks."""
    if trigger.kind != "repo" or trigger.action != "repo_changed":
        return None

    auto_repos = _parse_csv(getattr(Config, "auto_sync_repos", "") or "")
    repo_path = trigger.objetivo or ""
    if repo_path and repo_path in auto_repos:
        return PolicyDecision(
            kind="action",
            action="sync_repo",
            params={"repo_dir": repo_path},
            reason=f"auto-sync: {repo_path!r} is in auto_sync_repos",
        )
    return PolicyDecision(
        kind="think",
        context_tier="minimal",
        hard=False,
        reason="repo_changed: not on the auto-sync allowlist, needs judgment",
    )


def _health_alert(trigger: "Trigger") -> "Optional[PolicyDecision]":
    """A 'warn' digest just gets notified; an 'alert' digest needs judgment."""
    if trigger.kind != "health" or trigger.action != "health_alert":
        return None

    params = trigger.params or {}
    level = str(params.get("level") or "").strip().lower()
    summary = str(params.get("summary") or "")[:300]

    if level == "warn":
        return PolicyDecision(
            kind="action",
            action="notify",
            params={"text": f"Health warning: {summary}", "level": "warning"},
            reason="health warn: notified directly, no judgment needed",
        )
    if level == "alert":
        return PolicyDecision(
            kind="think",
            context_tier="full",
            hard=True,
            reason="health alert: needs judgment",
        )
    # Neither warn nor alert (shouldn't normally fire, watchlist only emits on a
    # transition into warn/alert) — fall through to the default reflex.
    return None


def _default(trigger: "Trigger") -> "Optional[PolicyDecision]":
    """The catch-all: think, with a minimal context budget (cheapest safe default)."""
    return PolicyDecision(
        kind="think",
        context_tier="minimal",
        hard=False,
        reason="default: no reflex matched, routine minimal-context think",
    )


register_reflex("job_review", _job_review)
register_reflex("repo_changed", _repo_changed)
register_reflex("health_alert", _health_alert)
register_reflex("default", _default)  # always last — the catch-all


# ── the safe fallback (used when evaluation itself errors) ────────────────────

_SAFE_FALLBACK = PolicyDecision(
    kind="think",
    context_tier="minimal",
    hard=False,
    reason="policy evaluation degraded (error) — safe default: think/minimal",
)


def evaluate(trigger: "Trigger") -> PolicyDecision:
    """Run the reflexes in order; the first non-``None`` verdict wins.

    NEVER raises: an individual reflex that errors is skipped (logged at DEBUG)
    so a bug in one rule can't take down the tick; if evaluation itself blows up
    unexpectedly the safe fallback (think/minimal, not hard) is returned.
    """
    try:
        for name in REFLEXES.names():
            fn = REFLEXES.get_or_none(name)
            if fn is None:
                continue
            try:
                decision = fn(trigger)
            except Exception as exc:
                logger.debug(f"policy: reflex {name!r} errored, skipping: {exc}")
                continue
            if decision is not None:
                return decision
    except Exception as exc:  # belt-and-suspenders: policy must never crash step()
        logger.warning(f"policy: evaluate() failed, degrading to safe default: {exc}")
        return _SAFE_FALLBACK
    # No reflex matched at all (shouldn't happen — "default" always matches).
    return _SAFE_FALLBACK
