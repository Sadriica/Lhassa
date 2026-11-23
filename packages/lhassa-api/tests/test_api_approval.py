"""Tests for the approval frontier (§2.6, §2.9-(1), §3-Fase 3 DoD); fully offline.

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
from lhassa.security import approval, autonomy, signing
from lhassa.security.trust import TrustLevel


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

        repeated = await client.post(f"/api/approvals/{ap_id}", json={"approved": True})
        assert repeated.status_code == 409
        missing = await client.post("/api/approvals/ap_missing", json={"approved": True})
        assert missing.status_code == 404

        empty = await client.get("/api/approvals")
        assert empty.json() == []

    # The autonomy count updated from the resolved (HUMANO-origin) approval.
    assert await autonomy.autonomy_level("inst_personal", "sync_repo", "duenio") == 1


@pytest.mark.asyncio
async def test_api_resolving_an_approval_pokes_the_waybar_refresh(tmp_path, monkeypatch, knob_config):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.api import server
    from lhassa.views import waybar_state

    pokes = []
    monkeypatch.setattr(waybar_state, "request_refresh", lambda: pokes.append(1))
    payload = {"task_id": "t_002", "accion": "sync_repo", "actor_id": "duenio",
               "objetivo": "proyecto_X", "origen_trust": int(TrustLevel.HUMANO)}
    ap_id = await db.add_approval("inst_personal", "sync_repo", payload)

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://brain") as client:
        missing = await client.post("/api/approvals/ap_missing", json={"approved": True})
        assert missing.status_code == 404 and pokes == []
        done = await client.post(f"/api/approvals/{ap_id}", json={"approved": False})
        assert done.status_code == 200
    assert pokes == [1]


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
        bad_actor = await client.post(
            f"/api/approvals/{ap_id}",
            json={"approved": True, "by_actor": 42},
        )
        assert bad_actor.status_code == 400


@pytest.mark.asyncio
async def test_api_high_risk_actor_label_cannot_bypass_self_approval(
    tmp_path, monkeypatch, knob_config
):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.api import server

    ap_id = await db.add_approval(
        "inst_personal", "task", {"accion": "move_money", "actor_id": "api"}
    )
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://brain") as client:
        response = await client.post(
            f"/api/approvals/{ap_id}",
            json={"approved": True, "by_actor": "totally-different-person"},
        )

    assert response.status_code == 409
    assert "self-approval refused" in response.json()["detail"]
    assert (await db.get_approval("inst_personal", ap_id))["estado"] == "pendiente"


@pytest.mark.asyncio
async def test_api_maps_untrusted_approver_to_403(tmp_path, monkeypatch, knob_config):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.api import server

    ap_id = await db.add_approval("inst_personal", "task", {"accion": "move_money"})

    async def refuse(*args, **kwargs):
        raise approval.UntrustedApproverError("authenticated identity required")

    monkeypatch.setattr(approval, "resolve", refuse)
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://brain") as client:
        response = await client.post(
            f"/api/approvals/{ap_id}", json={"approved": True}
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "authenticated identity required"
