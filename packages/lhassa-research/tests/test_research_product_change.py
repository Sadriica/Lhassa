from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lhassa.research import product_change
from lhassa.core import db, projects
from lhassa.security import approval
from lhassa.security.quarantine import QuarantineResult
from lhassa.state import state


CORPUS = """\
## The idea in one sentence
Use a small reversible improvement [S1].
## Mechanics - down to the base
It changes one local behavior [S1].
## Evidence the authors present
- The source describes the behavior [S1].
## Sources
- S1: https://example.com/source; sha256=abc
## Mapping to Lhassa
It may improve one code project [S1].
## Not yet verified by us - and the experiment that would
Baseline: current behavior. Instrument: tests. Metric: passing cases. Stop rule: any regression.
## Open questions
Does the local experiment pass?
"""


class Config:
    instance_id = "inst_change"

    def __init__(self, root: Path):
        self.adoption_dir = str(root)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout.strip()


@pytest.fixture
def change_env(tmp_path, tmp_db):
    repo = tmp_path / "product"
    repo.mkdir()
    git(repo, "init")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "base",
    )
    draft = tmp_path / "corpus.md"
    draft.write_text(CORPUS, encoding="utf-8")
    return repo, draft, Config(tmp_path / "external-adoptions")


@pytest.mark.asyncio
async def test_proposal_requires_gated_research_and_parks_only_experiment(
    change_env,
):
    repo, draft, config = change_env
    project = await projects.create_project(
        config.instance_id, "product", kind="code", path=str(repo)
    )

    record = await product_change.propose(
        config.instance_id,
        project_ref=project["id"],
        research_path=str(draft),
        objective="add the measured behavior",
        verify_command="pytest -q",
        config=config,
    )

    assert record["status"] == "awaiting_experiment_approval"
    assert record["research"]["sha256"]
    assert not (Path(config.adoption_dir) / record["id"] / "worktree").exists()
    row = await db.get_approval(config.instance_id, record["experiment_approval_id"])
    assert row["tipo"] == "product_change_experiment"
    assert row["estado"] == "pendiente"


@pytest.mark.asyncio
async def test_proposal_extends_linked_idea_append_only_log(change_env):
    repo, draft, config = change_env
    project = await projects.create_project(
        config.instance_id, "product", kind="code", path=str(repo)
    )
    assessment = await db.create_idea_assessment(
        config.instance_id,
        idea="linked technology",
        evidence_digest="d" * 64,
        evidence_path="/tmp/evidence.json",
    )
    await db.transition_idea_assessment(
        config.instance_id,
        assessment["id"],
        stage="test_research_completed",
        to_status="researched",
        updates={
            "draft_path": str(draft.resolve()),
            "investment_level": "rigorous",
            "decision": "experiment",
        },
    )

    record = await product_change.propose(
        config.instance_id,
        project_ref=project["id"],
        research_path=str(draft),
        objective="add measured behavior",
        verify_command="true",
        config=config,
    )

    assert record["assessment_id"] == assessment["id"]
    events = await db.list_idea_assessment_events(
        config.instance_id, assessment["id"]
    )
    assert events[-1]["stage"] == "product_change_proposed"
    assert events[-1]["payload"]["proposal_id"] == record["id"]


@pytest.mark.asyncio
async def test_workspace_needs_approval_and_research_hash_is_immutable(change_env):
    repo, draft, config = change_env
    project = await projects.create_project(
        config.instance_id, "product", kind="code", path=str(repo)
    )
    record = await product_change.propose(
        config.instance_id,
        project_ref=project["id"],
        research_path=str(draft),
        objective="add the measured behavior",
        verify_command="true",
        config=config,
    )

    with pytest.raises(ValueError, match="not approved"):
        await product_change._require_approved(
            config.instance_id,
            record["experiment_approval_id"],
            "product_change_experiment",
            record["id"],
        )
    await approval.resolve(
        config.instance_id,
        record["experiment_approval_id"],
        approved=True,
        by_actor="owner",
    )
    workspace = product_change.prepare_workspace(record, config)
    assert workspace.is_dir()
    assert repo not in workspace.parents
    assert git(repo, "rev-parse", "HEAD") == record["base_commit"]

    draft.write_text(CORPUS + "\nchanged after approval\n", encoding="utf-8")
    assert product_change._sha256(draft) != record["research"]["sha256"]


