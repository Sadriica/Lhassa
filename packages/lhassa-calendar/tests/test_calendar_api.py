"""Calendar API tests. TestClient over a fresh app mounting only the calendar router.

Mirrors ``tests/test_flow_api.py``'s pattern: mount the router alone (not the full
server) so these tests exercise exactly the routes the integrator includes via
``app.include_router(router)``, independent of its wiring.

SECURITY invariant asserted throughout: no response body from any route ever
contains a real feed url; only ``url_masked`` (a short non-reversible tail).
"""

from datetime import datetime, timedelta, timezone

import pytest

from lhassa import actionpack
from lhassa.extensions import PACKS

# The calendar router is served by the api pack (it brings fastapi); without that
# pack installed there is nothing to mount it on, so these tests skip.
actionpack.register_all()
if "api" not in PACKS:
    pytest.skip("needs the api pack (lhassa-api)", allow_module_level=True)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from lhassa.calendar.surfaces import api as calendar_routes  # noqa: E402

FAKE_URL_WORK = "https://calendar.example.com/secret-work-feed-abcdef1234567890.ics"
FAKE_URL_PERSONAL = "https://calendar.example.com/secret-personal-feed-zzzz999999.ics"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    calendars_file = tmp_path / "calendars.json"
    monkeypatch.setattr(config_mod.Config, "calendars_file", str(calendars_file))
    monkeypatch.setattr(config_mod.Config, "calendar_ics_url", "", raising=False)

    app = FastAPI()
    app.include_router(calendar_routes.router)
    return TestClient(app)


def _assert_no_real_url(payload) -> None:
    """Recursively walk a response payload and assert neither fake url ever appears."""
    text = str(payload)
    assert FAKE_URL_WORK not in text
    assert FAKE_URL_PERSONAL not in text


# ── calendars CRUD ──────────────────────────────────────────────────────────────


def test_add_list_masks_url(client):
    r = client.post("/api/calendar/calendars", json={"name": "Work", "url": FAKE_URL_WORK})
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_no_real_url(body)
    assert body == [{"name": "Work", "url_masked": body[0]["url_masked"], "primary": False}]
    assert body[0]["url_masked"] != FAKE_URL_WORK

    r = client.get("/api/calendar/calendars")
    assert r.status_code == 200
    _assert_no_real_url(r.json())
    assert r.json()[0]["name"] == "Work"


def test_add_rejects_bad_url(client):
    r = client.post("/api/calendar/calendars", json={"name": "Bad", "url": "not-a-url"})
    assert r.status_code == 400
    assert "url" in r.json()["detail"]


def test_add_rejects_missing_fields(client):
    r = client.post("/api/calendar/calendars", json={"name": "", "url": ""})
    assert r.status_code == 400


def test_remove_calendar(client):
    client.post("/api/calendar/calendars", json={"name": "Work", "url": FAKE_URL_WORK})
    r = client.delete("/api/calendar/calendars/Work")
    assert r.status_code == 200
    assert r.json() == {"removed": "Work"}
    assert client.get("/api/calendar/calendars").json() == []


def test_remove_missing_calendar_404(client):
    r = client.delete("/api/calendar/calendars/nope")
    assert r.status_code == 404


def test_set_primary_marks_exactly_one(client):
    client.post("/api/calendar/calendars", json={"name": "Work", "url": FAKE_URL_WORK})
    client.post("/api/calendar/calendars", json={"name": "Personal", "url": FAKE_URL_PERSONAL})

    r = client.post("/api/calendar/primary", json={"name": "Personal"})
    assert r.status_code == 200
    body = r.json()
    _assert_no_real_url(body)
    primaries = [c for c in body if c["primary"]]
    assert len(primaries) == 1
    assert primaries[0]["name"] == "Personal"

    # Switching primary keeps exactly one marked.
    r = client.post("/api/calendar/primary", json={"name": "Work"})
    body = r.json()
    primaries = [c for c in body if c["primary"]]
    assert len(primaries) == 1
    assert primaries[0]["name"] == "Work"


def test_set_primary_missing_404(client):
    r = client.post("/api/calendar/primary", json={"name": "ghost"})
    assert r.status_code == 404


# ── events ───────────────────────────────────────────────────────────────────────


def test_events_empty_with_no_calendars(client):
    r = client.get("/api/calendar/events")
    assert r.status_code == 200
    assert r.json() == []


def test_events_shape_from_fake_feed(client, monkeypatch):
    now = datetime.now(timezone.utc)

    async def fake_upcoming(instance_id, *, hours=48, config=None):
        return [
            {
                "summary": "Team sync",
                "start": now + timedelta(hours=1),
                "end": now + timedelta(hours=2),
                "location": "Room 4",
                "calendar": "Work",
                "all_day": False,
            }
        ]

    import lhassa.calendar.feed as feed_mod

    monkeypatch.setattr(feed_mod, "upcoming", fake_upcoming)

    r = client.get("/api/calendar/events?hours=24")
    assert r.status_code == 200
    body = r.json()
    _assert_no_real_url(body)
    assert body == [
        {
            "summary": "Team sync",
            "start": body[0]["start"],
            "end": body[0]["end"],
            "location": "Room 4",
            "calendar": "Work",
            "all_day": False,
        }
    ]
    assert isinstance(body[0]["start"], str)


def test_events_feed_error_degrades_to_empty(client, monkeypatch):
    async def boom(instance_id, *, hours=48, config=None):
        raise RuntimeError("feed unreachable")

    import lhassa.calendar.feed as feed_mod

    monkeypatch.setattr(feed_mod, "upcoming", boom)

    r = client.get("/api/calendar/events")
    assert r.status_code == 200
    assert r.json() == []


# ── sync ─────────────────────────────────────────────────────────────────────────


def test_sync_returns_summary_string(client, monkeypatch):
    async def fake_sync(params):
        return "calendar_sync: 2 unsynced event(s), 2 delivered"

    import lhassa.calendar.surfaces.actions as sync_mod

    monkeypatch.setattr(sync_mod, "calendar_sync", fake_sync)

    r = client.post("/api/calendar/sync")
    assert r.status_code == 200
    body = r.json()
    assert body == {"summary": "calendar_sync: 2 unsynced event(s), 2 delivered"}


def test_sync_no_primary_degrades_cleanly(client):
    # No calendars configured at all → the real calendar_sync path (no monkeypatch)
    # must degrade to a clean message, never raise.
    r = client.post("/api/calendar/sync")
    assert r.status_code == 200
    assert isinstance(r.json()["summary"], str)
    assert "no primary calendar" in r.json()["summary"] or "sync" in r.json()["summary"]
