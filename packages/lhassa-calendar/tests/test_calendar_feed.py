"""ICS feed reader; parsing, timezones, RRULE, caching. Offline; fake ICS only."""

from datetime import datetime, timedelta, timezone

import pytest

from lhassa.calendar import feed


def _win(days_back=1, days_fwd=14):
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    return now - timedelta(days=days_back), now + timedelta(days=days_fwd)


def _cal(*vevents):
    return "BEGIN:VCALENDAR\nVERSION:2.0\n" + "\n".join(vevents) + "\nEND:VCALENDAR"


def test_parse_tzid_bogota_to_utc():
    ev = (
        "BEGIN:VEVENT\nSUMMARY:Algorithms 201\n"
        "DTSTART;TZID=SA Pacific Standard Time:20260709T150000\n"
        "DTEND;TZID=SA Pacific Standard Time:20260709T163000\n"
        "LOCATION:Room 204\nEND:VEVENT"
    )
    ws, we = _win()
    out = feed.parse_events(_cal(ev), window_start=ws, window_end=we)
    assert len(out) == 1
    # 15:00 Bogotá (UTC-5) → 20:00 UTC
    assert out[0]["start"] == datetime(2026, 7, 9, 20, 0, tzinfo=timezone.utc)
    assert out[0]["summary"] == "Algorithms 201"
    assert out[0]["location"] == "Room 204"


def test_parse_utc_and_all_day():
    ev_utc = "BEGIN:VEVENT\nSUMMARY:UTC event\nDTSTART:20260709T100000Z\nDTEND:20260709T110000Z\nEND:VEVENT"
    ev_day = "BEGIN:VEVENT\nSUMMARY:Holiday\nDTSTART;VALUE=DATE:20260710\nEND:VEVENT"
    ws, we = _win()
    out = feed.parse_events(_cal(ev_utc, ev_day), window_start=ws, window_end=we)
    kinds = {e["summary"]: e for e in out}
    assert kinds["UTC event"]["start"] == datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)
    assert kinds["Holiday"]["all_day"] is True


def test_weekly_rrule_expands_in_window():
    # A class every Tuesday & Thursday, starting Jul 7 (Tue), 3 weeks out.
    ev = (
        "BEGIN:VEVENT\nSUMMARY:Física\n"
        "DTSTART;TZID=SA Pacific Standard Time:20260707T080000\n"
        "DTEND;TZID=SA Pacific Standard Time:20260707T093000\n"
        "RRULE:FREQ=WEEKLY;BYDAY=TU,TH;COUNT=4\nEND:VEVENT"
    )
    ws = datetime(2026, 7, 6, tzinfo=timezone.utc)
    we = datetime(2026, 7, 20, tzinfo=timezone.utc)
    out = feed.parse_events(_cal(ev), window_start=ws, window_end=we)
    days = sorted(e["start"].astimezone(timezone(timedelta(hours=-5))).strftime("%a %d") for e in out)
    # Tue 07, Thu 09, Tue 14, Thu 16; but COUNT=4 caps it at those four
    assert len(out) == 4
    assert "Tue 07" in days and "Thu 09" in days and "Tue 14" in days


def test_exdate_skips_an_occurrence():
    ev = (
        "BEGIN:VEVENT\nSUMMARY:Lab\n"
        "DTSTART;TZID=SA Pacific Standard Time:20260706T100000\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=3\n"
        "EXDATE;TZID=SA Pacific Standard Time:20260713T100000\nEND:VEVENT"
    )
    ws = datetime(2026, 7, 5, tzinfo=timezone.utc)
    we = datetime(2026, 7, 25, tzinfo=timezone.utc)
    out = feed.parse_events(_cal(ev), window_start=ws, window_end=we)
    starts = {e["start"].astimezone(timezone(timedelta(hours=-5))).date().isoformat() for e in out}
    assert "2026-07-06" in starts and "2026-07-20" in starts
    assert "2026-07-13" not in starts  # EXDATE removed it


def test_event_outside_window_excluded():
    ev = "BEGIN:VEVENT\nSUMMARY:Far away\nDTSTART:20270101T100000Z\nEND:VEVENT"
    ws, we = _win()
    assert feed.parse_events(_cal(ev), window_start=ws, window_end=we) == []


