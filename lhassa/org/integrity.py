# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integrity Watcher; deterministic conformance to Lhassa/Witara contracts.

This is not another reasoning agent. Each versioned contract returns observable
evidence and a boolean verdict without an LLM. Runs/checks/findings are durable;
a passing run mints a short-lived attestation used by dangerous transitions.

The monitor watcher owns only the cadence. Scheduled passes enter through the
closed ``temple_run(check_integrity)`` action, execute as the ``guardian`` monk,
and deliver upward through Temple to Lhassa. Manual CLI/API runs remain direct
diagnostic requests by the owner.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.conformance import MANIFEST_VERSION, attestation as attestation



@dataclass(frozen=True)
class Contract:
    id: str
    version: int
    severity: str
    description: str
    check: Callable[[Config, str], Awaitable[dict]]


def _result(passed: bool, summary: str, **evidence) -> dict:
    return {"passed": passed, "summary": summary, "evidence": evidence}


async def _db_schema(config: Config, instance_id: str) -> dict:
    from lhassa._vendor import aiosqlite
    from lhassa.core.migrations import get_schema_version

    async with aiosqlite.connect(db.DB_PATH) as conn:
        actual = await get_schema_version(conn)
    return _result(
        actual == db.SCHEMA_VERSION,
        f"schema {actual}/{db.SCHEMA_VERSION}",
        actual=actual,
        expected=db.SCHEMA_VERSION,
    )


async def _research_event_chain(config: Config, instance_id: str) -> dict:
    rows = await db.list_idea_assessments(instance_id, limit=500)
    violations: list[dict] = []
    allowed_deep = {"research", "experiment"}
    for row in rows:
        events = await db.list_idea_assessment_events(instance_id, row["id"])
        if not events:
            violations.append({"assessment_id": row["id"], "reason": "no events"})
            continue
        previous = None
        for event in events:
            if event.get("from_status") != previous:
                violations.append(
                    {
                        "assessment_id": row["id"],
                        "reason": "non-contiguous event stream",
                        "stage": event.get("stage"),
                        "expected_from": previous,
                        "actual_from": event.get("from_status"),
                    }
                )
                break
            previous = event.get("to_status")
        if previous != row.get("status"):
            violations.append(
                {
                    "assessment_id": row["id"],
                    "reason": "projection does not match event stream",
                    "event_status": previous,
                    "projection_status": row.get("status"),
                }
            )
        if (
            row.get("research_run_id") or row.get("draft_path")
        ) and row.get("decision") not in allowed_deep:
            violations.append(
                {
                    "assessment_id": row["id"],
                    "reason": "deep research without earned disposition",
                    "decision": row.get("decision"),
                }
            )
    return _result(
        not violations,
        (
            f"{len(rows)} assessment stream(s) contiguous"
            if not violations
            else f"{len(violations)} research-log violation(s)"
        ),
        assessments=len(rows),
        violations=violations[:50],
    )


async def _research_artifacts(config: Config, instance_id: str) -> dict:
    rows = await db.list_artifacts(instance_id, limit=500)
    relevant = [
        row
        for row in rows
        if row.get("kind")
        in {
            "idea_assessment",
            "research_corpus",
            "product_change",
            "product_change_candidate",
        }
    ]
    missing: list[dict] = []
    for row in relevant:
        for raw in row.get("paths") or []:
            if not raw or not Path(str(raw)).exists():
                missing.append({"artifact_id": row["id"], "path": raw})
    return _result(
        not missing,
        (
            f"{len(relevant)} research/product artifact(s) present"
            if not missing
            else f"{len(missing)} artifact path(s) missing"
        ),
        artifacts=len(relevant),
        missing=missing[:50],
    )


async def _canonical_research_flows(config: Config, instance_id: str) -> dict:
    from lhassa.flows.store import FlowStore
    from lhassa.seeds.research import (
        DEFAULT_ASSESSMENT_FLOW_ID,
        DEFAULT_RESEARCH_FLOW_ID,
        _ASSESSMENT_FLOW_JSON,
        _canonical_flow,
    )

    store = FlowStore(config)
    expected = {
        getattr(config, "research_assessment_flow_id", None)
        or DEFAULT_ASSESSMENT_FLOW_ID: _ASSESSMENT_FLOW_JSON,
        getattr(config, "research_flow_id", None)
        or DEFAULT_RESEARCH_FLOW_ID: None,
    }
    drift: list[dict] = []
    for flow_id, source in expected.items():
        actual = store.get(flow_id)
        canonical = (
            _canonical_flow(flow_id, source)
            if source is not None
            else _canonical_flow(flow_id)
        )
        if actual is None:
            drift.append({"flow_id": flow_id, "reason": "missing"})
        elif actual.to_dict() != canonical.to_dict():
            drift.append({"flow_id": flow_id, "reason": "drifted"})
    return _result(
        not drift,
        # A finding must NAME its remedy: this exact drift alerted the owner
        # hourly for three days while the fix was one idempotent command the
        # summary never mentioned. Counsel, not control; say what to type.
        "research flows canonical"
        if not drift
        else f"{len(drift)} flow drift(s); run `lhassa seed apply research` to restore",
        drift=drift,
    )


