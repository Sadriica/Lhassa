"""Tests for lhassa.calendar.ics — pure-stdlib RFC5545 ``.ics`` generation.

Offline + $0 by construction (no network, no model, no real clock read inside
the module under test — every datetime is supplied by the caller).
"""

from datetime import datetime, timedelta, timezone

from lhassa.calendar import ics


# ── build_event ───────────────────────────────────────────────────────────────


def test_build_event_has_the_right_summary_dtstart_dtend():
    start = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)
    content = ics.build_event("dentist appointment", start)

    assert "BEGIN:VCALENDAR" in content
    assert "VERSION:2.0" in content
    assert "BEGIN:VEVENT" in content
    assert "SUMMARY:dentist appointment" in content
    assert "DTSTART:20260708T150000Z" in content
    # default end = start + 1h
    assert "DTEND:20260708T160000Z" in content
    assert "END:VEVENT" in content
    assert "END:VCALENDAR" in content


def test_build_event_honors_explicit_end_description_location():
    start = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    content = ics.build_event(
        "dentist appointment",
        start,
        end_dt=end,
        description="cleaning + checkup",
        location="123 Main St",
    )
    assert "DTEND:20260708T153000Z" in content
    assert "DESCRIPTION:cleaning + checkup" in content
    assert "LOCATION:123 Main St" in content


def test_build_event_escapes_special_characters():
    start = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)
    content = ics.build_event(
        "call, re: taxes; urgent",
        start,
        description="line one\nline two",
    )
    assert "SUMMARY:call\\, re: taxes\\; urgent" in content
    assert "DESCRIPTION:line one\\nline two" in content


def test_build_event_uid_stable_for_same_seed():
    start = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)
    c1 = ics.build_event("dentist appointment", start)
    c2 = ics.build_event("dentist appointment", start)
    assert c1 == c2

    def _uid(content: str) -> str:
        for line in content.splitlines():
            if line.startswith("UID:"):
                return line
        return ""

    assert _uid(c1) == _uid(c2)

    # a different summary or start time yields a different UID
    c3 = ics.build_event("something else", start)
    assert _uid(c1) != _uid(c3)

    c4 = ics.build_event("dentist appointment", start + timedelta(hours=1))
    assert _uid(c1) != _uid(c4)

    # an explicit uid always wins
    c5 = ics.build_event("dentist appointment", start, uid="fixed-uid@lhassa")
    assert "UID:fixed-uid@lhassa" in c5


def test_build_event_times_are_utc_z_from_naive_and_aware_input():
    # naive datetime is treated as already-UTC, never guessed against local tz
    naive = datetime(2026, 7, 8, 15, 0)
    content = ics.build_event("x", naive)
    assert "DTSTART:20260708T150000Z" in content

    # an aware, non-UTC datetime is converted to UTC
    from datetime import timezone as tz

    minus5 = tz(timedelta(hours=-5))
    aware = datetime(2026, 7, 8, 10, 0, tzinfo=minus5)  # 15:00 UTC
    content2 = ics.build_event("x", aware)
    assert "DTSTART:20260708T150000Z" in content2


def test_build_event_never_raises_on_bad_input():
    # a start_dt that can't support arithmetic degrades to "" rather than raising
    assert ics.build_event("x", "not-a-datetime") == ""


# ── write_ics ─────────────────────────────────────────────────────────────────


def test_write_ics_writes_atomically_and_returns_ics_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))

    start = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)
    content = ics.build_event("dentist appointment", start)
    path = ics.write_ics(content, name="dentist appointment")

    assert path.suffix == ".ics"
    assert path.exists()
    with open(path, "r", encoding="utf-8", newline="") as fh:
        assert fh.read() == content
    assert path.parent == (tmp_path / "cal")
    # no leftover temp files
    assert not list(path.parent.glob(".ics-*"))


def test_write_ics_filename_is_a_slug_of_the_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    content = ics.build_event("x", datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc))
    path = ics.write_ics(content, name="Dentist Appointment!!")
    assert "dentist-appointment" in path.name