def test_malformed_never_raises():
    assert feed.parse_events("garbage not ical", window_start=_win()[0], window_end=_win()[1]) == []
    assert feed.parse_events("", window_start=_win()[0], window_end=_win()[1]) == []


@pytest.mark.asyncio
async def test_fetch_serves_fresh_cache_without_network(tmp_path, monkeypatch):
    cache = tmp_path / "feed_cache.ics"
    monkeypatch.setattr(feed, "feed_cache_path", lambda url: cache)
    cache.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR")

    def _boom(*a, **k):
        raise AssertionError("must not hit the network when cache is fresh")

    monkeypatch.setattr(feed.httpx, "AsyncClient", _boom)
    out = await feed.fetch_feed("https://example.com/c.ics", ttl=9999)
    assert "VCALENDAR" in out


@pytest.mark.asyncio
async def test_fetch_falls_back_to_stale_cache_on_error(tmp_path, monkeypatch):
    cache = tmp_path / "feed_cache.ics"
    monkeypatch.setattr(feed, "feed_cache_path", lambda url: cache)
    cache.write_text("STALE")
    import os
    old = __import__("time").time() - 10000
    os.utime(cache, (old, old))  # make the cache stale

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kwargs): raise RuntimeError("network down")

    monkeypatch.setattr(feed.httpx, "AsyncClient", _Client)
    out = await feed.fetch_feed("https://example.com/c.ics", ttl=1)
    assert out == "STALE"  # served the stale copy rather than nothing


@pytest.mark.asyncio
async def test_fetch_refuses_redirect_to_private_address(tmp_path, monkeypatch):
    cache = tmp_path / "feed_cache.ics"
    monkeypatch.setattr(feed, "feed_cache_path", lambda url: cache)

    class _Resp:
        status_code = 302
        is_redirect = True
        headers = {"location": "http://127.0.0.1/metadata"}
        text = ""

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kwargs): return _Resp()

    monkeypatch.setattr(feed.httpx, "AsyncClient", _Client)
    from lhassa.tools import net_safety
    monkeypatch.setattr(
        net_safety,
        "resolve_public_url",
        lambda url: (None, "private address")
        if "127.0.0.1" in url
        else ("93.184.216.34", None),
    )
    assert await feed.fetch_feed("https://example.com/c.ics", ttl=0) is None


@pytest.mark.asyncio
async def test_fetch_pins_validated_ip_while_preserving_host_and_sni(tmp_path, monkeypatch):
    monkeypatch.setattr(feed, "feed_cache_path", lambda url: tmp_path / "feed.ics")
    seen = {}

    class _Resp:
        status_code = 200
        text = "BEGIN:VCALENDAR\nEND:VCALENDAR"

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kwargs):
            seen.update(url=url, **kwargs)
            return _Resp()

    monkeypatch.setattr(feed.httpx, "AsyncClient", _Client)
    from lhassa.tools import net_safety
    monkeypatch.setattr(
        net_safety, "resolve_public_url", lambda _url: ("93.184.216.34", None)
    )

    assert await feed.fetch_feed("https://example.com/c.ics", ttl=0)
    assert seen["url"].startswith("https://93.184.216.34/")
    assert seen["headers"]["Host"] == "example.com"
    assert seen["extensions"]["sni_hostname"] == "example.com"


@pytest.mark.asyncio
async def test_unconfigured_url_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(feed, "feed_cache_path", lambda url: tmp_path / "feed_cache.ics")
    assert await feed.fetch_feed("", ttl=1) is None


# ── per-feed cache path ────────────────────────────────────────────────────────


def test_feed_cache_path_differs_per_url(tmp_path, monkeypatch):
    from lhassa.calendar import paths as _paths

    monkeypatch.setattr(_paths, "calendar_dir", lambda: tmp_path)
    p1 = _paths.feed_cache_path("https://example.com/a.ics")
    p2 = _paths.feed_cache_path("https://example.com/b.ics")
    assert p1 != p2
    assert p1.parent == tmp_path == p2.parent


def test_feed_cache_path_stable_for_same_url(tmp_path, monkeypatch):
    from lhassa.calendar import paths as _paths

    monkeypatch.setattr(_paths, "calendar_dir", lambda: tmp_path)
    assert _paths.feed_cache_path("https://example.com/a.ics") == _paths.feed_cache_path(
        "https://example.com/a.ics"
    )


