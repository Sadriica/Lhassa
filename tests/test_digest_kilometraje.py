"""Tests for the digest's "kilometraje" section (lhassa/notify/digest.py).

Fully offline: writes fake Episode rows directly into the tmp-redirected core
DB (via the autouse ``_isolate_brain_db`` conftest fixture) and asserts the
counts/costs the section reports, both in isolation and wired into
``build_digest``.
"""

import asyncio

from lhassa.core import db
from lhassa.core.episode import Episode
from lhassa.notify import digest

INSTANCE = "inst_test"


async def _init():
    await db.init_db()


async def _add_episode(*, decision="", accion="", resultado="", costo_usd=0.0, hours_ago=1.0):
    from datetime import datetime, timedelta, timezone

    ep = Episode.begin("test", INSTANCE)
    ep.creado_en = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    ep = ep.finish(decision=decision, accion=accion, resultado=resultado, costo_usd=costo_usd)
    await db.append_episode(ep)


def test_kilometraje_section_empty_when_no_episodes(tmp_path):
    asyncio.run(_init())
    lines = asyncio.run(digest._kilometraje_section(INSTANCE))
    assert lines
    assert "thinks: 0 today" in lines[1]
    assert "cache hits: 0 today" in lines[2]


def test_kilometraje_counts_thinks_and_cost():
    async def _run():
        await _init()
        await _add_episode(decision="think:full", costo_usd=0.01, hours_ago=1)
        await _add_episode(decision="think:minimal", costo_usd=0.02, hours_ago=2)
        return await digest._kilometraje_section(INSTANCE)

    lines = asyncio.run(_run())
    assert "thinks: 2 today ($0.0300)" in lines[1]


def test_kilometraje_counts_cache_hits_and_hit_rate():
    async def _run():
        await _init()
        await _add_episode(decision="think:full", costo_usd=0.01, hours_ago=1)
        await _add_episode(decision="cache-hit", costo_usd=0.0, hours_ago=1)
        await _add_episode(decision="cache-hit", costo_usd=0.0, hours_ago=1)
        return await digest._kilometraje_section(INSTANCE)

    lines = asyncio.run(_run())
    assert "cache hits: 2 today" in lines[2]
    # 2 hits / (2 hits + 1 think) = 66.7% ~ shown as 67%
    assert "hit-rate 67%" in lines[2]


def test_kilometraje_counts_zero_cost_deterministic_work():
    async def _run():
        await _init()
        await _add_episode(decision="action", accion="sync_repo", costo_usd=0.0, hours_ago=1)
        await _add_episode(decision="skip", costo_usd=0.0, hours_ago=1)
        # A think with $0 cost should NOT be double-counted here.
        await _add_episode(decision="think:full", costo_usd=0.0, hours_ago=1)
        return await digest._kilometraje_section(INSTANCE)

    lines = asyncio.run(_run())
    assert "$0 deterministic work: 2 today" in lines[3]


def test_kilometraje_counts_self_improvement_findings():
    async def _run():
        await _init()
        await _add_episode(
            accion="detect_repetition",
            resultado="found 3 recurring patterns",
            hours_ago=1,
        )
        await _add_episode(
            accion="detect_recurring",
            resultado="no repeated patterns found",
            hours_ago=1,
        )
        return await digest._kilometraje_section(INSTANCE)

    lines = asyncio.run(_run())
    assert "self-improvement findings: 1 today" in lines[4]


def test_kilometraje_respects_the_24h_7d_windows():
    async def _run():
        await _init()
        await _add_episode(decision="think:full", costo_usd=0.05, hours_ago=1)  # today + week
        await _add_episode(decision="think:full", costo_usd=0.07, hours_ago=48)  # week only
        await _add_episode(decision="think:full", costo_usd=0.09, hours_ago=24 * 10)  # neither
        return await digest._kilometraje_section(INSTANCE)

    lines = asyncio.run(_run())
    assert "thinks: 1 today ($0.0500)" in lines[1]
    assert "2 this week ($0.1200)" in lines[1]


def test_kilometraje_section_never_raises_on_broken_db(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(digest, "_kilometraje_window", _boom)
    lines = asyncio.run(digest._kilometraje_section(INSTANCE))
    assert lines == []


def test_kilometraje_wired_into_build_digest(monkeypatch):
    async def _run():
        await _init()
        await _add_episode(decision="think:full", costo_usd=0.01, hours_ago=1)

        import lhassa.org.monitor as monitor_mod

        def fake_gather(config, *, instance_id=None):
            return {"health": {"worst_level": "info", "ok": True, "failing": []}, "failures": [], "flows": []}

        monkeypatch.setattr(monitor_mod, "gather_signals", fake_gather)

        from types import SimpleNamespace

        cfg = SimpleNamespace(instance_id=INSTANCE, notify_channels="log")
        return await digest.build_digest(cfg)

    text = asyncio.run(_run())
    assert "My mileage" in text
    assert "thinks: 1 today" in text
