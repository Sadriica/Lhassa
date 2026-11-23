# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for lhassa.org.sharing; cross-instance sharing (Stream 6 Phase A).

Offline + $0: a tmp brain DB (monkeypatched ``db.DB_PATH``) shared by two
"instances" (distinguished only by ``instance_id``: the schema's existing
multi-tenant shape), a tmp skills dir and a tmp relationships file. No network,
no LLM, no org topology needed (SHARES is instance-scoped, see
test_org_relationships.py).

Covers: deny-by-default without a grant, the grant→list/subscribe happy path,
publishing a resource that doesn't exist locally, the closed shareable-type
vocabulary (facts/episodes can never be published), and; the mandatory
leak test; that a subscription/grant NEVER exposes the other instance's
private facts or episodes, only its declared shared resources.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.core import db
from lhassa.org import relationships as rel
from lhassa.org import sharing


def _cfg(tmp_path, instance_id: str) -> SimpleNamespace:
    # The SHARES grant registry (org_relationships_file) is shared across every
    # instance on this "box": same as the shared core.db DB_PATH (see
    # lhassa.org.sharing's module docstring): a grant an instance writes must be
    # readable by the instance it names, which today means the same physical
    # file, the same way it means the same physical DB. skills_dir stays PER
    # instance (each instance's own local resources).
    return SimpleNamespace(
        instance_id=instance_id,
        root_admin_id="lhassa",
        org_relationships_file=str(tmp_path / "relationships.json"),
        skills_dir=str(tmp_path / f"skills-{instance_id}"),
    )


async def _episodes(instance_id: str, despertar_por: str) -> list[dict]:
    rows = await db.load_recent_episodes(instance_id, n=200)
    rows.reverse()
    return [r for r in rows if r["despertar_por"] == despertar_por]


def _make_skill(tmp_path, instance_id: str, name: str) -> None:
    from lhassa.skills.skillmd import Skill
    from lhassa.skills.store import SkillStore

    cfg = _cfg(tmp_path, instance_id)
    SkillStore(cfg).save(Skill(name=name, description="a test skill", instructions="do the thing"))


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")


# ── the closed shareable-type vocabulary ───────────────────────────────────────


def test_facts_and_episodes_are_never_shareable():
    assert "fact" not in sharing.SHAREABLE_TYPES
    assert "episode" not in sharing.SHAREABLE_TYPES
    assert sharing.SHAREABLE_TYPES == {"skill", "flow", "template", "knowledge"}


@pytest.mark.asyncio
async def test_publish_refuses_unshareable_type(tmp_path):
    cfg_a = _cfg(tmp_path, "inst_a")
    result = await sharing.publish("fact", "some-fact-id", actor_id="alice", config=cfg_a)
    assert result["ok"] is False
    assert "not shareable" in result["reason"]


@pytest.mark.asyncio
async def test_subscribe_refuses_unshareable_type_even_with_grant(tmp_path):
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.grant("inst_a", actor_id="bob", config=cfg_b)
    result = await sharing.subscribe("inst_b", "episode", "ep-123", actor_id="alice", config=cfg_a)
    assert result["ok"] is False
    assert "not shareable" in result["reason"]


# ── publish requires the resource to actually exist ────────────────────────────


@pytest.mark.asyncio
async def test_publish_refuses_nonexistent_skill(tmp_path):
    cfg_a = _cfg(tmp_path, "inst_a")
    result = await sharing.publish("skill", "does-not-exist", actor_id="alice", config=cfg_a)
    assert result["ok"] is False
    assert "no local skill" in result["reason"]


@pytest.mark.asyncio
async def test_publish_existing_skill_succeeds(tmp_path):
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    result = await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)
    assert result["ok"] is True
    offerings = await db.list_shared_resources("inst_a")
    assert len(offerings) == 1
    assert offerings[0]["resource_name"] == "code-review"
    assert offerings[0]["actor_id"] == "alice"


# ── deny-by-default: no grant, no access ────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_offerings_refused_without_grant(tmp_path):
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)

    result = await sharing.list_offerings(
        "inst_a", requester_instance_id="inst_b", actor_id="bob", config=cfg_b
    )
    assert result["ok"] is False
    assert "no shares permit" in result["reason"]
    assert result["offerings"] == []


@pytest.mark.asyncio
async def test_subscribe_refused_without_grant(tmp_path):
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)

    result = await sharing.subscribe("inst_a", "skill", "code-review", actor_id="bob", config=cfg_b)
    assert result["ok"] is False
    assert "no shares permit" in result["reason"]
    subs = await db.list_resource_subscriptions("inst_b")
    assert subs == []


@pytest.mark.asyncio
async def test_own_offerings_never_need_a_grant(tmp_path):
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)
    result = await sharing.list_offerings(
        "inst_a", requester_instance_id="inst_a", actor_id="alice", config=cfg_a
    )
    assert result["ok"] is True
    assert len(result["offerings"]) == 1


# ── the grant → discover → subscribe happy path ─────────────────────────────────


@pytest.mark.asyncio
async def test_grant_then_list_and_subscribe_succeeds(tmp_path):
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)

    granted = await sharing.grant("inst_b", actor_id="alice", config=cfg_a)
    assert granted["ok"] is True

    listed = await sharing.list_offerings(
        "inst_a", requester_instance_id="inst_b", actor_id="bob", config=cfg_b
    )
    assert listed["ok"] is True
    assert listed["offerings"][0]["resource_name"] == "code-review"

    subscribed = await sharing.subscribe(
        "inst_a", "skill", "code-review", actor_id="bob", config=cfg_b
    )
    assert subscribed["ok"] is True
    subs = await sharing.list_subscriptions(config=cfg_b)
    assert len(subs) == 1
    assert subs[0]["source_instance_id"] == "inst_a"


