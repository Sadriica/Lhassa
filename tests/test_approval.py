"""Tests for the approval frontier (§2.6, §2.9-(1), §3-Fase 3 DoD) — fully offline.

Covers:
  - HITL per flow: a `mundo`-origin OR a `requires_approval` action → needs_approval
    True; a plain human-origin draft → False.
  - a high-risk action ALWAYS needs approval, even with autonomy in effect.
  - the autonomy knob lowers friction for non-high-risk actions (autonomy=True →
    needs_approval False) but never for the high-risk set.
  - the local-agent gate PARKS a needs-approval task (pending, NOT executed) and
    reports `pendiente_aprobacion`.
  - the /api/approvals GET/POST round-trip resolves an approval and the autonomy
    count updates.
  - the autonomy knob NEVER promotes an Origin's trust level.

The brain DB + signing key are redirected to tmp dirs (the 0.1/0.4 seams); the API
runs through httpx.ASGITransport against the FastAPI app (no live network).
"""

import httpx
import pytest
import pytest_asyncio  # noqa: F401

from lhassa.config import Config
from lhassa.core.task import Task
from lhassa.security import allowlist as al
from lhassa.security import approval, autonomy, signing
from lhassa.security.allowlist import ActionSpec, build_allowlist
from lhassa.security.trust import Origin, TrustLevel


# ── offline setup ────────────────────────────────────────────────────────────────

async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    await db.init_db()
    return db


@pytest.fixture
def knob_config(monkeypatch):
    monkeypatch.setattr(Config, "autonomy_threshold", 3)
    monkeypatch.setattr(Config, "always_approve_actions", {"move_money", "delete_data"})
    monkeypatch.setattr(Config, "instance_id", "inst_personal")


def _task(*, accion="sync_repo", objetivo="proyecto_X", trust=TrustLevel.HUMANO, actor="duenio"):
    return Task(
        id="t_001",
        instance_id="inst_personal",
        accion=accion,
        objetivo=objetivo,
        params={"branch": "main"},
        origen=Origin(trust=trust, actor_id=actor),
        estado="pendiente",
        creado_en="2026-06-29T00:00:00+00:00",
    )


# ── needs_approval — HITL per flow ───────────────────────────────────────────────

def test_mundo_origin_needs_approval(knob_config):
    # A world-derived origin must wait for a human OK ("origen mundo espera tu OK").
    assert approval.needs_approval(_task(trust=TrustLevel.MUNDO)) is True


def test_requires_approval_flag_needs_approval(monkeypatch, knob_config):
    monkeypatch.setattr(
        al, "ALLOWLIST",
        {"sync_repo": ActionSpec(objetivos="*", min_trust=TrustLevel.INTERNO, requires_approval=True)},
    )
    # Even a clean human-origin action needs approval when the action's flag is set.
    assert approval.needs_approval(_task(trust=TrustLevel.HUMANO)) is True


def test_human_origin_draft_does_not_need_approval(monkeypatch, knob_config):
    monkeypatch.setattr(al, "ALLOWLIST", build_allowlist(repos="*"))  # sync_repo: no flag
    assert approval.needs_approval(_task(trust=TrustLevel.HUMANO)) is False


def test_allow_decision_input_uses_per_action_flag(knob_config):
    from lhassa.security.allowlist import AllowDecision
    assert approval.needs_approval(AllowDecision(True, "allowed", requires_approval=True)) is True
    assert approval.needs_approval(AllowDecision(True, "allowed", requires_approval=False)) is False


# ── the high-risk set is always required, autonomy never lowers it ───────────────

def test_high_risk_always_needs_approval(knob_config):
    task = _task(accion="move_money", trust=TrustLevel.HUMANO)
    assert approval.needs_approval(task) is True
    # Even if the autonomy knob is (somehow) in effect, high-risk is never lowered.
    assert approval.needs_approval(task, autonomy=True) is True


