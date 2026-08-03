# SPDX-License-Identifier: AGPL-3.0-or-later
"""Research-to-product adoption, with two human gates and an external worktree.

Research is knowledge, not authority to mutate a product.  This module makes the
promotion path explicit:

    gated corpus -> experiment approval -> external worktree -> objective gate
    -> integration approval -> merge

The coding agent is launched with the worktree as its workspace and no automated
write targets in the product checkout. Verification runs through the existing
quarantine (scrubbed environment, no network when a namespace backend is
available), and fails closed when only best-effort isolation is available.
Nothing merges automatically.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lhassa.agents.verify import ResearchCorpusVerifier
from lhassa.core import artifacts, db, projects
from lhassa.security import approval
from lhassa.security.quarantine import run_in_quarantine
from lhassa.security.trust import TrustLevel

SCHEMA = "lhassa.product-change/v1"
_OUTPUT_CAP = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:1000]
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _root(config=None) -> Path:
    if config is None:
        from lhassa.config import Config

        config = Config()
    return Path(config.adoption_dir).expanduser().resolve()


def _proposal_path(proposal_id: str, config=None) -> Path:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_"
    if not proposal_id.startswith("chg_") or any(c not in allowed for c in proposal_id):
        raise ValueError("invalid product-change id")
    return _root(config) / proposal_id / "proposal.json"


def _write(record: dict, config=None) -> Path:
    path = _proposal_path(record["id"], config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load(proposal_id: str, config=None) -> dict:
    path = _proposal_path(proposal_id, config)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"unknown product change {proposal_id!r}") from exc
    if record.get("schema") != SCHEMA or record.get("id") != proposal_id:
        raise ValueError("invalid product-change record")
    return record


async def _snapshot(instance_id: str, record: dict, config=None) -> str:
    """Write and register an immutable state snapshot."""
    current = _write(record, config)
    stamp = len(record.get("events") or [])
    snapshot = current.parent / f"{stamp:02d}-{record['status']}.json"
    snapshot.write_bytes(current.read_bytes())
    return await artifacts.register(
        instance_id,
        kind="product_change",
        paths=[snapshot],
        metadata={
            "proposal_id": record["id"],
            "project_id": record["project"]["id"],
            "research_sha256": record["research"]["sha256"],
            "status": record["status"],
            "assessment_id": record.get("assessment_id"),
        },
        origin_department="research",
    )


async def _log_research_line(
    instance_id: str, record: dict, stage: str, **payload
) -> None:
    """Best-effort bridge into the idea's append-only end-to-end event log."""
    assessment_id = record.get("assessment_id")
    if not assessment_id:
        return
    try:
        from lhassa.core import idea_assessments

        await idea_assessments.log_downstream(
            instance_id,
            assessment_id,
            stage=stage,
            payload={"proposal_id": record["id"], **payload},
        )
    except Exception:
        return


def _event(record: dict, state: str, **detail) -> None:
    record["status"] = state
    record["updated_at"] = _now()
    record.setdefault("events", []).append({"at": record["updated_at"], "state": state, **detail})


