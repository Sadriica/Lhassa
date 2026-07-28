"""Projects — the first-class workspace entity (Wave 0). Fully offline.

Covers: migration 004 lands the table; CRUD + resolve semantics (id > name >
deepest path); duplicate-name refusal; archive-not-delete; the create_project
ACTION; and the detector sense — a configured repo with no project row mints a
MUNDO-origin proposal (parked at the approval frontier, never auto-created),
deduped via watch_state.
"""

import asyncio

import pytest

from lhassa.core import projects


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())
    return db


def _create(name, **kw):
    return asyncio.run(projects.create_project("inst_personal", name, **kw))


def test_create_get_list_roundtrip(brain, tmp_path):
    row = _create("lhassa", kind="code", path=str(tmp_path))
    assert row["id"].startswith("prj")
    got = asyncio.run(projects.get_project("inst_personal", row["id"]))
    assert got["name"] == "lhassa" and got["kind"] == "code"
    assert got["path"] == str(tmp_path)
    listed = asyncio.run(projects.list_projects("inst_personal"))
    assert [p["id"] for p in listed] == [row["id"]]


def test_duplicate_active_name_is_refused(brain):
    _create("uno")
    with pytest.raises(ValueError):
        _create("uno")


def test_resolve_id_beats_name_beats_path(brain, tmp_path):
    outer = _create("outer", path=str(tmp_path))
    nested_dir = tmp_path / "inner"
    nested_dir.mkdir()
    inner = _create("inner", path=str(nested_dir))

    assert asyncio.run(projects.resolve_project("inst_personal", outer["id"]))["id"] == outer["id"]
    assert asyncio.run(projects.resolve_project("inst_personal", "inner"))["id"] == inner["id"]
    # A file inside the nested project resolves to the DEEPEST root.
    deep = nested_dir / "src" / "x.py"
    hit = asyncio.run(projects.resolve_project("inst_personal", str(deep)))
    assert hit["id"] == inner["id"]
    # Outside both → None; garbage → None (never raises).
    assert asyncio.run(projects.resolve_project("inst_personal", "/nowhere/else")) is None
    assert asyncio.run(projects.resolve_project("inst_personal", "")) is None


def test_archive_hides_from_active_but_keeps_the_row(brain):
    row = _create("done-proj")
    assert asyncio.run(projects.archive_project("inst_personal", row["id"]))
    assert asyncio.run(projects.list_projects("inst_personal")) == []
    everything = asyncio.run(projects.list_projects("inst_personal", status=None))
    assert everything[0]["status"] == "archived"


# ── the create_project ACTION ─────────────────────────────────────────────────


def test_action_creates_and_derives_name_from_path(brain, tmp_path):
    from lhassa.actionpack._projects import create_project_action

    repo = tmp_path / "mi-repo"
    repo.mkdir()
    out = asyncio.run(create_project_action({"path": str(repo), "kind": "code"}))
    assert "mi-repo" in out and "created" in out
    hit = asyncio.run(projects.resolve_project("inst_personal", str(repo)))
    assert hit is not None and hit["kind"] == "code"


def test_action_degrades_honestly(brain):
    from lhassa.actionpack._projects import create_project_action

    assert "no name" in asyncio.run(create_project_action({}))
    asyncio.run(create_project_action({"name": "dup"}))
    assert "refused" in asyncio.run(create_project_action({"name": "dup"}))


# ── the detector sense (propose, never auto-create) ───────────────────────────


def _run_sense(monkeypatch, brain, repo_path):
    import lhassa.config as config_mod
    from lhassa.actionpack._projects import _from_unregistered_repos

    monkeypatch.setattr(config_mod.Config, "auto_sync_repos", str(repo_path))
    monkeypatch.setattr(config_mod.Config, "instance_id", "inst_personal", raising=False)
    out: list = []
    asyncio.run(_from_unregistered_repos("inst_personal", out))
    return out


def test_sense_proposes_with_mundo_origin_parked_for_approval(brain, tmp_path, monkeypatch):
    import lhassa.core.db as db

    repo = tmp_path / "nuevo-repo"
    repo.mkdir()
    out = _run_sense(monkeypatch, brain, repo)
    assert out == []  # the sense emits no triggers — it parks a proposal
    # NOT created:
    assert asyncio.run(projects.resolve_project("inst_personal", str(repo))) is None
    # A create_project task exists, waiting for the owner.
    tasks = asyncio.run(db.list_tasks("inst_personal"))
    mine = [t for t in tasks if t["accion"] == "create_project"]
    assert len(mine) == 1
    assert mine[0]["estado"] == "esperando_aprobacion"
    approvals = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert any(
        (a.get("payload") or {}).get("accion") == "create_project" for a in approvals
    )


def test_sense_is_deduped_via_watch_state(brain, tmp_path, monkeypatch):
    import lhassa.core.db as db

    repo = tmp_path / "otro-repo"
    repo.mkdir()
    _run_sense(monkeypatch, brain, repo)
    _run_sense(monkeypatch, brain, repo)  # second beat
    tasks = asyncio.run(db.list_tasks("inst_personal"))
    assert len([t for t in tasks if t["accion"] == "create_project"]) == 1


def test_sense_marks_known_projects_and_skips(brain, tmp_path, monkeypatch):
    import lhassa.core.db as db

    repo = tmp_path / "ya-registrado"
    repo.mkdir()
    _create("ya-registrado", kind="code", path=str(repo))
    _run_sense(monkeypatch, brain, repo)
    tasks = asyncio.run(db.list_tasks("inst_personal"))
    assert [t for t in tasks if t["accion"] == "create_project"] == []