async def _product_adoption_chain(config: Config, instance_id: str) -> dict:
    root = Path(config.adoption_dir).expanduser().resolve()
    proposals = sorted(root.glob("chg_*/proposal.json")) if root.exists() else []
    violations: list[dict] = []
    for path in proposals:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            repo = Path(record["project"]["repo"]).resolve()
            workspace = Path(record.get("workspace") or root).resolve()
            if record.get("workspace") and (
                workspace == repo or repo in workspace.parents
            ):
                violations.append(
                    {"proposal_id": record.get("id"), "reason": "workspace inside product"}
                )
            exp_id = record.get("experiment_approval_id")
            exp = await db.get_approval(instance_id, exp_id) if exp_id else None
            if record.get("status") != "awaiting_experiment_approval" and (
                not exp or exp.get("estado") != "aprobada"
            ):
                violations.append(
                    {"proposal_id": record.get("id"), "reason": "experiment not approved"}
                )
            if record.get("status") in {"awaiting_integration_approval", "integrated"}:
                verification = record.get("verification") or {}
                if not verification.get("ok") or verification.get("isolation") == "none":
                    violations.append(
                        {"proposal_id": record.get("id"), "reason": "unisolated candidate"}
                    )
                int_id = record.get("integration_approval_id")
                integration = (
                    await db.get_approval(instance_id, int_id) if int_id else None
                )
                if record.get("status") == "integrated" and (
                    not integration or integration.get("estado") != "aprobada"
                ):
                    violations.append(
                        {"proposal_id": record.get("id"), "reason": "integration not approved"}
                    )
        except Exception as exc:
            violations.append({"path": str(path), "reason": f"invalid record: {exc}"})
    return _result(
        not violations,
        (
            f"{len(proposals)} adoption record(s) respect the chain"
            if not violations
            else f"{len(violations)} adoption-chain violation(s)"
        ),
        proposals=len(proposals),
        violations=violations[:50],
    )


async def _quarantine_backend(config: Config, instance_id: str) -> dict:
    from lhassa.security.quarantine import _select_isolation

    with tempfile.TemporaryDirectory(prefix="lhassa_integrity_") as tmp:
        _, isolation = _select_isolation(Path(tmp))
    return _result(
        isolation != "none",
        f"quarantine isolation={isolation}",
        isolation=isolation,
    )


async def _approval_age(config: Config, instance_id: str) -> dict:
    rows = await db.load_pending_approvals(instance_id)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    stale = []
    for row in rows:
        try:
            created = datetime.fromisoformat(
                str(row.get("creado_en") or "").replace("Z", "+00:00")
            )
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                stale.append({"id": row["id"], "tipo": row.get("tipo")})
        except ValueError:
            stale.append({"id": row.get("id"), "tipo": row.get("tipo")})
    return _result(
        not stale,
        f"{len(stale)} approval(s) stale over 72h" if stale else "no stale approvals",
        stale=stale,
    )


async def _security_governance(config: Config, instance_id: str) -> dict:
    required = {"move_money", "delete_data"}
    configured = set(getattr(config, "always_approve_actions", set()) or set())
    missing = sorted(required - configured)
    threshold = int(getattr(config, "autonomy_threshold", 0) or 0)
    integration_enforced = bool(
        getattr(config, "integrity_enforce_product_changes", False)
    )
    passed = not missing and threshold >= 1 and integration_enforced
    return _result(
        passed,
        (
            "high-risk approvals and autonomy threshold intact"
            if passed
            else "security governance configuration drifted"
        ),
        required_always_approve=sorted(required),
        configured_always_approve=sorted(configured),
        missing=missing,
        autonomy_threshold=threshold,
        integrity_enforce_product_changes=integration_enforced,
    )


