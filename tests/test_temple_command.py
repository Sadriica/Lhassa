"""Tests for the /temple Telegram command — the owner's door into temple.

Offline + $0: a tmp brain DB, a seeded lhassa->temple topology, and a fake
``notify.dispatch.notify`` so the up-hop send never touches the network.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.core import db
from lhassa.notify import commands


def _cfg(tmp_path, **over):
    topo = tmp_path / "org.json"
    topo.write_text(
        '[{"id":"temple","role":"internal-operations","department":"temple","parent":null}]',
        encoding="utf-8",
    )
    base = dict(
        instance_id="inst_personal",
        org_topology_file=str(topo),
        root_admin_id="lhassa",
        notify_channels="log",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from lhassa import actionpack

    actionpack.register_all()  # org_run/temple_run must be registered for mint_task
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)


def test_temple_requires_text(tmp_path):
    reply = asyncio.run(commands.dispatch("/temple", config=_cfg(tmp_path)))
    assert "Usage" in reply


def test_temple_keyword_match_runs_deterministic_action(tmp_path, monkeypatch):
    from lhassa.org import topology as topo_mod
    from lhassa.org.topology import BrainNode, Topology

    monkeypatch.setattr(
        topo_mod,
        "load_topology",
        lambda config=None: Topology(
            [
                BrainNode(id="lhassa", role="administrator"),
                BrainNode(id="temple", role="internal-operations", department="temple", parent="lhassa"),
            ]
        ),
    )

    reply = asyncio.run(
        commands.dispatch("/temple distill recent episodes into facts", config=_cfg(tmp_path))
    )
    assert "temple ran distill_memory" in reply

    rows = asyncio.run(db.load_recent_episodes("inst_personal", n=50))
    assert any(r["despertar_por"] == "temple-execute" for r in rows)


def test_temple_free_form_instruction_mints_org_run(tmp_path):
    reply = asyncio.run(
        commands.dispatch("/temple draft the internal onboarding doc", config=_cfg(tmp_path))
    )
    assert "Handed to temple" in reply

    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    org_runs = [t for t in tasks if t["accion"] == "org_run"]
    assert len(org_runs) == 1
    assert org_runs[0]["params"]["department"] == "temple"


# ── /temple schedule — the temple's own clock ─────────────────────────────────────


def test_temple_schedule_lists_recurring_jobs_with_cadence_and_monk(tmp_path, monkeypatch):
    import lhassa.core.db as _db
    from lhassa.notify import commands

    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "brain.db")

    async def go():
        await _db.init_db()
        from lhassa.core.dispatch import mint_task
        from lhassa.security.trust import Origin, TrustLevel
        origin = Origin(trust=TrustLevel.INTERNO, actor_id="seed")
        # Two internal cronjobs owned by different monks + one owner-facing task
        # (send_digest) that must NOT appear — it isn't the temple's.
        await mint_task("inst_personal", "temple_run", objetivo="curate",
                        params={"action": "curate_skills"}, origin=origin,
                        recurrence="604800")
        await mint_task("inst_personal", "temple_run", objetivo="detect",
                        params={"action": "detect_repetition"}, origin=origin,
                        recurrence="86400")
        await mint_task("inst_personal", "send_digest", objetivo="digest",
                        origin=origin, recurrence="43200")
        return await commands.dispatch("/temple schedule", config=_cfg(tmp_path))

    reply = asyncio.run(go())
    assert "temple's clock" in reply
    assert "curate_skills — weekly · guardian" in reply
    assert "detect_repetition — daily · oracle" in reply
    assert "send_digest" not in reply  # owner-facing, not the temple's


def test_temple_schedule_empty(tmp_path, monkeypatch):
    import lhassa.core.db as _db
    from lhassa.notify import commands

    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "brain.db")

    async def go():
        await _db.init_db()
        return await commands.dispatch("/temple schedule", config=_cfg(tmp_path))

    reply = asyncio.run(go())
    assert "clock is empty" in reply
