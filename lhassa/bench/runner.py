# SPDX-License-Identifier: AGPL-3.0-or-later
"""LhassaBench; run fixtures against subjects, score with the real árbitro.

The whole point of a benchmark for a brain that routes between providers/
harnesses is closing the loop with hard numbers: this module runs each
fixture's prompt through each named *subject* (today: a
:class:`~lhassa.llm.provider.ModelProvider`; the same shape would fit a route
or a loop config later) and scores the completion with the SAME objective
verifier the rest of Lhassa trusts (:mod:`lhassa.agents.verify`: never an
LLM judge, per the standing invariant). Every scored row is appended to
``bench_runs`` (:mod:`lhassa.core.db`) so the CLI/dashboard/digest can read
history without re-running anything.

Degrades, never raises: an unresolvable/not-installed subject yields an
honest failed row (mirrors :func:`lhassa.llm.router.fallback_chain`'s skip
discipline); a provider call that raises, times out, or a verifier that
raises all become a failed row with the exception text in ``detail``: never
propagate out of :func:`run_bench`. This is a bench, not a critical path, but
the same "never crash the loop" discipline applies.
"""

from __future__ import annotations

import shutil
import time
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from lhassa.agents import verify as verify_agent
from lhassa.bench.loader import load_fixtures, resolve_verify_spec
from lhassa.llm.router import CLI_PROVIDERS

# Bound on how much a bench completion is allowed to cost in tokens; fixtures
# are short-answer by design, so this stays small on purpose.
DEFAULT_MAX_TOKENS = 256

CompleteFn = Callable[..., Awaitable[Any]]


def _binary_for(subject: str, config: Any) -> str:
    return (getattr(config, f"{subject}_bin", "") or "") or subject


def subject_available(subject: str, config: Any) -> bool:
    """Mirror ``fallback_chain``'s skip discipline: a CLI subject needs its
    binary on PATH; anything else is assumed reachable (resolved lazily).

    Public (no leading underscore); the CLI's default-subject picker
    (:mod:`lhassa.cli.bench_cmds`) reuses this exact check.
    """
    if subject in CLI_PROVIDERS:
        return shutil.which(_binary_for(subject, config)) is not None
    return True


def _resolve_subject(subject: str, config: Any):
    """Best-effort provider resolve. Returns ``None`` on any failure."""
    try:
        from lhassa.llm.provider import get_model_provider

        return get_model_provider(config, name=subject)
    except Exception as exc:  # unknown/misconfigured factory
        logger.debug("bench: could not resolve subject {}: {}", subject, exc)
        return None


def _row(
    *, fixture_id: str, subject: str, instance_id: str, passed: bool,
    metric: Optional[float] = None, detail: str = "", cost_usd: float = 0.0,
    duration_ms: Optional[int] = None,
) -> dict:
    from lhassa.core import db

    return {
        "id": db._new_id("bench"),
        "instance_id": instance_id,
        "fixture_id": fixture_id,
        "subject": subject,
        "passed": passed,
        "metric": metric,
        "detail": detail,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "created_at": db._now(),
    }


async def _default_complete_fn(provider, prompt: str, *, max_tokens: int) -> Any:
    messages = [{"role": "user", "content": prompt}]
    return await provider.complete(messages, max_tokens=max_tokens)


