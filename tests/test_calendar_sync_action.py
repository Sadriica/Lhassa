"""Tests for the ``calendar_sync`` action (lhassa/actionpack/_calendar_sync.py).

Offline: :func:`lhassa.calendar.reconcile.find_unsynced` is monkeypatched to
return fixed candidates (no real feed fetch), and
:func:`lhassa.org.deliver.deliver` is faked so no real Telegram send happens —
this test only asserts the CALL, matching the guard in ``tests/test_deliver.py``
that ``send_document`` is reachable only through ``org/deliver.py``.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from lhassa.actionpack import _calendar_sync
from lhassa.calendar import store


def _cfg(tmp_path, **over):
    base = dict(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url="",
        instance_id="inst_personal",
        calendar_dir=str(tmp_path / "cal"),
    )
    base.update(over)
    return SimpleNamespace(**base)


_NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


def _candidate(summary="Team meeting", start=None, source="Personal"):
    return {
        "summary": summary,
        "start": start or (_NOW + timedelta(hours=3)),
        "end": (start or (_NOW + timedelta(hours=3))) + timedelta(hours=1),
        "location": "",
        "source_calendar": source,
    }


def _fake_deliver(calls):
    async def fake(*, document=None, caption="", kind="message", metadata=None,
                    origin_department="", config=None, **kwargs):
        calls.append(
            {
                "document": document,
                "caption": caption,
                "kind": kind,
                "metadata": metadata,
                "origin_department": origin_department,
            }
        )
        return True, "art_fake"

    return fake


def test_no_primary_is_a_clean_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    # no primary set

    out = asyncio.run(_calendar_sync.calendar_sync({"config": cfg}))
    assert "no primary" in out.lower()


def test_no_candidates_is_a_clean_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    async def fake_find_unsynced(instance_id, *, hours=168, config=None):
        return []

    monkeypatch.setattr(_calendar_sync._reconcile, "find_unsynced", fake_find_unsynced)

    out = asyncio.run(_calendar_sync.calendar_sync({"config": cfg}))
    assert "0 unsynced" in out


def test_delivers_each_candidate_via_deliver(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    candidates = [_candidate("Team meeting"), _candidate("Standup", source="Work")]

    async def fake_find_unsynced(instance_id, *, hours=168, config=None):
        return candidates

    monkeypatch.setattr(_calendar_sync._reconcile, "find_unsynced", fake_find_unsynced)

    calls: list = []
    from lhassa.org import deliver as deliver_mod

    monkeypatch.setattr(deliver_mod, "deliver", _fake_deliver(calls))

    out = asyncio.run(_calendar_sync.calendar_sync({"config": cfg}))

    assert "2 unsynced" in out
    assert "2 delivered" in out
    assert len(calls) == 2
    for call in calls:
        assert call["kind"] == "calendar_event"
        assert str(call["document"]).endswith(".ics")
        assert call["metadata"]["primary"] == "Work"


def test_second_run_dedupes_via_watch_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    candidates = [_candidate("Team meeting")]

    async def fake_find_unsynced(instance_id, *, hours=168, config=None):
        return candidates

    monkeypatch.setattr(_calendar_sync._reconcile, "find_unsynced", fake_find_unsynced)

    calls: list = []
    from lhassa.org import deliver as deliver_mod

    monkeypatch.setattr(deliver_mod, "deliver", _fake_deliver(calls))

    out1 = asyncio.run(_calendar_sync.calendar_sync({"config": cfg}))
    assert "1 delivered" in out1
    assert len(calls) == 1

    out2 = asyncio.run(_calendar_sync.calendar_sync({"config": cfg}))
    assert "1 unsynced" in out2
    assert "0 delivered" in out2
    assert len(calls) == 1  # no second send


def test_delivery_failure_does_not_mark_delivered(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    candidates = [_candidate("Team meeting")]

    async def fake_find_unsynced(instance_id, *, hours=168, config=None):
        return candidates

    monkeypatch.setattr(_calendar_sync._reconcile, "find_unsynced", fake_find_unsynced)

    async def failing_deliver(**kwargs):
        return False, None

    from lhassa.org import deliver as deliver_mod

    monkeypatch.setattr(deliver_mod, "deliver", failing_deliver)

    out = asyncio.run(_calendar_sync.calendar_sync({"config": cfg}))
    assert "0 delivered" in out


def test_never_raises_on_reconcile_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    async def boom(instance_id, *, hours=168, config=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(_calendar_sync._reconcile, "find_unsynced", boom)

    out = asyncio.run(_calendar_sync.calendar_sync({"config": cfg}))
    assert "error" in out.lower()
