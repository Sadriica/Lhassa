"""
Objective verification gates — the árbitro's *correctness* layer (§2.8).

A ``Verifier`` answers one question about an artifact: *is it good enough?* — and
it answers **objectively, NUNCA con un LLM**. Every built-in here is pure standard
library + subprocess + the LLM-free :mod:`lhassa.health` module. None of them
imports or calls a provider, gateway, or cloud model. That invariant is the whole
point of the Phase-3 DoD ("el verificador nunca llama a un LLM"): the thing that
checks the model's work must not itself depend on a model.

The same ``Verifier`` Protocol is reused by the Phase-5 self-improvement gates
(§2.8): *correct* (tests/lint/command exit 0) and *better* (a metric beats a
baseline) are exactly the ``TestsVerifier`` / ``LintVerifier`` / ``CommandVerifier``
and ``MetricVerifier`` built-ins below.

Async shape
-----------
``Verifier.verify`` is **synchronous**. Verification is either pure CPU (regex,
metric) or a blocking subprocess / health collection — none of it needs an event
loop, and a sync method is what :func:`lhassa.llm.router.cascade` wants for its
``verify`` callable (it calls ``verify(completion)`` directly). Async callers
(``core.step``) just call it inline; the subprocess built-ins block only for the
duration of the command they run, with a timeout. There is deliberately no
``averify`` — a single sync shape keeps the Protocol honest and the cascade wiring
trivial.

Each built-in that shells out does so through a ``_run(cmd)`` seam so tests can
replace the subprocess without spawning anything.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from loguru import logger

from lhassa.registry import Registry

# Cap on captured command output stored in a result detail.
_OUTPUT_CAP = 2000


@dataclass
class VerifyResult:
    """The verdict of one verification: did it pass, why, and an optional metric."""

    passed: bool
    detail: str = ""
    metric: Optional[float] = None

    def __bool__(self) -> bool:  # ergonomic: `if result:` ≡ `if result.passed:`
        return self.passed


@runtime_checkable
class Verifier(Protocol):
    """An objective gate over an artifact. NEVER calls an LLM/provider/gateway."""

    name: str

    def verify(self, artifact, context=None) -> VerifyResult: ...


# ── helpers ──────────────────────────────────────────────────────────────────


def _truncate(text: str, cap: int = _OUTPUT_CAP) -> str:
    """Clamp captured output so a noisy command can't bloat an episode."""
    text = text or ""
    if len(text) <= cap:
        return text
    return text[:cap] + f"… (+{len(text) - cap} chars)"


