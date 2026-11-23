# SPDX-License-Identifier: AGPL-3.0-or-later
"""The TUI status bar: pure rendering, duration format, never-raising gather."""

from __future__ import annotations

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


def _style_of(text, needle):
    for span in text.spans:
        if text.plain[span.start : span.end].startswith(needle):
            return str(span.style)
    return ""


def test_render_is_the_core_read_model():
    from lhassa.views import status

    for lang in ("en", "es"):
        assert statusbar.render(BASE, DARK, lang).plain == status.plain(BASE, lang)


def test_beads_take_the_palette_color_of_their_level():
    full = "●"
    assert DARK["lh-green"] in _style_of(statusbar.render(BASE, DARK, "en"), full)
    assert DARK["lh-gold"] in _style_of(statusbar.render({**BASE, "pct_used": 0.85}, DARK, "en"), full)
    assert DARK["lh-red"] in _style_of(statusbar.render({**BASE, "pct_used": 1.2}, DARK, "en"), full)
    assert DARK["lh-faint"] in _style_of(statusbar.render(BASE, DARK, "en"), "○")


def test_every_role_has_a_style():
    from lhassa.views import status

    assert set(statusbar._styles(DARK)) == set(status.ROLES)


def test_failing_health_is_red():
    text = statusbar.render({**BASE, "health_failing": ("disk",)}, DARK, "en")
    assert DARK["lh-red"] in _style_of(text, "heads up: disk")


def test_format_duration():
    assert statusbar.format_duration(None) == "?"
    assert statusbar.format_duration(40) == "40s"
    assert statusbar.format_duration(720) == "12m"
    assert statusbar.format_duration(26400) == "7h20m"
    assert statusbar.format_duration(90000) == "1d1h"


async def test_gather_never_raises_on_an_empty_db():
    snap = await statusbar.gather("inst_test")
    assert snap["instance_id"] == "inst_test"
    assert snap["approvals"] == 0 and snap["tasks"] == 0
    assert set(BASE) <= set(snap)
