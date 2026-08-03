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

    assert "produced:" in dept_hop["resultado"]  # the origin brain produced it
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


# ── the invariant is GUARDED, not just documented ─────────────────────────────────


def test_send_document_is_reachable_only_through_deliver():
    """The enforcement behind the standing principle: an artifact (a file) may
    reach the owner ONLY through org/deliver.py. A future flow or brain that
    calls telegram_api.send_document directly — bypassing the per-brain
    delivery ledger — fails this test. Documentation is not enforcement; this is.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "lhassa"
    offenders = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        if rel in ("org/deliver.py", "notify/telegram_api.py"):
            continue  # the seam itself and the primitive it wraps
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name == "send_document":
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "send_document called outside org/deliver.py — a file reaching the owner "
        "must bubble UP through the hierarchy (see doc CLAUDE.md standing "
        f"principle). Offenders: {offenders}"
    )


@pytest.mark.asyncio
async def test_deliver_walks_arbitrary_depth(tmp_path, monkeypatch):
    """The per-brain ledger is depth-independent: a 5-level chart logs 5 hops,
    each attributed — so a FUTURE deeper org chart is respected with no seam
    change (deliver reuses delegate.command_path)."""
    import lhassa.core.db as _db

    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "brain.db")
    await _db.init_db()

    # A deep synthetic topology: lhassa → div → area → dept → cell.
    from lhassa.org import deliver as deliver_mod

    monkeypatch.setattr(
        deliver_mod, "_resolve_chain",
        lambda origin_department, config: ["lhassa", "div", "area", "dept", "cell"],
    )
    sent = []

    async def fake_send_document(path, *, caption="", config=None):
        sent.append(path)
        return True

    import lhassa.notify.telegram_api as _tapi
    monkeypatch.setattr(_tapi, "send_document", fake_send_document)

    ok, art = await deliver_mod.deliver(
        document=str(tmp_path / "r.pdf"),
        kind="report", origin_department="cell", config=None,
    )
    rows = await _db.load_recent_episodes(_db_instance(), 20)
    hops = [r for r in rows if r["despertar_por"] == "org-deliver"]
    domains = [h["dominio"] for h in hops]
    # 5 brains on the chain → 4 forward/receive hops + the final outcome = 5+1
    assert domains.count("cell") == 1 and domains.count("dept") == 1
    assert domains.count("area") == 1 and domains.count("div") == 1
    assert domains.count("lhassa") == 2  # intent + outcome
    # deepest brain "produced", every level above "received from" its child
    cell = next(h for h in hops if h["dominio"] == "cell")
    assert "produced:" in cell["resultado"]
    dept = next(h for h in hops if h["dominio"] == "dept")
    assert "received from cell" in dept["resultado"]


def _db_instance():
    from lhassa.config import Config
    return Config.instance_id