async def run_bench(
    subjects: list[str],
    *,
    fixtures: Optional[list[str]] = None,
    config: Any = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    complete_fn: Optional[CompleteFn] = None,
    persist: bool = True,
) -> list[dict]:
    """Run every fixture x subject pair; return the scored rows (also persisted).

    ``fixtures``: a list of fixture ids to restrict to (default: every
    bundled fixture). ``complete_fn(provider, prompt, *, max_tokens) ->
    Completion-like`` is the injectable seam offline tests use to avoid any
    real provider/network call; it defaults to calling ``provider.complete``
    directly. ``persist=False`` skips the DB write (unit-testing the scoring
    logic without a DB fixture wired up).

    A subject that cannot be resolved (unknown name, or a CLI subject whose
    binary is not on PATH) produces ONE failed row per fixture explaining why,
    instead of raising or silently dropping the subject; the caller always
    gets a complete, honest picture.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()
    complete = complete_fn or _default_complete_fn
    instance_id = getattr(config, "instance_id", "inst_personal")

    try:
        fixture_rows = load_fixtures(fixtures)
    except Exception as exc:  # a malformed fixture must not abort the whole run
        logger.warning("bench: could not load fixtures: {}", exc)
        fixture_rows = []

    results: list[dict] = []
    for subject in subjects:
        available = subject_available(subject, config)
        provider = _resolve_subject(subject, config) if available else None
        for fixture in fixture_rows:
            row = await _run_one(
                fixture=fixture,
                subject=subject,
                provider=provider,
                available=available,
                instance_id=instance_id,
                config=config,
                max_tokens=max_tokens,
                complete=complete,
            )
            results.append(row)
            if persist:
                await _persist(row)
    return results


async def _persist(row: dict) -> None:
    try:
        from lhassa.core import db

        await db.insert_bench_run(row)
    except Exception as exc:  # storage must never take down a bench run
        logger.debug("bench: could not persist row {}: {}", row.get("id"), exc)


async def _run_one(
    *, fixture: dict, subject: str, provider, available: bool, instance_id: str,
    config: Any, max_tokens: int, complete: CompleteFn,
) -> dict:
    fixture_id = fixture.get("id", "?")
    if not available:
        return _row(
            fixture_id=fixture_id, subject=subject, instance_id=instance_id,
            passed=False, detail=f"subject {subject!r}: CLI binary not installed",
        )
    if provider is None:
        return _row(
            fixture_id=fixture_id, subject=subject, instance_id=instance_id,
            passed=False, detail=f"subject {subject!r}: could not resolve a provider",
        )

    prompt = fixture.get("prompt", "")
    start = time.monotonic()
    try:
        completion = await complete(provider, prompt, max_tokens=max_tokens)
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return _row(
            fixture_id=fixture_id, subject=subject, instance_id=instance_id,
            passed=False, detail=f"provider error: {exc}", duration_ms=duration_ms,
        )
    duration_ms = int((time.monotonic() - start) * 1000)
    text = getattr(completion, "text", "") or ""
    cost_usd = float(getattr(completion, "cost_usd", 0.0) or 0.0)

    try:
        spec = resolve_verify_spec(fixture.get("verify") or {})
    except Exception as exc:
        return _row(
            fixture_id=fixture_id, subject=subject, instance_id=instance_id,
            passed=False, detail=f"bad verify spec: {exc}", cost_usd=cost_usd,
            duration_ms=duration_ms,
        )

    # ``verify_agent.verify`` is THE scoring seam. LOCKED, no bypass: this is
    # the one and only place a completion is judged, and it is always the real
    # LLM-free árbitro (never an LLM judge). It normally never raises (it
    # degrades to a failed VerifyResult itself), but the call is still guarded
    # here so a hostile/broken verifier can never crash a bench run either.
    try:
        result = verify_agent.verify(text, spec=spec)
    except Exception as exc:
        return _row(
            fixture_id=fixture_id, subject=subject, instance_id=instance_id,
            passed=False, detail=f"scorer error: {exc}", cost_usd=cost_usd,
            duration_ms=duration_ms,
        )
    return _row(
        fixture_id=fixture_id, subject=subject, instance_id=instance_id,
        passed=bool(result.passed), metric=result.metric, detail=result.detail,
        cost_usd=cost_usd, duration_ms=duration_ms,
    )


# ── summary ────────────────────────────────────────────────────────────────

def summarize(rows: list[dict]) -> str:
    """A compact per-subject table: subject, n, pass-rate, avg metric, cost.

    Deterministic, $0, plain text; usable from the CLI (wrapped in a Rich
    table) or dropped straight into a digest. Rows with no metric are simply
    excluded from the metric average; a subject with zero rows never appears.
    """
    by_subject: dict[str, list[dict]] = {}
    for row in rows:
        by_subject.setdefault(row.get("subject", "?"), []).append(row)

    if not by_subject:
        return "LhassaBench: no rows."

    lines = [f"{'subject':<12} {'n':>4} {'pass%':>7} {'avg_metric':>11} {'cost_usd':>10}"]
    for subject in sorted(by_subject):
        subject_rows = by_subject[subject]
        n = len(subject_rows)
        passed = sum(1 for r in subject_rows if r.get("passed"))
        pass_pct = (passed / n * 100.0) if n else 0.0
        metrics = [r["metric"] for r in subject_rows if r.get("metric") is not None]
        avg_metric = (sum(metrics) / len(metrics)) if metrics else None
        total_cost = sum(float(r.get("cost_usd") or 0.0) for r in subject_rows)
        avg_metric_str = f"{avg_metric:.2f}" if avg_metric is not None else "—"
        lines.append(
            f"{subject:<12} {n:>4} {pass_pct:>6.1f}% {avg_metric_str:>11} {total_cost:>10.4f}"
        )
    return "\n".join(lines)
