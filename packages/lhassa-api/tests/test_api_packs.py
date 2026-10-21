"""``GET /api/packs`` and the dashboard-dist resolution (api pack)."""

from pathlib import Path

from fastapi.testclient import TestClient

from lhassa.api import _shared
from lhassa.extensions import PACKS


def test_packs_endpoint_lists_loaded_manifests():
    from lhassa.api.server import app

    res = TestClient(app).get("/api/packs")
    assert res.status_code == 200
    packs = res.json()["packs"]
    names = [p["name"] for p in packs]
    assert names == sorted(names)
    assert "api" in names
    assert set(names) == set(PACKS.items())
    assert all(p["summary"] for p in packs)


def test_dashboard_dist_env_override_wins(tmp_path):
    got = _shared.resolve_dashboard_dist(env={"LHASSA_DASHBOARD_DIST": str(tmp_path / "d")})
    assert got == tmp_path / "d"


def test_dashboard_dist_found_from_a_nested_checkout(tmp_path):
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard" / "package.json").write_text("{}")
    nested = tmp_path / "packages" / "lhassa-api" / "src" / "lhassa" / "api" / "_shared.py"
    got = _shared.resolve_dashboard_dist(start=nested, env={})
    assert got == tmp_path / "dashboard" / "dist"


def test_dashboard_dist_falls_back_to_cwd(tmp_path):
    got = _shared.resolve_dashboard_dist(start=Path("/nonexistent/a/b.py"), env={}, cwd=tmp_path)
    assert got == tmp_path / "dashboard" / "dist"


def test_this_checkout_resolves_the_root_dashboard():
    repo_root = Path(__file__).resolve().parents[3]
    assert _shared.resolve_dashboard_dist(env={}) == repo_root / "dashboard" / "dist"
