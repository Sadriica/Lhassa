# SPDX-License-Identifier: AGPL-3.0-or-later
"""The status read-model shared by the REPL toolbar and the TUI status bar."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from lhassa.views import status

SNAP = {
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


def test_plain_two_lines():
    first, second = status.plain(SNAP).split("\n")
    assert first == (
        "inst_test  beat claude  chat some-model  budget [" + "█" * 10 + "] 12% 7h20m  $0.0412"
    )
    assert second == "▸▸ org live · 3 approvals · 1 tasks · last beat 6m ago · health ok"


def test_roles_are_closed():
    for snap in (SNAP, {**SNAP, "budget": 0, "health_failing": ("disk",), "org_live": False}):
        assert {r for r, _ in status.segments(snap) if r} <= status.ROLES


def test_bar_level_follows_the_soft_limit():
    def level(pct):
        return {r for r, _ in status.seat_segments({**SNAP, "pct_used": pct}) if r.startswith("bar-")}

    assert level(0.1) == {"bar-ok", "bar-empty"}
    assert level(0.85) == {"bar-warn", "bar-empty"}
    assert level(1.5) == {"bar-alert"}


def test_any_spend_shows_one_cell():
    filled = "".join(t for r, t in status.seat_segments({**SNAP, "pct_used": 0.001}) if r == "bar-ok")
    assert filled.count("█") == 1


def test_unknown_fields_render_as_question_marks():
    snap = {**SNAP, "budget": 0, "approvals": None, "tasks": None, "beat_age": None,
            "health_failing": None, "org_live": False}
    text = status.plain(snap)
    assert "budget unlimited" in text and "[" not in text
    assert "approvals ?" in text and "? tasks" in text
    assert "last beat ? ago" in text and "health ?" in text and "org offline" in text


def test_time_helpers():
    now = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
    assert status.seconds_to_utc_midnight(now) == 3600
    assert status.age_seconds("2026-01-01T22:59:00+00:00", now) == 60
    assert status.age_seconds("2026-01-01T22:59:00", now) == 60
    assert status.age_seconds("nope", now) is None
    assert [status.format_duration(x) for x in (None, 40, 720, 26400, 90000)] == [
        "?", "40s", "12m", "7h20m", "1d1h",
    ]


def test_health_failing_reads_the_digest(tmp_path):
    class Cfg:
        health_dir = str(tmp_path)

    assert status.health_failing(Cfg) is None
    (tmp_path / "digest.json").write_text(json.dumps({"results": {
        "disk": {"level": "alert"}, "temp": {"level": "ok"}, "mem": {"level": "warn"},
    }}))
    assert status.health_failing(Cfg) == ("disk", "mem")
    (tmp_path / "digest.json").write_text("[]")
    assert status.health_failing(Cfg) is None


async def test_gather_never_raises():
    snap = await status.gather("inst_test")
    assert snap["instance_id"] == "inst_test"
    assert snap["approvals"] == 0 and snap["tasks"] == 0
    assert set(SNAP) <= set(snap)
