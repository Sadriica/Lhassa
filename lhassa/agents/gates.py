# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Las cuatro compuertas de la auto-mejora 2b (§2.8).

A self-written *candidate* graduates into a trusted tool only by passing, **in
order**, the gates of self-improvement 2b; and graduation is *deny-by-default*:
a candidate is rejected unless every gate explicitly passes.

  1. **Segura** : it runs in the cuarentena sandbox (no network, no real data).
  2. **Correcta**: it works: a test/command verifier exits 0.
  3. **Mejor**  : it beats a concrete baseline on a concrete metric
     (tokens/tiempo/pasos). No baseline → cannot prove "better" → fail.
  4. **Retiro** : *continuous*, not inline: a graduated tool that fails in
     production or goes unused is demoted back to cuarentena.

The pipeline stops at the first failed gate (later gates do not run): there is no
point measuring "better" on something that is not even "correct". Gates are
**modular per domain**: the first domain is ``optimization`` (clean metrics).

Reuse, not reinvention: gate 1 is :func:`lhassa.security.quarantine.run_in_quarantine`;
gates 2 and 3 are the existing :mod:`lhassa.agents.verify` ``Verifier`` built-ins
(``CommandVerifier``/``TestsVerifier`` for *correcta*, ``MetricVerifier`` for
*mejor*); the árbitro layer is objective and NEVER calls an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

from loguru import logger

from lhassa.agents import verify
from lhassa.core import db
from lhassa.core.episode import Episode
from lhassa.security.quarantine import run_in_quarantine

# Gate names, in pipeline order. The fourth ("Retiro") is continuous, not inline.
GATE_SEGURA = "1_segura"
GATE_CORRECTA = "2_correcta"
GATE_MEJOR = "3_mejor"

# Gate 4 (Retiro) thresholds. ``[SUPUESTO]``: "went unused" is proxied by a
# graduated tool with zero recorded uses; the supervisor only evaluates a tool
# for retirement after a grace window, so a freshly graduated tool is not retired
# the instant it lands.
RETIRE_FAILURE_RATE = 0.5
RETIRE_MIN_USES = 3

# Default direction for the "mejor" metric. The first domain is optimization, whose
# metrics (tokens/tiempo/pasos) are all *lower is better* → not higher_is_better.
_LOWER_IS_BETTER = False


@dataclass
class GateResult:
    """The verdict of one gate: which gate, did it pass, why, and any metric."""

    gate: str
    passed: bool
    detail: str
    metric: Optional[float] = None


Candidate = Union[str, list]


async def run_gates(
    candidate: Candidate,
    *,
    tests_spec: Optional[dict] = None,
    metric_fn: Optional[Callable] = None,
    baseline: Optional[float] = None,
    domain: str = "optimization",
    quarantine_timeout: int = 30,
) -> list[GateResult]:
    """Run the three inline gates IN ORDER, stopping at the first failure.

    Returns the list of :class:`GateResult` up to and including the gate that
    failed (or all three if every gate passed). A candidate graduates iff every
    returned result passed; deny-by-default, so an empty/short list never means
    "ok by omission".
    """
    results: list[GateResult] = []

    # ── Gate 1: Segura; run it caged (no red/datos). ─────────────────────────
    q = await run_in_quarantine(candidate, timeout=quarantine_timeout)
    g1_ok = q.ok and not q.escaped and not q.timed_out
    detail = (
        f"isolation={q.isolation} exit={q.exit_code} "
        f"timed_out={q.timed_out} escaped={q.escaped}"
    )
    if q.stderr:
        detail += f"\n{q.stderr}"
    results.append(GateResult(GATE_SEGURA, g1_ok, detail.strip()))
    if not g1_ok:
        logger.debug("gates[{}]: failed gate 1 (segura): {}", domain, detail)
        return results

    # ── Gate 2: Correcta; a test/command verifier must exit 0. ───────────────
    if not tests_spec:
        results.append(
            GateResult(
                GATE_CORRECTA,
                False,
                "no tests_spec given: cannot prove 'correcta' (deny-by-default)",
            )
        )
        return results
    vr = verify.verify(candidate, spec=tests_spec)
    results.append(GateResult(GATE_CORRECTA, bool(vr.passed), vr.detail, vr.metric))
    if not vr.passed:
        logger.debug("gates[{}]: failed gate 2 (correcta): {}", domain, vr.detail)
        return results

    # ── Gate 3: Mejor; must beat a concrete baseline on a concrete metric. ───
    if metric_fn is None or baseline is None:
        results.append(
            GateResult(
                GATE_MEJOR,
                False,
                "no metric_fn/baseline: cannot prove 'mejor' (need a baseline)",
            )
        )
        return results
    metric_verifier = verify.MetricVerifier(
        metric_fn, baseline, higher_is_better=_LOWER_IS_BETTER
    )
    mr = metric_verifier.verify(candidate)
    results.append(GateResult(GATE_MEJOR, bool(mr.passed), mr.detail, mr.metric))
    if not mr.passed:
        logger.debug("gates[{}]: failed gate 3 (mejor): {}", domain, mr.detail)
    return results


