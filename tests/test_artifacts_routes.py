"""Artifacts read-surface tests; the ``lhassa artifacts`` CLI sub-app.

The API router (``GET /api/artifacts``) is tested in the api pack
(``packages/lhassa-api/tests/test_api_artifacts.py``). CLI tests use Typer's
``CliRunner`` against the real ``app``, the same pattern as
``tests/test_flow_templates.py``. The surface is a pure read over
:mod:`lhassa.core.artifacts` (covered on its own in ``tests/test_artifacts.py``);
artifacts are seeded here directly through ``core.artifacts.register``: the same
seam :mod:`lhassa.org.deliver` uses; never through the CLI (it never writes).
"""

import asyncio
import json

from typer.testing import CliRunner

from lhassa.cli.app import app as cli_app

runner = CliRunner()


def _use_tmp_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    return db


async def _seed(db, *, instance_id: str, count: int = 2):
    from lhassa.core import artifacts

    ids = []
    for i in range(count):
        artifact_id = await artifacts.register(
            instance_id,
            kind="cv",
            paths=[f"/tmp/cv-{i}.tex", f"/tmp/cv-{i}.pdf"],
            metadata={"company": f"Acme{i}", "role": "Backend Engineer"},
            origin_department="interns",
        )
        ids.append(artifact_id)
    return ids


# ── CLI ───────────────────────────────────────────────────────────────────────────


def test_cli_artifacts_list_and_show(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    db = _use_tmp_db(tmp_path, monkeypatch)
    ids = asyncio.run(_seed(db, instance_id=config_mod.Config.instance_id, count=1))

    res_list = runner.invoke(cli_app, ["artifacts", "list", "--json"])
    assert res_list.exit_code == 0, res_list.output
    listed = json.loads(res_list.output)
    assert any(a["id"] == ids[0] for a in listed)

    res_show = runner.invoke(cli_app, ["artifacts", "show", ids[0], "--json"])
    assert res_show.exit_code == 0, res_show.output
    shown = json.loads(res_show.output)
    assert shown["id"] == ids[0]
    assert shown["kind"] == "cv"
    assert shown["metadata"]["company"] == "Acme0"


def test_cli_artifacts_list_empty(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    res = runner.invoke(cli_app, ["artifacts", "list", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == []


def test_cli_artifacts_show_missing_exits_nonzero(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    res = runner.invoke(cli_app, ["artifacts", "show", "ghost"])
    assert res.exit_code == 1
