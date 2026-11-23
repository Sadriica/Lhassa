# SPDX-License-Identifier: AGPL-3.0-or-later
"""A channel name whose pack is absent is noted once, quietly; a typo still warns."""

from __future__ import annotations

from types import SimpleNamespace

from loguru import logger

from lhassa.notify import channels


def _capture():
    seen: list[tuple[str, str]] = []
    sink = logger.add(lambda m: seen.append((m.record["level"].name, m.record["message"])), level="DEBUG")
    return seen, sink


def test_absent_pack_channel_is_debug_once_and_typo_warns(monkeypatch):
    monkeypatch.setattr(channels, "_NOTED_MISSING", set())
    monkeypatch.setitem(channels._PACK_CHANNELS, "test-pack-chan", "lhassa-test")
    seen, sink = _capture()
    try:
        cfg = SimpleNamespace(notify_channels="test-pack-chan,test-typo-chan")
        channels.active_channels(cfg)
        channels.active_channels(cfg)
    finally:
        logger.remove(sink)
    pack_lines = [lvl for lvl, msg in seen if "test-pack-chan" in msg]
    typo_lines = [lvl for lvl, msg in seen if "test-typo-chan" in msg]
    assert pack_lines == ["DEBUG"]
    assert typo_lines == ["WARNING"]