def _graduated(results: list[GateResult]) -> bool:
    """Deny-by-default: graduate only if all three inline gates ran and passed."""
    gates_seen = {r.gate for r in results}
    required = {GATE_SEGURA, GATE_CORRECTA, GATE_MEJOR}
    return required.issubset(gates_seen) and all(r.passed for r in results)


def _first_failure(results: list[GateResult]) -> Optional[GateResult]:
    for r in results:
        if not r.passed:
            return r
    return None


async def promote(
    instance_id: str,
    tool_name: str,
    candidate: Candidate,
    *,
    domain: str = "optimization",
    tests_spec: Optional[dict] = None,
    metric_fn: Optional[Callable] = None,
    baseline: Optional[float] = None,
    repo_url: Optional[str] = None,
) -> dict[str, Any]:
    """Run the gates and record the outcome on the ``tools`` table + an Episode.

    All gates pass → ``estado='graduada'`` with ``metrica_baseline``/``actual``
    recorded; any gate fails → ``estado='rechazada'`` with the failing reason. An
    Episode is appended either way (observability from the first commit). Returns a
    dict summarising the verdict.
    """
    results = await run_gates(
        candidate,
        tests_spec=tests_spec,
        metric_fn=metric_fn,
        baseline=baseline,
        domain=domain,
    )
    graduated = _graduated(results)
    metric = next((r.metric for r in reversed(results) if r.metric is not None), None)

    if graduated:
        estado = "graduada"
        reason = "passed all gates: segura + correcta + mejor"
        tool_id = await db.upsert_tool(
            instance_id,
            tool_name,
            dominio=domain,
            repo_url=repo_url,
            estado=estado,
            metrica_baseline=baseline,
            metrica_actual=metric,
        )
    else:
        estado = "rechazada"
        failed = _first_failure(results)
        reason = (
            f"failed {failed.gate}: {failed.detail}" if failed else "no gates passed"
        )
        tool_id = await db.upsert_tool(
            instance_id,
            tool_name,
            dominio=domain,
            repo_url=repo_url,
            estado=estado,
            metrica_actual=metric,
        )

    ep = Episode.begin(despertar_por="auto_mejora_2b", instance_id=instance_id)
    ep.dominio = domain
    ep.finish(
        decision=f"promote:{tool_name}",
        accion="run_gates",
        resultado=f"{estado}: {reason}",
    )
    await ep.append()

    return {
        "tool_id": tool_id,
        "tool_name": tool_name,
        "estado": estado,
        "graduated": graduated,
        "reason": reason,
        "metric": metric,
        "baseline": baseline,
        "gates": results,
    }


async def retire(instance_id: str, tool_name: str, reason: str = "") -> dict[str, Any]:
    """Gate 4 (Retiro): demote a graduated tool back to cuarentena.

    Records ``estado='cuarentena'`` on the tool and appends an Episode. Used by the
    supervisor when :func:`should_retire` flags a tool (production failures or
    disuse). The tool can re-enter the pipeline and be re-graduated later.
    """
    tool_id = await db.upsert_tool(instance_id, tool_name, estado="cuarentena")
    ep = Episode.begin(despertar_por="retiro", instance_id=instance_id)
    ep.finish(
        decision=f"retire:{tool_name}",
        accion="degradar",
        resultado=f"cuarentena: {reason}" if reason else "cuarentena",
    )
    await ep.append()
    return {"tool_id": tool_id, "tool_name": tool_name, "estado": "cuarentena", "reason": reason}


def should_retire(tool_row: dict) -> bool:
    """Gate 4 predicate: should this graduated tool be demoted to cuarentena?

    True when a **graduated** tool either went unused (zero recorded uses) or its
    production failure-rate crossed :data:`RETIRE_FAILURE_RATE` over at least
    :data:`RETIRE_MIN_USES` uses. Anything not graduated is never "retired" here.
    """
    if not tool_row or tool_row.get("estado") != "graduada":
        return False
    usos = int(tool_row.get("usos") or 0)
    fallos = int(tool_row.get("fallos") or 0)
    if usos == 0:
        return True  # graduated but never used → went unused
    return usos >= RETIRE_MIN_USES and (fallos / usos) >= RETIRE_FAILURE_RATE