@pytest.mark.asyncio
async def test_subscribe_refuses_unpublished_resource_even_with_grant(tmp_path):
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.grant("inst_b", actor_id="alice", config=cfg_a)
    result = await sharing.subscribe(
        "inst_a", "skill", "never-published", actor_id="bob", config=cfg_b
    )
    assert result["ok"] is False
    assert "has not published" in result["reason"]


@pytest.mark.asyncio
async def test_revoke_grant_closes_access_again(tmp_path):
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)
    await sharing.grant("inst_b", actor_id="alice", config=cfg_a)
    await sharing.revoke_grant("inst_b", actor_id="alice", config=cfg_a)

    result = await sharing.list_offerings(
        "inst_a", requester_instance_id="inst_b", actor_id="bob", config=cfg_b
    )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_unpublish_removes_from_offerings(tmp_path):
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)
    unpub = await sharing.unpublish("skill", "code-review", actor_id="alice", config=cfg_a)
    assert unpub["revoked"] is True
    offerings = await db.list_shared_resources("inst_a")
    assert offerings == []


# ── every cross-instance hop logs its own $0 episode ────────────────────────────


@pytest.mark.asyncio
async def test_every_hop_logs_a_zero_cost_episode(tmp_path):
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)
    await sharing.grant("inst_b", actor_id="alice", config=cfg_a)
    await sharing.subscribe("inst_a", "skill", "code-review", actor_id="bob", config=cfg_b)

    eps_a = await _episodes("inst_a", sharing.DESPERTAR_SHARE)
    eps_b = await _episodes("inst_b", sharing.DESPERTAR_SHARE)
    assert eps_a  # publish + grant hops logged against inst_a
    assert eps_b  # subscribe hop logged against inst_b
    for ep in eps_a + eps_b:
        assert float(ep["costo_usd"]) == 0.0
        assert ep["dominio"] == "lhassa"  # root_admin_id


# ── THE mandatory leak test: private memory never crosses the boundary ─────────


@pytest.mark.asyncio
async def test_private_facts_never_leak_across_instances(tmp_path):
    """Even with a full grant in place, sharing NEVER exposes facts/episodes;
    only the resources explicitly published to shared_resources. This is the
    structural guarantee: SHAREABLE_TYPES has no 'fact'/'episode' member and
    shared_resources is a table populated only through publish(), so there is
    no code path that could leak a fact row into an offering."""
    await db.init_db()
    await db.insert_fact(
        {
            "id": "fact-secret",
            "instance_id": "inst_a",
            "tipo": "dato",
            "contenido": "the owner's private salary figure",
            "creado_en": "2026-01-01T00:00:00",
            "actualizado_en": "2026-01-01T00:00:00",
            "actor_id": "alice",
        }
    )
    _make_skill(tmp_path, "inst_a", "code-review")
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.publish("skill", "code-review", actor_id="alice", config=cfg_a)
    await sharing.grant("inst_b", actor_id="alice", config=cfg_a)

    # inst_b, even fully granted, only ever sees the published skill; never
    # inst_a's facts (query_facts scoped to inst_a never appears anywhere here).
    listed = await sharing.list_offerings(
        "inst_a", requester_instance_id="inst_b", actor_id="bob", config=cfg_b
    )
    assert listed["ok"] is True
    names = {(o["resource_type"], o["resource_name"]) for o in listed["offerings"]}
    assert names == {("skill", "code-review")}

    # And inst_a's own facts stay scoped to inst_a at the DB layer regardless;
    # the isolation the whole schema is built on, unaffected by any grant.
    inst_a_facts = await db.query_facts("inst_a")
    inst_b_facts = await db.query_facts("inst_b")
    assert len(inst_a_facts) == 1
    assert inst_b_facts == []


@pytest.mark.asyncio
async def test_list_offerings_filters_out_a_row_that_bypassed_publish(tmp_path):
    """A stronger version of the leak test: this one DEFEATS publish()'s own
    type check by writing a 'fact'-typed row DIRECTLY into shared_resources via
    the low-level db.publish_resource (as a future bug, a bad migration, or
    manual DB surgery might), and asserts list_offerings still refuses to
    surface it. If this ever fails, it means SHAREABLE_TYPES is enforced only
    at the publish() call site and not at read time; a real leak path."""
    await db.init_db()
    await db.publish_resource("inst_a", "fact", "fact-secret", actor_id="alice")
    cfg_a = _cfg(tmp_path, "inst_a")
    cfg_b = _cfg(tmp_path, "inst_b")
    await sharing.grant("inst_b", actor_id="alice", config=cfg_a)

    listed = await sharing.list_offerings(
        "inst_a", requester_instance_id="inst_b", actor_id="bob", config=cfg_b
    )
    assert listed["ok"] is True
    assert listed["offerings"] == []


@pytest.mark.asyncio
async def test_grant_permit_alone_cannot_publish_a_fact(tmp_path):
    """A grant only OPENS the discovery door for the closed shareable-type set;
    it can never be used to publish memory, because publish() itself refuses
    any resource_type outside SHAREABLE_TYPES regardless of grants."""
    cfg_a = _cfg(tmp_path, "inst_a")
    await sharing.grant("inst_b", actor_id="alice", config=cfg_a)
    result = await sharing.publish("fact", "fact-secret", actor_id="alice", config=cfg_a)
    assert result["ok"] is False


# ── permits() extension itself ──────────────────────────────────────────────────


def test_shares_kind_is_closed_and_instance_scoped():
    assert rel.SHARES in rel.RELATION_KINDS
    assert rel.SHARES in rel.INSTANCE_SCOPED_KINDS
    assert rel.SHARES not in rel.SYMMETRIC_KINDS