def test_autonomy_lowers_friction_for_non_high_risk(monkeypatch, knob_config):
    monkeypatch.setattr(
        al, "ALLOWLIST",
        {"sync_repo": ActionSpec(objetivos="*", min_trust=TrustLevel.INTERNO, requires_approval=True)},
    )
    task = _task(trust=TrustLevel.HUMANO)
    assert approval.needs_approval(task, autonomy=False) is True
    assert approval.needs_approval(task, autonomy=True) is False  # knob lowers it


# ── request / pending / resolve + autonomy feed ──────────────────────────────────

@pytest.mark.asyncio
async def test_request_park_and_pending(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    ap_id = await approval.request_approval(
        "inst_personal", tipo="sync_repo",
        payload={"task_id": "t_001", "accion": "sync_repo", "actor_id": "duenio",
                 "origen_trust": int(TrustLevel.HUMANO)},
    )
    queue = await approval.pending("inst_personal")
    assert len(queue) == 1
    assert queue[0]["id"] == ap_id
    assert queue[0]["payload"]["accion"] == "sync_repo"


@pytest.mark.asyncio
async def test_resolve_approve_feeds_autonomy(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    payload = {"task_id": "t_001", "accion": "sync_repo", "actor_id": "duenio",
               "origen_trust": int(TrustLevel.HUMANO)}
    for _ in range(3):
        ap_id = await approval.request_approval("inst_personal", tipo="sync_repo", payload=payload)
        await approval.resolve("inst_personal", ap_id, approved=True, by_actor="duenio")

    assert await approval.pending("inst_personal") == []
    assert await autonomy.autonomy_level("inst_personal", "sync_repo", "duenio") == 3
    assert await autonomy.should_auto_approve("inst_personal", "sync_repo", "duenio") is True


@pytest.mark.asyncio
async def test_resolve_deny_resets_autonomy(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    payload = {"accion": "sync_repo", "actor_id": "duenio", "origen_trust": 2}
    ap_a = await approval.request_approval("inst_personal", tipo="sync_repo", payload=payload)
    await approval.resolve("inst_personal", ap_a, approved=True, by_actor="duenio")
    ap_b = await approval.request_approval("inst_personal", tipo="sync_repo", payload=payload)
    await approval.resolve("inst_personal", ap_b, approved=False, by_actor="duenio")
    assert await autonomy.autonomy_level("inst_personal", "sync_repo", "duenio") == 0


# ── the knob never promotes an Origin's trust level ──────────────────────────────

@pytest.mark.asyncio
async def test_knob_never_promotes_trust(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    # Earn auto-approval for (sync_repo, duenio).
    for _ in range(3):
        await autonomy.record_decision(
            "inst_personal", "sync_repo", "duenio", approved=True, origen_trust=2
        )
    assert await autonomy.should_auto_approve("inst_personal", "sync_repo", "duenio") is True

    # A MUNDO-origin task by the same actor still has MUNDO trust — the knob did not
    # promote it. needs_approval WITHOUT the knob still demands approval (trust low);
    # only passing the knob's verdict explicitly lowers the friction.
    task = _task(trust=TrustLevel.MUNDO, actor="duenio")
    assert task.origen.trust == TrustLevel.MUNDO
    assert approval.needs_approval(task) is True


# ── local-agent gate parks a needs-approval task (not executed) ──────────────────

@pytest.mark.asyncio
async def test_local_agent_parks_needs_approval_task(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    from lhassa.core import actions
    import lhassa.remote.local_agent as la

    # sync_repo requires approval per policy; sign an INTERNO-origin task so the
    # allowlist ALLOWS it, leaving the approval frontier to decide.
    monkeypatch.setattr(
        al, "ALLOWLIST",
        {"sync_repo": ActionSpec(objetivos="*", min_trust=TrustLevel.INTERNO, requires_approval=True)},
    )
    invoked = {"git": False}
    monkeypatch.setattr(actions, "_run", lambda cmd: invoked.__setitem__("git", True))

    task = _task(trust=TrustLevel.INTERNO, actor="duenio")
    task.firma = signing.sign_task(task)

    reports = await la.run_tasks([task])
    assert len(reports) == 1
    assert reports[0]["outcome"] == "pendiente_aprobacion"
    assert invoked["git"] is False  # parked, NOT executed

    # The action is now sitting in the approvals queue.
    queue = await approval.pending("inst_personal")
    assert len(queue) == 1
    assert queue[0]["payload"]["accion"] == "sync_repo"


@pytest.mark.asyncio
async def test_local_agent_auto_approves_after_streak(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    from lhassa.core import actions
    import lhassa.remote.local_agent as la

    monkeypatch.setattr(
        al, "ALLOWLIST",
        {"sync_repo": ActionSpec(objetivos="*", min_trust=TrustLevel.INTERNO, requires_approval=True)},
    )
    # A human has already approved this (accion, actor) to threshold → auto-approve.
    for _ in range(3):
        await autonomy.record_decision(
            "inst_personal", "sync_repo", "duenio", approved=True, origen_trust=2
        )
    import subprocess
    monkeypatch.setattr(
        actions, "_run",
        lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout="Updating a..b", stderr=""),
    )

    # A directory that looks like a git repo so the (executed) sync_repo finds a path.
    repo = tmp_path / "proyecto_X"
    repo.mkdir()
    (repo / ".git").mkdir()

    task = _task(trust=TrustLevel.HUMANO, actor="duenio", objetivo=str(repo))
    task.params = {"repo_dir": str(repo)}
    task.firma = signing.sign_task(task)

    reports = await la.run_tasks([task])
    assert reports[0]["outcome"] == "hecha"  # executed without parking
    assert await approval.pending("inst_personal") == []


# ── API round-trip: GET queue, POST resolve, autonomy updates ────────────────────

@pytest.mark.asyncio
async def test_api_approvals_roundtrip(tmp_path, monkeypatch, knob_config):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.api import server

    payload = {"task_id": "t_001", "accion": "sync_repo", "actor_id": "duenio",
               "objetivo": "proyecto_X", "origen_trust": int(TrustLevel.HUMANO)}
    ap_id = await db.add_approval("inst_personal", "sync_repo", payload)

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://brain") as client:
        listed = await client.get("/api/approvals")
        assert listed.status_code == 200
        body = listed.json()
        assert len(body) == 1 and body[0]["id"] == ap_id

        resolved = await client.post(f"/api/approvals/{ap_id}", json={"approved": True})
        assert resolved.status_code == 200
        assert resolved.json()["approved"] is True

        empty = await client.get("/api/approvals")
        assert empty.json() == []

    # The autonomy count updated from the resolved (HUMANO-origin) approval.
    assert await autonomy.autonomy_level("inst_personal", "sync_repo", "duenio") == 1


@pytest.mark.asyncio
async def test_api_approvals_rejects_bad_body(tmp_path, monkeypatch, knob_config):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.api import server

    ap_id = await db.add_approval("inst_personal", "sync_repo", {"accion": "sync_repo"})
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://brain") as client:
        missing = await client.post(f"/api/approvals/{ap_id}", json={})
        assert missing.status_code == 400
        wrong_type = await client.post(f"/api/approvals/{ap_id}", json={"approved": "yes"})
        assert wrong_type.status_code == 400


# ── describe(): the one human-readable render every queue surface uses ─────────────


def test_describe_mint_action():
    row = {
        "tipo": "mint_action",
        "payload": {
            "accion": "mint_action",
            "pattern": "telegram::responder",
            "despertar_por": "telegram",
            "candidate_accion": "responder",
            "count": 41,
            "total_cost_usd": 0.2625,
        },
    }
    desc = approval.describe(row)
    assert desc["title"] == "Make 'responder' automatic"
    assert "×41" in desc["detail"]
    assert "$0.26" in desc["detail"]
    assert "$0 deterministic action" in desc["detail"]


def test_describe_mint_memory():
    desc = approval.describe(
        {
            "tipo": "mint_memory",
            "payload": {
                "accion": "mint_memory",
                "despertar_por": "telegram",
                "count": 10,
                "total_cost_usd": 0.5,
            },
        }
    )
    assert desc["title"] == "Remember the answer to 'telegram' so I stop re-deriving it"
    assert "×10" in desc["detail"]
    assert "$0.50" in desc["detail"]
    assert "durable $0 fact" in desc["detail"]


def test_describe_mint_schedule():
    desc = approval.describe(
        {
            "tipo": "mint_schedule",
            "payload": {
                "accion": "mint_schedule",
                "despertar_por": "weekly_report",
                "interval_days": 7.0,
                "count": 5,
            },
        }
    )
    assert desc["title"] == "Answer 'weekly_report' on its cadence automatically"
    assert "7" in desc["detail"]
    assert "×5" in desc["detail"]
    assert "$0 recurring action" in desc["detail"]


def test_describe_seed_cache():
    desc = approval.describe(
        {
            "tipo": "seed_cache",
            "payload": {
                "accion": "seed_cache",
                "question": "what is the capital of France",
                "count": 5,
            },
        }
    )
    assert desc["title"] == "'what is the capital of France' is already answered from the cache"
    assert "×5" in desc["detail"]
    assert "nothing further to mint" in desc["detail"]


def test_describe_tool_graduation():
    desc = approval.describe(
        {"tipo": "tool_graduation", "payload": {"accion": "tool_graduation", "tool": "csv_summarize", "branch": "cand/1"}}
    )
    assert desc["title"] == "Graduate self-written tool 'csv_summarize'"
    assert "gates" in desc["detail"] and "cand/1" in desc["detail"]


def test_describe_flow_gate_and_org_task():
    gate = approval.describe({"tipo": "flow_gate", "payload": {"accion": "flow_gate", "node": "contact_preserved"}})
    assert "contact_preserved" in gate["title"]

    org = approval.describe(
        {"tipo": "org_task", "payload": {"accion": "org_task", "department": "interns", "goal": "tailor the CV", "actor_id": "lhassa"}}
    )
    assert org["title"] == "Org task for 'interns'"
    assert "tailor the CV" in org["detail"] and "lhassa" in org["detail"]


def test_describe_code_run():
    desc = approval.describe(
        {
            "tipo": "code_run",
            "payload": {
                "accion": "code_run",
                "task": "fix the failing test in tests/test_x.py",
                "cli": "claude",
                "cwd": "/home/owner/Code/lhassa",
            },
        }
    )
    assert desc["title"] == "Run a coding agent: fix the failing test in tests/test_x.py"
    assert "claude" in desc["detail"]
    assert "/home/owner/Code/lhassa" in desc["detail"]
    assert "write access" in desc["detail"]


def test_describe_code_run_truncates_long_task():
    desc = approval.describe(
        {"tipo": "code_run", "payload": {"task": "x" * 200, "cli": "codex", "cwd": "/tmp"}}
    )
    assert desc["title"].startswith("Run a coding agent: " + "x" * 57 + "…")


def test_describe_dispatch_task_and_unknown_degrade():
    task = approval.describe(
        {"tipo": "task", "payload": {"accion": "sync_repo", "objetivo": "~/Code/x", "actor_id": "duenio", "origen_trust": 3}}
    )
    assert task["title"] == "Run 'sync_repo'"
    assert "~/Code/x" in task["detail"] and "trust: 3" in task["detail"]

    # Unknown/malformed shapes degrade, never raise.
    assert approval.describe({})["title"] == "Approval: ?"
    assert approval.describe({"tipo": "weird", "payload": "not-a-dict"})["title"] == "Approval: weird"
    weird_cost = approval.describe(
        {"tipo": "mint_action", "payload": {"total_cost_usd": "not-a-number", "candidate_accion": "x"}}
    )
    assert weird_cost["title"]  # degraded but present