async def propose(
    instance_id: str,
    *,
    project_ref: str,
    research_path: str,
    objective: str,
    verify_command: str,
    config=None,
) -> dict:
    """Create a traceable adoption proposal and park the experiment approval."""
    project = await projects.resolve_project(instance_id, project_ref)
    if not project or project.get("kind") != "code" or not project.get("path"):
        raise ValueError("the target must be an active code project with a path")
    repo = Path(project["path"]).resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ValueError("the target project path is not a git checkout")

    source = Path(research_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError("research draft does not exist")
    verdict = ResearchCorpusVerifier().verify(source.read_text(encoding="utf-8"))
    if not verdict:
        raise ValueError(f"research gate failed: {verdict.detail}")
    if not objective.strip() or not verify_command.strip():
        raise ValueError("objective and verify command are required")
    linked_assessment = await db.find_idea_assessment_by_draft(
        instance_id, str(source)
    )
    if linked_assessment is not None and linked_assessment.get("status") != "researched":
        raise ValueError("linked idea assessment has not completed full research")

    proposal_id = "chg_" + uuid.uuid4().hex[:12]
    record = {
        "schema": SCHEMA,
        "id": proposal_id,
        "status": "awaiting_experiment_approval",
        "created_at": _now(),
        "updated_at": _now(),
        "project": {"id": project["id"], "name": project["name"], "repo": str(repo)},
        "research": {
            "path": str(source),
            "sha256": _sha256(source),
            "gate": verdict.detail,
        },
        "objective": objective.strip(),
        "verify_command": verify_command.strip(),
        "rollback": "A merged candidate is rolled back with git revert of its merge commit.",
        "assessment_id": (
            linked_assessment.get("id") if linked_assessment is not None else None
        ),
        "events": [],
    }
    _event(record, "awaiting_experiment_approval")
    await _snapshot(instance_id, record, config)
    ap_id = await approval.request_approval(
        instance_id,
        tipo="product_change_experiment",
        payload={
            "accion": "product_change_experiment",
            "proposal_id": proposal_id,
            "project_id": project["id"],
            "project": project["name"],
            "objective": record["objective"],
            "research_path": str(source),
            "research_sha256": record["research"]["sha256"],
            "verify_command": record["verify_command"],
            "actor_id": "research",
            "origen_trust": int(TrustLevel.HUMANO),
        },
    )
    record["experiment_approval_id"] = ap_id
    _write(record, config)
    await _log_research_line(
        instance_id,
        record,
        "product_change_proposed",
        experiment_approval_id=ap_id,
    )
    return record


async def _require_approved(
    instance_id: str,
    approval_id: str,
    expected_type: str,
    proposal_id: str,
) -> dict:
    row = await db.get_approval(instance_id, approval_id)
    if not row or row.get("tipo") != expected_type:
        raise ValueError(f"{expected_type} approval not found")
    payload = row.get("payload") or {}
    if payload.get("proposal_id") != proposal_id:
        raise ValueError("approval belongs to a different product change")
    if row.get("estado") != "aprobada":
        raise ValueError(f"approval {approval_id} is not approved")
    return row


def prepare_workspace(record: dict, config=None) -> Path:
    """Create the candidate branch in an external git worktree, idempotently."""
    repo = Path(record["project"]["repo"]).resolve()
    root = _root(config)
    if root == repo or repo in root.parents or root in repo.parents:
        raise ValueError("LHASSA_ADOPTION_DIR must be outside the product repository")
    workspace = root / record["id"] / "worktree"
    branch = f"lhassa/adopt/{record['id'][4:]}"
    if not workspace.exists():
        root.mkdir(parents=True, exist_ok=True)
        _run_git(repo, "worktree", "add", "-b", branch, str(workspace), "HEAD")
    record["workspace"] = str(workspace)
    record["branch"] = branch
    record.setdefault("base_commit", _run_git(workspace, "rev-parse", "HEAD"))
    return workspace


def build_prompt(record: dict) -> str:
    return (
        "Implement the approved product-change experiment in this isolated git "
        "worktree. Do not access or modify the source checkout, do not merge, "
        "do not publish, and do not weaken tests or security controls.\n\n"
        f"Objective: {record['objective']}\n"
        f"Research corpus (read-only evidence): {record['research']['path']}\n"
        f"Research sha256: {record['research']['sha256']}\n"
        f"Acceptance command: {record['verify_command']}\n\n"
        "Make the smallest reversible change. Leave all candidate files in this "
        "worktree and run the acceptance command before finishing."
    )


async def build(
    instance_id: str,
    proposal_id: str,
    *,
    cli: Optional[str] = None,
    config=None,
) -> dict:
    """Run the approved experiment externally, gate it, and park integration."""
    from lhassa.agents import local_cli
    from lhassa.state import AgentRun, state

    record = load(proposal_id, config)
    if record["status"] != "awaiting_experiment_approval":
        raise ValueError(f"cannot build from state {record['status']!r}")
    await _require_approved(
        instance_id,
        record["experiment_approval_id"],
        "product_change_experiment",
        proposal_id,
    )
    if _sha256(Path(record["research"]["path"])) != record["research"]["sha256"]:
        raise ValueError("research draft changed after the proposal was approved")

    chosen = (cli or local_cli.detect_ai_cli()).strip().lower()
    if not local_cli.is_installed(chosen):
        raise ValueError(f"coding CLI {chosen!r} is not installed")
    workspace = prepare_workspace(record, config)
    _event(record, "experiment_running", workspace=str(workspace))
    await _snapshot(instance_id, record, config)

    agent = local_cli.make_agent(chosen)
    run_id = uuid.uuid4().hex[:8]
    run = AgentRun(
        id=run_id,
        name=f"adoption ({chosen})",
        status="running",
        task=record["objective"],
        cli=chosen,
    )
    state.register_agent(run)
    try:
        output = await agent._invoke(run_id, run, build_prompt(record), workspace)
        state.finish_agent(run_id, output, success=True)
    except Exception as exc:
        state.finish_agent(run_id, str(exc), success=False)
        _event(record, "experiment_failed", error=str(exc)[:1000])
        await _snapshot(instance_id, record, config)
        await _log_research_line(
            instance_id,
            record,
            "product_experiment_failed",
            error=str(exc)[:1000],
        )
        raise

    # The objective gate is deliberately not the coding agent.  It runs with the
    # existing secret-scrubbed quarantine and must have real namespace isolation.
    result = await run_in_quarantine(
        record["verify_command"], timeout=600, workdir=workspace
    )
    record["verification"] = {
        "ok": result.ok,
        "isolation": result.isolation,
        "exit_code": result.exit_code,
        "stdout": result.stdout[-_OUTPUT_CAP:],
        "stderr": result.stderr[-_OUTPUT_CAP:],
    }
    if not result.ok or result.isolation == "none":
        reason = "objective gate failed" if not result.ok else "no enforceable sandbox backend"
        _event(record, "experiment_failed", reason=reason)
        await _snapshot(instance_id, record, config)
        await _log_research_line(
            instance_id, record, "product_experiment_failed", reason=reason
        )
        raise RuntimeError(reason)

    _run_git(workspace, "add", "-A")
    status = _run_git(workspace, "status", "--porcelain")
    if not status:
        _event(record, "experiment_failed", reason="candidate produced no changes")
        await _snapshot(instance_id, record, config)
        await _log_research_line(
            instance_id,
            record,
            "product_experiment_failed",
            reason="candidate produced no changes",
        )
        raise RuntimeError("candidate produced no changes")
    _run_git(
        workspace,
        "-c", "user.name=Lhassa",
        "-c", "user.email=lhassa@localhost",
        "commit", "-m", f"adopt: {record['objective'][:60]}",
    )
    record["candidate_commit"] = _run_git(workspace, "rev-parse", "HEAD")
    record["diff_stat"] = _run_git(
        workspace, "show", "--stat", "--oneline", "--format=short", "HEAD"
    )[-_OUTPUT_CAP:]
    patch_path = _proposal_path(proposal_id, config).parent / "candidate.patch"
    patch_path.write_text(
        _run_git(workspace, "format-patch", "-1", "--stdout", "HEAD"),
        encoding="utf-8",
    )
    record["candidate_patch"] = str(patch_path)
    _event(record, "awaiting_integration_approval", run_id=run_id)
    await _snapshot(instance_id, record, config)
    await artifacts.register(
        instance_id,
        kind="product_change_candidate",
        paths=[patch_path],
        metadata={
            "proposal_id": proposal_id,
            "project_id": record["project"]["id"],
            "candidate_commit": record["candidate_commit"],
            "verification": record["verification"],
        },
        origin_department="research",
    )
    ap_id = await approval.request_approval(
        instance_id,
        tipo="product_change_integration",
        payload={
            "accion": "product_change_integration",
            "proposal_id": proposal_id,
            "project_id": record["project"]["id"],
            "project": record["project"]["name"],
            "objective": record["objective"],
            "candidate_commit": record["candidate_commit"],
            "verification": record["verification"],
            "diff_stat": record["diff_stat"],
            "actor_id": "research",
            "origen_trust": int(TrustLevel.HUMANO),
        },
    )
    record["integration_approval_id"] = ap_id
    _write(record, config)
    await _log_research_line(
        instance_id,
        record,
        "product_experiment_verified",
        candidate_commit=record["candidate_commit"],
        integration_approval_id=ap_id,
        isolation=record["verification"]["isolation"],
    )
    return record


async def apply(instance_id: str, proposal_id: str, *, config=None) -> dict:
    """Integrate only the separately approved, unchanged candidate."""
    if config is None:
        from lhassa.config import Config

        config = Config()
    record = load(proposal_id, config)
    if record["status"] != "awaiting_integration_approval":
        raise ValueError(f"cannot integrate from state {record['status']!r}")
    await _require_approved(
        instance_id,
        record["integration_approval_id"],
        "product_change_integration",
        proposal_id,
    )
    repo = Path(record["project"]["repo"])
    workspace = Path(record["workspace"])
    if _run_git(workspace, "rev-parse", "HEAD") != record["candidate_commit"]:
        raise ValueError("candidate changed after verification")
    if _run_git(repo, "status", "--porcelain"):
        raise ValueError("product checkout is not clean; refusing to integrate")
    if _run_git(repo, "rev-parse", "HEAD") != record["base_commit"]:
        raise ValueError("product moved since the experiment; rebuild/review required")
    if getattr(config, "integrity_enforce_product_changes", False):
        from lhassa.core.conformance import attestation

        integrity = await attestation(instance_id)
        if not integrity["valid"]:
            raise ValueError(
                "product integration requires a fresh green integrity "
                f"attestation: {integrity['reason']}"
            )
    _run_git(
        repo,
        "-c", "user.name=Lhassa",
        "-c", "user.email=lhassa@localhost",
        "merge", "--no-ff", "--no-edit", record["branch"],
    )
    record["merge_commit"] = _run_git(repo, "rev-parse", "HEAD")
    record["rollback_command"] = f"git -C {shlex.quote(str(repo))} revert {record['merge_commit']}"
    _event(record, "integrated", merge_commit=record["merge_commit"])
    await _snapshot(instance_id, record, config)
    await _log_research_line(
        instance_id,
        record,
        "product_change_integrated",
        merge_commit=record["merge_commit"],
        rollback_command=record["rollback_command"],
    )
    return record