# ── upcoming() across MULTIPLE named calendars ────────────────────────────────


def _ev(summary: str, start: str) -> str:
    return f"BEGIN:VEVENT\nSUMMARY:{summary}\nDTSTART:{start}Z\nEND:VEVENT"


def _soon(hours_ahead: int) -> str:
    """A UTC ``YYYYMMDDTHHMMSS`` stamp ``hours_ahead`` from real now; keeps the
    ``upcoming()`` window tests (which fetch [now, now+hours]) from rotting as
    the calendar's hardcoded dates slide into the past."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).strftime(
        "%Y%m%dT%H%M%S"
    )


@pytest.mark.asyncio
async def test_upcoming_tags_events_with_their_calendar_name(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from lhassa.calendar import store as _store

    cfg = SimpleNamespace(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url="",
        calendar_feed_ttl=3600.0,
    )
    _store.add_calendar("Work", "https://example.edu/work.ics", config=cfg)
    _store.add_calendar("Personal", "https://example.com/personal.ics", config=cfg)

    fake_bodies = {
        "https://example.edu/work.ics": _cal(_ev("Algorithms 201", _soon(3))),
        "https://example.com/personal.ics": _cal(_ev("Team sync", _soon(7))),
    }

    async def _fake_fetch(url, *, ttl=None, config=None):
        return fake_bodies.get(url)

    monkeypatch.setattr(feed, "fetch_feed", _fake_fetch)

    events = await feed.upcoming("inst_x", hours=48, config=cfg)
    by_summary = {e["summary"]: e["calendar"] for e in events}
    assert by_summary == {"Algorithms 201": "Work", "Team sync": "Personal"}
    # sorted by start
    assert [e["summary"] for e in events] == ["Algorithms 201", "Team sync"]


@pytest.mark.asyncio
async def test_upcoming_skips_a_failing_feed_others_still_show(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from lhassa.calendar import store as _store

    cfg = SimpleNamespace(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url="",
        calendar_feed_ttl=3600.0,
    )
    _store.add_calendar("Broken", "https://example.edu/broken.ics", config=cfg)
    _store.add_calendar("Good", "https://example.com/good.ics", config=cfg)

    async def _fake_fetch(url, *, ttl=None, config=None):
        if "broken" in url:
            raise RuntimeError("network down")
        return _cal(_ev("Standup", _soon(4)))

    monkeypatch.setattr(feed, "fetch_feed", _fake_fetch)

    events = await feed.upcoming("inst_x", hours=48, config=cfg)
    assert len(events) == 1
    assert events[0]["calendar"] == "Good"


@pytest.mark.asyncio
async def test_upcoming_no_feeds_configured_is_empty(tmp_path):
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url="",
        calendar_feed_ttl=3600.0,
    )
    assert await feed.upcoming("inst_x", hours=48, config=cfg) == []


@pytest.mark.asyncio
async def test_upcoming_per_feed_caches_dont_collide(tmp_path, monkeypatch):
    """Two feeds fetched for real (through fetch_feed, not stubbed) must write to
    DIFFERENT cache files; proving the hash-keyed cache path actually varies."""
    from lhassa.calendar import paths as _paths

    monkeypatch.setattr(_paths, "calendar_dir", lambda: tmp_path)

    class _Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            body = {
                "example.edu": _cal(_ev("A", "20260709T100000")),
                "example.com": _cal(_ev("B", "20260709T110000")),
            }[kwargs["headers"]["Host"]]
            return _Resp(body)

    monkeypatch.setattr(feed.httpx, "AsyncClient", _Client)
    from lhassa.tools import net_safety
    monkeypatch.setattr(
        net_safety, "resolve_public_url", lambda _url: ("93.184.216.34", None)
    )

    await feed.fetch_feed("https://example.edu/a.ics", ttl=0)
    await feed.fetch_feed("https://example.com/b.ics", ttl=0)

    cache_a = _paths.feed_cache_path("https://example.edu/a.ics")
    cache_b = _paths.feed_cache_path("https://example.com/b.ics")
    assert cache_a != cache_b
    assert cache_a.exists() and cache_b.exists()
    assert "A" in cache_a.read_text()
    assert "B" in cache_b.read_text()
