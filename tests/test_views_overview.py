"""The overview read-model (``lhassa.views.overview``) in core: ``lhassa status``
renders it with or without the api pack. Offline: tmp brain DB, gateway ledger
and topology; the endpoint itself is tested in the api pack."""

import asyncio
import json

from lhassa.views import overview


def _setup(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.llm import gateway as gw_mod

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(gw_mod.gateway, "db_path", tmp_path / "gw.db")
    monkeypatch.setattr(gw_mod.gateway, "_initialized", False)
    topo = tmp_path / "org.json"
    topo.write_text(json.dumps([{"id": "lhassa", "role": "general", "parent": None}]))
    monkeypatch.setenv("LHASSA_ORG_TOPOLOGY_FILE", str(topo))


def test_build_overview_has_every_section(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from lhassa.health import checks

    monkeypatch.setattr(checks, "run_all", lambda notify=False: {"worst_level": "info", "ok": True})
    data = asyncio.run(overview.build_overview())
    for name in overview._OVERVIEW_SECTIONS:
        assert name in data
    assert data["approvals"] == {"pending": 0, "items": []}
    assert "instance_id" in data and "generated_at" in data


def test_build_overview_reports_a_failing_section_without_raising(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from lhassa.health import checks

    def _boom(*a, **k):
        raise RuntimeError("sensors offline")

    monkeypatch.setattr(checks, "run_all", _boom)
    data = asyncio.run(overview.build_overview())
    assert "sensors offline" in data["errors"]["health"]
    assert data["health"] == {"worst_level": "info", "ok": True}
