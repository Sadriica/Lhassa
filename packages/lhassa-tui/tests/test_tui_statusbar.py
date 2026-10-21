# SPDX-License-Identifier: AGPL-3.0-or-later
"""The TUI status bar: pure rendering, duration format, never-raising gather."""

from __future__ import annotations

from datetime import datetime, timezone

from lhassa.tui import statusbar
from lhassa.tui.colors import DARK

BASE = {
    "instance_id": "inst_test",
    "heartbeat_seat": "claude",
    "model": "some-model",
    "budget": 1000,
    "pct_used": 0.12,
    "soft_pct": 0.8,
    "reset_seconds": 26400,
    "cost_usd": 0.0412,
    "org_live": True,
    "approvals": 3,
    "tasks": 1,
    "beat_age": 360,
    "health_failing": (),
}


def _bar_style(text, char="█"):
    for span in text.spans:
        if text.plain[span.start : span.end].startswith(char):
            return str(span.style)
    return ""


def test_render_two_lines_with_seat_budget_and_mode():
    plain = statusbar.render(BASE, DARK).plain
    first, second = plain.split("\n")
    assert first.startswith("inst_test")
    assert "beat claude" in first and "chat some-model" in first
    assert "12%" in first and "7h20m" in first and "$0.0412" in first
    assert second == "▸▸ org live · 3 approvals · 1 tasks · last beat 6m ago · health ok"


def test_bar_color_follows_soft_limit():
    assert DARK["lh-green"] in _bar_style(statusbar.render(BASE, DARK))
    assert DARK["lh-gold"] in _bar_style(statusbar.render({**BASE, "pct_used": 0.85}, DARK))
    assert DARK["lh-red"] in _bar_style(statusbar.render({**BASE, "pct_used": 1.2}, DARK))


def test_bar_width_is_fixed_and_any_spend_shows():
    plain = statusbar.render({**BASE, "pct_used": 0.001}, DARK).plain
    inner = plain[plain.index("[") + 1 : plain.index("]")]
    assert len(inner) == statusbar.BAR_WIDTH


def test_unlimited_budget_and_unknown_fields_degrade():
    snap = {
        **BASE,
        "budget": 0,
        "org_live": False,
        "approvals": None,
        "tasks": None,
        "beat_age": None,
        "health_failing": None,
    }
    plain = statusbar.render(snap, DARK).plain
    assert "budget unlimited" in plain and "[" not in plain
    assert "org offline" in plain and "approvals ?" in plain
    assert "last beat ? ago" in plain and "health ?" in plain


def test_failing_health_is_named():
    plain = statusbar.render({**BASE, "health_failing": ("disk", "temp")}, DARK).plain
    assert "health: disk, temp" in plain


def test_format_duration():
    assert statusbar.format_duration(None) == "?"
    assert statusbar.format_duration(40) == "40s"
    assert statusbar.format_duration(720) == "12m"
    assert statusbar.format_duration(26400) == "7h20m"
    assert statusbar.format_duration(90000) == "1d1h"


def test_reset_and_age_helpers():
    now = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
    assert statusbar._seconds_to_utc_midnight(now) == 3600
    assert statusbar._age_seconds("2026-01-01T22:59:00+00:00", now) == 60
    assert statusbar._age_seconds("2026-01-01T22:59:00", now) == 60
    assert statusbar._age_seconds("not a date", now) is None


async def test_gather_never_raises_on_an_empty_db():
    snap = await statusbar.gather("inst_test")
    assert snap["instance_id"] == "inst_test"
    assert snap["approvals"] == 0 and snap["tasks"] == 0
    assert set(BASE) <= set(snap)