def _shell(cmd, *, cwd=None, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a command, capturing output. ``str`` cmd → shell; ``list`` → argv."""
    return subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ── command / tests / lint (exit-code gates) ─────────────────────────────────


class CommandVerifier:
    """Run a shell command; pass iff it exits 0. Detail = captured output."""

    name = "command"

    def __init__(self, cmd, *, cwd=None, timeout: int = 300):
        self.cmd = cmd
        self.cwd = cwd
        self.timeout = timeout

    def _run(self, cmd) -> subprocess.CompletedProcess:
        """The subprocess seam — mock this in tests."""
        return _shell(cmd, cwd=self.cwd, timeout=self.timeout)

    def verify(self, artifact=None, context=None) -> VerifyResult:
        try:
            proc = self._run(self.cmd)
        except (OSError, subprocess.SubprocessError) as exc:
            return VerifyResult(False, f"{self.name}: command error: {exc}")
        output = _truncate((proc.stdout or "") + (proc.stderr or ""))
        detail = f"exit={proc.returncode}\n{output}".strip()
        return VerifyResult(proc.returncode == 0, detail)


class TestsVerifier(CommandVerifier):
    """Correctness gate — the test suite must pass (exit 0)."""

    name = "tests"

    def __init__(
        self,
        cmd="uv run --extra dev python -m pytest -q",
        cwd=None,
        timeout: int = 600,
    ):
        super().__init__(cmd, cwd=cwd, timeout=timeout)


class LintVerifier(CommandVerifier):
    """Style gate — the linter must be clean (exit 0)."""

    name = "lint"

    def __init__(
        self,
        cmd="uv run --extra dev python -m ruff check .",
        cwd=None,
        timeout: int = 300,
    ):
        super().__init__(cmd, cwd=cwd, timeout=timeout)


# ── health (reuses the LLM-free health module) ───────────────────────────────


class HealthVerifier:
    """Pass iff the PC-state digest is ok (worst level not in {warn, alert})."""

    name = "health"

    def __init__(self, *, notify: bool = False, out_dir=None):
        self.notify = notify
        self.out_dir = out_dir

    def _run(self, cmd=None) -> dict:
        """The collection seam — runs the LLM-free health checks."""
        from lhassa.health import checks

        return checks.run_all(out_dir=self.out_dir, notify=self.notify)

    def verify(self, artifact=None, context=None) -> VerifyResult:
        try:
            digest = self._run()
        except Exception as exc:  # a health collector must never crash the gate
            return VerifyResult(False, f"health: error: {exc}")
        worst = digest.get("worst_level", "info")
        passed = bool(digest.get("ok")) and worst not in ("warn", "alert")
        return VerifyResult(passed, f"worst_level={worst} ok={digest.get('ok')}")


# ── regex (the artifact string must / must not match) ────────────────────────


class RegexVerifier:
    """The artifact (a string) must match ``pattern`` — or must NOT, if asked."""

    name = "regex"

    def __init__(self, pattern: str, *, must_match: bool = True, flags: int = 0):
        self.pattern = re.compile(pattern, flags)
        self.must_match = must_match

    def verify(self, artifact, context=None) -> VerifyResult:
        text = artifact if isinstance(artifact, str) else str(artifact)
        found = self.pattern.search(text) is not None
        passed = found if self.must_match else not found
        want = "match" if self.must_match else "no-match"
        detail = f"pattern={self.pattern.pattern!r} want={want} found={found}"
        return VerifyResult(passed, detail)


# ── metric (beats a baseline — the Phase-5 "mejor" gate) ─────────────────────


class MetricVerifier:
    """Pass iff a measured metric beats ``baseline`` (the §2.8 "mejor" gate).

    ``fn_or_value`` is either a fixed number or a callable measuring the artifact
    (tried as ``fn(artifact, context)`` then ``fn(artifact)``). With
    ``higher_is_better`` the metric must exceed the baseline; otherwise it must
    fall below it.
    """

    name = "metric"

    def __init__(self, fn_or_value, baseline: float, *, higher_is_better: bool = True):
        self.fn_or_value = fn_or_value
        self.baseline = baseline
        self.higher_is_better = higher_is_better

    def _measure(self, artifact, context) -> float:
        val = self.fn_or_value
        if callable(val):
            try:
                val = val(artifact, context)
            except TypeError:
                val = val(artifact)
        return float(val)

    def verify(self, artifact=None, context=None) -> VerifyResult:
        try:
            metric = self._measure(artifact, context)
        except Exception as exc:
            return VerifyResult(False, f"metric: error: {exc}", None)
        if self.higher_is_better:
            passed = metric > self.baseline
        else:
            passed = metric < self.baseline
        cmp = ">" if self.higher_is_better else "<"
        detail = f"metric={metric} {cmp} baseline={self.baseline} → {passed}"
        return VerifyResult(passed, detail, metric)


# ── dispatcher ───────────────────────────────────────────────────────────────

# The single source of truth mapping a spec ``type`` to its Verifier class lives
# in the shared keystone ``Registry`` (the same primitive that backs actions/
# channels/commands). A plugin can drop in a new objective gate with
# ``@register_verifier("x")`` WITHOUT editing this file — the elastic/modular
# principle — and a duplicate type is rejected instead of silently clobbering.
VERIFIERS: Registry[Callable[..., Verifier]] = Registry("verifier")


def register_verifier(name: str, cls: Optional[Callable[..., Verifier]] = None, *, override: bool = False):
    """Register a Verifier class under the spec ``type`` ``name``.

    Usable as a **decorator** (``@register_verifier("x")`` on a Verifier class) or a
    **direct call** (``register_verifier("x", cls)``) — a thin wrapper over
    :data:`VERIFIERS`. A duplicate ``name`` raises unless ``override=True``. This is
    the drop-in seam for adding an objective gate without touching core.
    """
    return VERIFIERS.register(name, cls, override=override)


# Register the built-in gates through the seam (insertion order preserved).
register_verifier("command", CommandVerifier)
register_verifier("tests", TestsVerifier)
register_verifier("lint", LintVerifier)
register_verifier("health", HealthVerifier)
register_verifier("regex", RegexVerifier)
register_verifier("metric", MetricVerifier)

# Backward-compatible alias: a LIVE, read-only ``{type: class}`` view of the
# registry's backing store, so any historical consumer doing ``_REGISTRY.get(typ)``
# keeps working while later ``register_verifier`` calls are reflected.
_REGISTRY = VERIFIERS.as_mapping()


def make_verifier(spec: dict) -> Verifier:
    """Build a Verifier from a spec like ``{"type": "command", "cmd": "..."}``.

    The remaining keys are passed as keyword arguments to the matching class.
    Raises ``ValueError`` for an unknown/absent type or bad arguments.
    """
    params = dict(spec or {})
    typ = params.pop("type", None)
    cls = VERIFIERS.get_or_none(typ)
    if cls is None:
        raise ValueError(f"unknown verifier type: {typ!r}")
    try:
        return cls(**params)
    except TypeError as exc:
        raise ValueError(f"bad arguments for verifier {typ!r}: {exc}") from exc


def verify(artifact, *, spec: dict, context=None) -> VerifyResult:
    """Build the verifier named by ``spec`` and run it over ``artifact``.

    Degrades gracefully: a bad spec or a verifier that raises becomes a failed
    ``VerifyResult`` — this function never propagates an exception, so a caller
    can treat "could not verify" as "did not pass" without a try/except.
    """
    try:
        verifier = make_verifier(spec)
    except Exception as exc:
        logger.debug("verify: could not build verifier from {}: {}", spec, exc)
        return VerifyResult(False, f"verifier setup error: {exc}")
    try:
        return verifier.verify(artifact, context)
    except Exception as exc:  # objective gates must never crash the step loop
        logger.debug("verify: verifier {} raised: {}", spec, exc)
        return VerifyResult(False, f"verify error: {exc}")
