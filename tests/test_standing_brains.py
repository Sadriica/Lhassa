"""Standing brains (Wave 1); a project's review brains attach AUTOMATICALLY.

Covers: kind->template map + env override parsing; ``template_is_read_only``
true/false/unknown-fail-closed; the ``attach_project_template`` action attaches
idempotently (second call is a no-op via ``project_brains``); the sense mints
once per project (deduped) and only for mapped kinds; a read-only template's
attach auto-runs (``pendiente``) while a write-capable one parks
(``esperando_aprobacion``).
"""

import asyncio

import pytest

from lhassa.core import projects
from lhassa.org import templates as org_templates


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.core.actions import register_action
    from lhassa.security import allowlist as al
    from lhassa.security import signing
    from lhassa.security.trust import TrustLevel

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())
    # The integrator wires these into actionpack/__init__.py's register_all() and
    # security/allowlist.py's ALLOWLIST (out of this task's file territory); until
    # then (and to keep this test self-contained) wire them directly here.
    # min_trust=INTERNO + requires_approval=False: the (a)/(b) inputs stay neutral
    # so the tests below isolate the NEW (e) gate this task adds.
    from lhassa.actionpack._standing import attach_project_template

    register_action("attach_project_template", attach_project_template, override=True)
    monkeypatch.setitem(
        al.ALLOWLIST,
        "attach_project_template",
        al.ActionSpec(objetivos=al.WILDCARD, min_trust=TrustLevel.INTERNO, requires_approval=False),
    )
    return db


@pytest.fixture(autouse=True)
def _fresh_topology(tmp_path, monkeypatch):
    """Redirect the org topology file so template instantiation is isolated.

    ``Config.org_topology_file`` is a class attribute evaluated at import time
    (``os.getenv(...)``), so setting the env var post-import has no effect;
    the attribute itself must be monkeypatched.
    """
    from lhassa.config import Config

    monkeypatch.setattr(Config, "org_topology_file", str(tmp_path / "topology.json"))


def _create(name, **kw):
    return asyncio.run(projects.create_project("inst_personal", name, **kw))


# ── kind -> template map ────────────────────────────────────────────────────


def test_default_kind_template_map():
    m = org_templates.kind_template_map()
    assert m.get("code") == "code-review"


def test_env_override_extends_and_overrides(monkeypatch):
    monkeypatch.setenv(
        "LHASSA_KIND_TEMPLATES", "code:software-factory, research:bug-investigation"
    )
    m = org_templates.kind_template_map()
    assert m["code"] == "software-factory"
    assert m["research"] == "bug-investigation"


def test_env_override_parses_defensively(monkeypatch):
    monkeypatch.setenv("LHASSA_KIND_TEMPLATES", "garbage,,:missing-kind,code:")
    m = org_templates.kind_template_map()
    # malformed entries are skipped; the default survives untouched
    assert m.get("code") == "code-review"


# ── template_is_read_only ────────────────────────────────────────────────────


def test_template_is_read_only_true_for_code_review():
    assert org_templates.template_is_read_only("code-review") is True


def test_template_is_read_only_false_for_write_capable_template():
    assert org_templates.template_is_read_only("software-factory") is False


def test_template_is_read_only_unknown_fails_closed():
    assert org_templates.template_is_read_only("no-such-template") is False


# ── the attach_project_template ACTION ──────────────────────────────────────


def test_action_attaches_idempotently(brain):
    from lhassa.actionpack._standing import attach_project_template

    row = _create("proj-a", kind="code")
    first = asyncio.run(attach_project_template({"project_id": row["id"]}))
    assert "attached" in first

    rows = asyncio.run(brain.list_project_brains("inst_personal", row["id"]))
    assert len(rows) == 1
    assert rows[0]["template_id"] == "code-review"

    second = asyncio.run(attach_project_template({"project_id": row["id"]}))
    assert "no-op" in second
    rows_again = asyncio.run(brain.list_project_brains("inst_personal", row["id"]))
    assert len(rows_again) == 1  # unchanged


