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
    first, second = status.plain(SNAP, "en").split("\n")
    sep = status.SEP
    assert first == sep.join(
        ["inst_test", "beat claude", "chat some-model", "●○○○○○○○○○ 12% ↻ 7h20m", "$0.0412"]
    )
    assert second == sep.join(
        ["☸ the wheel turns", "3 await your word", "1 queued", "♥ 6m ago", "all calm"]
    )


def test_spanish_follows_the_owner_language():
    text = status.plain(SNAP, "es")
    assert "latido claude" in text and "3 esperan tu palabra" in text
    assert "la rueda gira" in text and "todo en calma" in text and "♥ hace 6m" in text
    assert set(status.WORDS["en"]) == set(status.WORDS["es"])


def test_roles_are_closed():
    for snap in (SNAP, {**SNAP, "budget": 0, "health_failing": ("disk",), "org_live": False}):
        for lang in ("en", "es"):
            assert {r for r, _ in status.segments(snap, lang) if r} <= status.ROLES


def test_bar_level_follows_the_soft_limit():
    def level(pct):
        segs = status.seat_segments({**SNAP, "pct_used": pct}, "en")
        return {r for r, _ in segs if r.startswith("bar-")}

    assert level(0.1) == {"bar-ok", "bar-empty"}
    assert level(0.85) == {"bar-warn", "bar-empty"}
    assert level(1.5) == {"bar-alert"}


def test_any_spend_shows_one_cell():
    segs = status.seat_segments({**SNAP, "pct_used": 0.001}, "en")
    filled = "".join(t for r, t in segs if r == "bar-ok")
    assert filled == status.BEAD_FULL


def test_unknown_fields_render_as_question_marks():
    snap = {**SNAP, "budget": 0, "approvals": None, "tasks": None, "beat_age": None,
            "health_failing": None, "org_live": False}
    text = status.plain(snap, "en")
    assert "today, no cap" in text and status.BEAD_FULL not in text
    assert "approvals ?" in text and "? queued" in text
    assert "♥ ? ago" in text and "health ?" in text and "the org rests" in text


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
