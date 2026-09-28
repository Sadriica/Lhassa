"""The recurrence slot must survive suspend; lhassa.core.step.next_recurrence_due.

Regression cover for the duplicate-digest bug: a laptop that suspends past more
than one interval used to re-anchor every overdue recurring task to the wake
instant, which both walked each schedule off its wall-clock slot AND collapsed
separate schedules (08:00 + 21:00) onto the same time, so they fired as
duplicates from then on. Pure arithmetic; no DB, no clock, no network.
"""

from datetime import datetime, timedelta, timezone

from lhassa.core.step import next_recurrence_due

DAY = 24 * 60 * 60


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_ordinary_tick_advances_exactly_one_interval():
    old_due = _dt(2026, 8, 1, 8, 0)
    now = _dt(2026, 8, 1, 8, 1)  # fired a minute late, as heartbeats do
    assert next_recurrence_due(old_due, now, DAY) == _dt(2026, 8, 2, 8, 0)


def test_slot_survives_a_multi_day_suspend():
    """The whole point: 08:00 stays 08:00 after the lid was shut for days."""
    old_due = _dt(2026, 8, 1, 8, 0)
    now = _dt(2026, 8, 4, 14, 11)  # laptop reopened days later, mid-afternoon
    assert next_recurrence_due(old_due, now, DAY) == _dt(2026, 8, 5, 8, 0)


def test_separate_schedules_do_not_collapse_onto_each_other():
    """A morning and an evening schedule keep their OWN hour across downtime.

    The old ``max(now, …)`` re-anchored both to the wake instant, making them
    identical; which is exactly how the digest started arriving twice.
    """
    now = _dt(2026, 8, 4, 14, 11)
    morning = next_recurrence_due(_dt(2026, 8, 1, 8, 0), now, DAY)
    evening = next_recurrence_due(_dt(2026, 8, 1, 21, 0), now, DAY)
    assert morning != evening
    assert morning.hour == 8 and morning.minute == 0
    assert evening.hour == 21 and evening.minute == 0


def test_missed_occurrences_are_not_replayed_in_a_burst():
    """Exactly one next occurrence, in the future; never a catch-up storm."""
    old_due = _dt(2026, 8, 1, 8, 0)
    now = _dt(2026, 9, 1, 8, 0)  # a month of downtime
    nxt = next_recurrence_due(old_due, now, DAY)
    assert nxt > now
    assert nxt == _dt(2026, 9, 2, 8, 0)


def test_result_is_always_strictly_in_the_future():
    now = _dt(2026, 8, 4, 12, 0)
    for days_ago in range(0, 40):
        old_due = now - timedelta(days=days_ago)
        assert next_recurrence_due(old_due, now, DAY) > now


def test_hourly_interval_keeps_its_minute_offset():
    old_due = _dt(2026, 8, 1, 8, 30)
    now = _dt(2026, 8, 2, 15, 5)
    assert next_recurrence_due(old_due, now, 3600) == _dt(2026, 8, 2, 15, 30)


def test_non_positive_interval_degrades_instead_of_looping():
    now = _dt(2026, 8, 4, 12, 0)
    assert next_recurrence_due(_dt(2026, 8, 1, 8, 0), now, 0) == now
    assert next_recurrence_due(_dt(2026, 8, 1, 8, 0), now, -5) == now
