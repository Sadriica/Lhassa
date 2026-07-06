"""Tests for lhassa.org.deliver — the UP-hop mirror of lhassa.org.delegate.

Offline + $0: a tmp brain DB (monkeypatched ``db.DB_PATH``), a seeded topology
(lhassa → general → interns, mirroring tests/test_org_delegate.py), and FAKE send
seams (``telegram_api.send_document`` / ``notify.dispatch.notify``) so nothing
touches the network. Covers:

  * a document delivery through the full chain — one Episode per brain on the
    path plus the final root outcome episode, the artifact row, and the actual
    fake send call;
  * a text-only delivery — no artifact row, still per-brain episodes;
  * a broken/missing topology — degrades to a single root-attributed hop, the
    send still happens;
  * an artifact-registration failure — send + episodes still happen, id is None;
  * a send failure — ``(False, artifact_id)`` and a "failed" final episode.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.core import artifacts, db
from lhassa.org import deliver
from lhassa.org.topology import BrainNode, Topology


# ── fixtures / builders ───────────────────────────────────────────────────────────


def _topology() -> Topology:
    """lhassa (root admin) → general → interns (a department leaf)."""
    return Topology(
        [
            BrainNode(id="lhassa", role="administrator"),
            BrainNode(id="general", role="general", parent="lhassa"),
            BrainNode(id="interns", role="department", department="interns", parent="general"),
        ]
    )


def _cfg(tmp_path):
    topo = tmp_path / "org.json"
    topo.write_text(
        '[{"id":"general","role":"general","department":null,"parent":null},'
        '{"id":"interns","role":"department","department":"interns","parent":"general"}]',
        encoding="utf-8",
    )
    return SimpleNamespace(
        instance_id="inst_personal",
        org_topology_file=str(topo),
        root_admin_id="lhassa",
        notify_channels="telegram",
    )


async def _episodes(instance_id: str) -> list[dict]:
    rows = await db.load_recent_episodes(instance_id, n=100)
    rows.reverse()  # oldest first, matching the order they were written
    return [r for r in rows if r["despertar_por"] == "org-deliver"]


# ── document delivery through the full chain ──────────────────────────────────────


@pytest.mark.asyncio
async def test_deliver_document_logs_one_episode_per_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    from lhassa.notify import telegram_api

    sent = {}

    async def fake_send_document(path, *, caption="", config=None):
        sent["path"] = str(path)
        sent["caption"] = caption
        return True

    monkeypatch.setattr(telegram_api, "send_document", fake_send_document)

    cv_pdf = tmp_path / "cv_sezzle.pdf"
    cv_pdf.write_text("pdf")
    cv_tex = tmp_path / "cv_sezzle.tex"
    cv_tex.write_text("tex")

    cfg = _cfg(tmp_path)
    delivered, artifact_id = await deliver.deliver(
        document=str(cv_pdf),
        paths=[str(cv_tex), str(cv_pdf)],
        caption="Your tailored CV",
        kind="cv",
        metadata={"company": "sezzle", "role": "AI Engineer"},
        origin_department="interns",
        config=cfg,
    )

    assert delivered is True
    assert artifact_id is not None
    assert sent["path"] == str(cv_pdf)
    assert sent["caption"] == "Your tailored CV"

    # The artifact was registered with both files.
    row = await artifacts.get("inst_personal", artifact_id)
    assert row["kind"] == "cv"
    assert row["paths"] == [str(cv_tex), str(cv_pdf)]
    assert row["origin_department"] == "interns"

    eps = await _episodes("inst_personal")
    # 3 chain hops (interns, general, lhassa) + 1 final root outcome episode.
    assert len(eps) == 4
    assert [e["dominio"] for e in eps] == ["interns", "general", "lhassa", "lhassa"]
    assert all(e["despertar_por"] == "org-deliver" for e in eps)
    assert all(float(e["costo_usd"]) == 0.0 for e in eps)

    dept_hop, mid_hop, root_intent, root_final = eps

    assert "received from interns" in dept_hop["resultado"]
    assert "cv_sezzle.tex" in dept_hop["resultado"]
    assert "cv_sezzle.pdf" in dept_hop["resultado"]
    assert "company=sezzle" in dept_hop["resultado"]
    assert "forwarding ↑ general" in dept_hop["resultado"]
    assert artifact_id in dept_hop["resultado"]

    assert "received from interns" in mid_hop["resultado"]
    assert "forwarding ↑ lhassa" in mid_hop["resultado"]

    assert "artifact" in root_intent["resultado"]
    assert artifact_id in root_intent["resultado"]
    assert "cv_sezzle.pdf" in root_intent["resultado"]
    assert "owner via telegram" in root_intent["resultado"]

    assert root_final["resultado"] == "delivered: cv_sezzle.pdf → owner (telegram, ok)"


# ── text-only delivery ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deliver_text_only_no_artifact_still_logs_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    from lhassa.notify import dispatch

    calls = []

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        calls.append(text)
        return True

    monkeypatch.setattr(dispatch, "notify", fake_notify)

    cfg = _cfg(tmp_path)
    delivered, artifact_id = await deliver.deliver(
        "hello owner", origin_department="interns", config=cfg
    )

    assert delivered is True
    assert artifact_id is None
    assert calls == ["hello owner"]

    rows = await artifacts.list_artifacts("inst_personal")
    assert rows == []

    eps = await _episodes("inst_personal")
    assert len(eps) == 4
    assert [e["dominio"] for e in eps] == ["interns", "general", "lhassa", "lhassa"]
    assert eps[-1]["resultado"] == "delivered: message → owner (log+telegram, ok)" or (
        "owner (" in eps[-1]["resultado"]
    )


# ── degrade path: broken/missing topology ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_deliver_degrades_on_broken_topology(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    def _boom(config=None):
        raise RuntimeError("topology unavailable")

    monkeypatch.setattr(topo_mod, "load_topology", _boom)

    from lhassa.notify import dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(dispatch, "notify", fake_notify)

    cfg = _cfg(tmp_path)
    delivered, artifact_id = await deliver.deliver(
        "hello", origin_department="interns", config=cfg
    )

    assert delivered is True
    eps = await _episodes("inst_personal")
    # Degraded to a single root-attributed hop + the final outcome episode.
    assert [e["dominio"] for e in eps] == ["lhassa", "lhassa"]


# ── artifact-write failure: send + episodes still happen ───────────────────────────


@pytest.mark.asyncio
async def test_deliver_survives_artifact_registration_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    async def _boom_register(*args, **kwargs):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(artifacts, "register", _boom_register)

    from lhassa.notify import telegram_api

    async def fake_send_document(path, *, caption="", config=None):
        return True

    monkeypatch.setattr(telegram_api, "send_document", fake_send_document)

    doc = tmp_path / "report.pdf"
    doc.write_text("pdf")

    cfg = _cfg(tmp_path)
    delivered, artifact_id = await deliver.deliver(
        document=str(doc), origin_department="interns", config=cfg
    )

    assert delivered is True
    assert artifact_id is None

    eps = await _episodes("inst_personal")
    assert len(eps) == 4


# ── send failure ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deliver_reports_send_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    from lhassa.notify import telegram_api

    async def fake_send_document(path, *, caption="", config=None):
        return False

    monkeypatch.setattr(telegram_api, "send_document", fake_send_document)

    doc = tmp_path / "cv.pdf"
    doc.write_text("pdf")

    cfg = _cfg(tmp_path)
    delivered, artifact_id = await deliver.deliver(
        document=str(doc), origin_department="interns", config=cfg
    )

    assert delivered is False
    assert artifact_id is not None

    eps = await _episodes("inst_personal")
    final = eps[-1]
    assert "failed" in final["resultado"]
    assert final["resultado"] == "delivered: cv.pdf → owner (telegram, failed)"
