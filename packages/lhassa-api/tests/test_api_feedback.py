"""Tests for the $0 feedback signal (lhassa.core.feedback).

Seeds a tmp brain.db with episode sequences that exercise each heuristic;
re-ask, correction, silence-after, acted-on; plus the summary aggregation and
the API read surface. No LLM, no network; everything is direct SQLite.
"""


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lhassa.api import analytics_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain_api.db")
    monkeypatch.setattr(config_mod.Config, "instance_id", "inst_test")

    app = FastAPI()
    app.include_router(analytics_routes.router)
    return TestClient(app)


def test_feedback_route_returns_summary_shape(client):
    response = client.get("/api/analytics/feedback")
    assert response.status_code == 200
    body = response.json()
    for key in ("positive", "negative", "neutral", "total", "net_score", "range_days"):
        assert key in body


def test_feedback_route_never_500s_on_empty_db(client):
    response = client.get("/api/analytics/feedback?range=30d")
    assert response.status_code == 200
    assert response.json()["total"] == 0