def test_action_degrades_honestly(brain):
    from lhassa.actionpack._standing import attach_project_template

    assert "no project_id" in asyncio.run(attach_project_template({}))
    assert "unknown project" in asyncio.run(
        attach_project_template({"project_id": "nope"})
    )

    generic = _create("proj-generic", kind="ops")
    out = asyncio.run(attach_project_template({"project_id": generic["id"]}))
    assert "no default template" in out

    bad_template = _create("proj-badtpl", kind="code")
    out2 = asyncio.run(
        attach_project_template(
            {"project_id": bad_template["id"], "template_id": "no-such-template"}
        )
    )
    assert "refused" in out2


def test_two_projects_get_isolated_brains(brain):
    from lhassa.actionpack._standing import attach_project_template
    from lhassa.org import topology as topo

    a = _create("proj-x", kind="code")
    b = _create("proj-y", kind="code")
    asyncio.run(attach_project_template({"project_id": a["id"]}))
    asyncio.run(attach_project_template({"project_id": b["id"]}))

    chart = topo.load_topology()
    ids = {n.id for n in chart.nodes()}
    assert f"code-review:{a['id']}:reviewer" in ids
    assert f"code-review:{b['id']}:reviewer" in ids


# ── the sense (mint once per project, only mapped kinds) ────────────────────


def _run_sense(instance_id="inst_personal"):
    from lhassa.actionpack._standing import _from_projects_without_brains

    out: list = []
    asyncio.run(_from_projects_without_brains(instance_id, out))
    return out


def test_sense_mints_for_mapped_kind_only(brain):
    _create("proj-code", kind="code")
    _create("proj-generic", kind="generic")

    out = _run_sense()
    assert out == []  # the sense emits no triggers; it mints a task

    tasks = asyncio.run(brain.list_tasks("inst_personal"))
    mine = [t for t in tasks if t["accion"] == "attach_project_template"]
    assert len(mine) == 1


def test_sense_is_deduped_via_watch_state(brain):
    _create("proj-code2", kind="code")
    _run_sense()
    _run_sense()  # second beat
    tasks = asyncio.run(brain.list_tasks("inst_personal"))
    mine = [t for t in tasks if t["accion"] == "attach_project_template"]
    assert len(mine) == 1


def test_sense_skips_projects_already_attached(brain):
    from lhassa.actionpack._standing import attach_project_template

    row = _create("proj-code3", kind="code")
    asyncio.run(attach_project_template({"project_id": row["id"]}))
    _run_sense()
    tasks = asyncio.run(brain.list_tasks("inst_personal"))
    mine = [t for t in tasks if t["accion"] == "attach_project_template"]
    assert mine == []


# ── read-only auto-runs, write-capable parks ────────────────────────────────


def test_sense_read_only_template_auto_runs(brain, monkeypatch):
    monkeypatch.setenv("LHASSA_KIND_TEMPLATES", "code:code-review")
    _create("proj-ro", kind="code")
    _run_sense()
    tasks = asyncio.run(brain.list_tasks("inst_personal"))
    mine = [t for t in tasks if t["accion"] == "attach_project_template"]
    assert len(mine) == 1
    assert mine[0]["estado"] == "pendiente"


def test_sense_write_capable_template_parks_for_approval(brain, monkeypatch):
    monkeypatch.setenv("LHASSA_KIND_TEMPLATES", "code:software-factory")
    _create("proj-wc", kind="code")
    _run_sense()
    tasks = asyncio.run(brain.list_tasks("inst_personal"))
    mine = [t for t in tasks if t["accion"] == "attach_project_template"]
    assert len(mine) == 1
    assert mine[0]["estado"] == "esperando_aprobacion"
    approvals = asyncio.run(brain.load_pending_approvals("inst_personal"))
    assert any(
        (a.get("payload") or {}).get("accion") == "attach_project_template"
        for a in approvals
    )