@pytest.mark.asyncio
async def test_integration_requires_second_approval_and_unchanged_product(
    change_env, monkeypatch
):
    repo, draft, config = change_env
    project = await projects.create_project(
        config.instance_id, "product", kind="code", path=str(repo)
    )
    record = await product_change.propose(
        config.instance_id,
        project_ref=project["id"],
        research_path=str(draft),
        objective="add candidate",
        verify_command="true",
        config=config,
    )
    await approval.resolve(
        config.instance_id,
        record["experiment_approval_id"],
        approved=True,
        by_actor="owner",
    )
    workspace = product_change.prepare_workspace(record, config)
    (workspace / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    git(workspace, "add", "candidate.txt")
    git(
        workspace,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "candidate",
    )
    record["candidate_commit"] = git(workspace, "rev-parse", "HEAD")
    product_change._event(record, "awaiting_integration_approval")
    ap_id = await approval.request_approval(
        config.instance_id,
        tipo="product_change_integration",
        payload={
            "accion": "product_change_integration",
            "proposal_id": record["id"],
        },
    )
    record["integration_approval_id"] = ap_id
    product_change._write(record, config)

    with pytest.raises(ValueError, match="not approved"):
        await product_change.apply(config.instance_id, record["id"], config=config)
    await approval.resolve(
        config.instance_id, ap_id, approved=True, by_actor="owner"
    )
    (repo / "dirty.txt").write_text("owner work\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not clean"):
        await product_change.apply(config.instance_id, record["id"], config=config)
    (repo / "dirty.txt").unlink()

    from lhassa.core import conformance

    config.integrity_enforce_product_changes = True

    async def invalid(_instance_id):
        return {"valid": False, "reason": "no green run"}

    monkeypatch.setattr(conformance, "attestation", invalid)
    with pytest.raises(ValueError, match="fresh green integrity"):
        await product_change.apply(config.instance_id, record["id"], config=config)

    async def valid(_instance_id):
        return {"valid": True, "reason": "fresh"}

    monkeypatch.setattr(conformance, "attestation", valid)
    integrated = await product_change.apply(
        config.instance_id, record["id"], config=config
    )
    assert (repo / "candidate.txt").read_text(encoding="utf-8") == "candidate\n"
    assert integrated["status"] == "integrated"
    assert integrated["merge_commit"] == git(repo, "rev-parse", "HEAD")
    assert "git -C" in integrated["rollback_command"]


@pytest.mark.asyncio
async def test_build_fails_closed_when_quarantine_has_no_enforceable_isolation(
    change_env, monkeypatch
):
    repo, draft, config = change_env
    project = await projects.create_project(
        config.instance_id, "product", kind="code", path=str(repo)
    )
    record = await product_change.propose(
        config.instance_id,
        project_ref=project["id"],
        research_path=str(draft),
        objective="add candidate",
        verify_command="true && true",
        config=config,
    )
    await approval.resolve(
        config.instance_id,
        record["experiment_approval_id"],
        approved=True,
        by_actor="owner",
    )

    class Agent:
        async def _invoke(self, run_id, run, task, cwd):
            (cwd / "candidate.txt").write_text("candidate\n", encoding="utf-8")
            return "done"

    from lhassa.agents import local_cli

    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "make_agent", lambda cli: Agent())

    async def unisolated(*args, **kwargs):
        return QuarantineResult(
            ok=True,
            stdout="passed",
            stderr="",
            exit_code=0,
            timed_out=False,
            escaped=False,
            isolation="none",
        )

    monkeypatch.setattr(product_change, "run_in_quarantine", unisolated)
    with pytest.raises(RuntimeError, match="no enforceable sandbox"):
        await product_change.build(
            config.instance_id, record["id"], cli="fake", config=config
        )
    failed = product_change.load(record["id"], config)
    assert failed["status"] == "experiment_failed"
    assert "integration_approval_id" not in failed
    state.agent_runs.clear()


@pytest.mark.asyncio
async def test_passing_build_creates_patch_and_distinct_integration_gate(
    change_env, monkeypatch
):
    repo, draft, config = change_env
    project = await projects.create_project(
        config.instance_id, "product", kind="code", path=str(repo)
    )
    record = await product_change.propose(
        config.instance_id,
        project_ref=project["id"],
        research_path=str(draft),
        objective="add candidate",
        verify_command="true",
        config=config,
    )
    await approval.resolve(
        config.instance_id,
        record["experiment_approval_id"],
        approved=True,
        by_actor="owner",
    )

    class Agent:
        async def _invoke(self, run_id, run, task, cwd):
            (cwd / "candidate.txt").write_text("candidate\n", encoding="utf-8")
            return "done"

    from lhassa.agents import local_cli

    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "make_agent", lambda cli: Agent())

    async def isolated(*args, **kwargs):
        return QuarantineResult(
            ok=True,
            stdout="passed",
            stderr="",
            exit_code=0,
            timed_out=False,
            escaped=False,
            isolation="bwrap",
        )

    monkeypatch.setattr(product_change, "run_in_quarantine", isolated)
    built = await product_change.build(
        config.instance_id, record["id"], cli="fake", config=config
    )

    assert built["status"] == "awaiting_integration_approval"
    assert Path(built["candidate_patch"]).read_text(encoding="utf-8").startswith(
        "From "
    )
    assert built["integration_approval_id"] != built["experiment_approval_id"]
    integration = await db.get_approval(
        config.instance_id, built["integration_approval_id"]
    )
    assert integration["tipo"] == "product_change_integration"
    assert integration["estado"] == "pendiente"
    state.agent_runs.clear()