async def _capability_wiring(config: Config, instance_id: str) -> dict:
    from lhassa import actionpack
    from lhassa.agents.verify import VERIFIERS
    from lhassa.core.actions import ACTIONS

    actionpack.register_all()
    required_actions = {
        "research_intake",
        "draft_research_note",
        "org_run",
        "temple_run",
        "review_tools",
    }
    required_verifiers = {
        "idea_assessment",
        "research_corpus",
        "command",
        "metric",
    }
    missing_actions = sorted(required_actions - set(ACTIONS))
    missing_verifiers = sorted(required_verifiers - set(VERIFIERS.names()))
    passed = not missing_actions and not missing_verifiers
    return _result(
        passed,
        (
            "core governed capabilities are wired"
            if passed
            else "required action/verifier wiring is missing"
        ),
        missing_actions=missing_actions,
        missing_verifiers=missing_verifiers,
        actions_registered=len(ACTIONS),
        verifiers_registered=len(VERIFIERS.names()),
    )


async def _architecture_boundaries(config: Config, instance_id: str) -> dict:
    from lhassa.org.integrity_architecture import scan

    violations = scan()
    return _result(
        not violations,
        (
            "runtime import boundaries intact"
            if not violations
            else f"{len(violations)} forbidden import edge(s)"
        ),
        violations=violations[:100],
    )


CONTRACTS = (
    Contract("db.schema.current", 1, "critical", "DB schema is supported.", _db_schema),
    Contract(
        "research.event_chain",
        1,
        "critical",
        "Research projection is reconstructible from its event stream.",
        _research_event_chain,
    ),
    Contract(
        "research.artifacts.present",
        1,
        "warn",
        "Research and adoption artifact paths remain present.",
        _research_artifacts,
    ),
    Contract(
        "research.flows.canonical",
        1,
        "critical",
        "Theory assessment and corpus flows match the method.",
        _canonical_research_flows,
    ),
    Contract(
        "product.adoption.chain",
        1,
        "critical",
        "Product candidates preserve isolation and both approvals.",
        _product_adoption_chain,
    ),
    Contract(
        "security.quarantine.available",
        1,
        "critical",
        "An enforceable quarantine backend is available.",
        _quarantine_backend,
    ),
    Contract(
        "security.governance.config",
        1,
        "critical",
        "High-risk approvals cannot be learned away.",
        _security_governance,
    ),
    Contract(
        "runtime.capabilities.wired",
        1,
        "critical",
        "Research, org, temple and objective-gate seams are registered.",
        _capability_wiring,
    ),
    Contract(
        "architecture.import_boundaries",
        1,
        "critical",
        "Inner layers do not reach into world-facing shells.",
        _architecture_boundaries,
    ),
    Contract(
        "operations.approvals.fresh",
        1,
        "warn",
        "Pending approvals are not silently abandoned.",
        _approval_age,
    ),
)


async def run_integrity(
    config: Config | None = None, *, trigger: str = "manual"
) -> dict:
    config = config or Config()
    instance_id = getattr(config, "instance_id", None) or Config.instance_id
    await db.init_db()
    run_id = await db.start_conformance_run(
        instance_id, manifest_version=MANIFEST_VERSION, trigger=trigger
    )
    results = []
    warn = critical = passed_count = 0
    for contract in CONTRACTS:
        started = time.perf_counter()
        try:
            result = await contract.check(config, instance_id)
        except Exception as exc:
            result = _result(False, f"check errored: {type(exc).__name__}: {exc}")
        duration_ms = int((time.perf_counter() - started) * 1000)
        passed = bool(result.get("passed"))
        passed_count += int(passed)
        if not passed:
            if contract.severity == "critical":
                critical += 1
            else:
                warn += 1
        await db.record_conformance_check(
            instance_id,
            run_id,
            contract_id=contract.id,
            version=contract.version,
            severity=contract.severity,
            passed=passed,
            summary=str(result.get("summary") or ""),
            evidence=result.get("evidence") or {},
            duration_ms=duration_ms,
        )
        results.append(
            {
                "contract_id": contract.id,
                "version": contract.version,
                "severity": contract.severity,
                "passed": passed,
                "summary": result.get("summary"),
                "evidence": result.get("evidence") or {},
            }
        )
    status = "fail" if critical else ("warn" if warn else "pass")
    expiry = None
    if status == "pass":
        ttl = max(60.0, float(config.integrity_attestation_ttl))
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
    await db.finish_conformance_run(
        instance_id,
        run_id,
        status=status,
        checks_total=len(CONTRACTS),
        checks_passed=passed_count,
        findings_warn=warn,
        findings_critical=critical,
        attestation_expires_at=expiry,
    )
    return {
        "run_id": run_id,
        "status": status,
        "manifest_version": MANIFEST_VERSION,
        "attestation_expires_at": expiry,
        "checks": results,
        "findings_warn": warn,
        "findings_critical": critical,
    }
