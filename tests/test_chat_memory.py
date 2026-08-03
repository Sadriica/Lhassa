"""Telegram chat memory — rolling history, the remember nudge, instant tasks.

All offline: providers, facts and the task runner are monkeypatched fakes.
"""

import asyncio
import time
from types import SimpleNamespace

from lhassa.notify import telegram_inbound


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _fresh_chat_state():
    """Module-level chat history/turn counters never leak between tests."""
    from lhassa.notify import telegram_inbound as _ti

    _ti._CHAT_HISTORY.clear()
    _ti._CHAT_TURNS.clear()
    yield
    _ti._CHAT_HISTORY.clear()
    _ti._CHAT_TURNS.clear()



def _cfg(**over):
    base = dict(
        instance_id="inst_personal",
        telegram_chat_id="hist-test",
        telegram_history_turns=3,
        telegram_history_ttl=7200.0,
        chat_memory_nudge_turns=2,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _clear(cfg):
    telegram_inbound._CHAT_HISTORY.pop(telegram_inbound._history_key(cfg), None)
    telegram_inbound._CHAT_TURNS.pop(telegram_inbound._history_key(cfg), None)


# ── the rolling history store ──────────────────────────────────────────────────


def test_history_records_and_returns_in_order():
    cfg = _cfg(telegram_chat_id="h1")
    _clear(cfg)
    telegram_inbound._history_record(cfg, "user", "hola")
    telegram_inbound._history_record(cfg, "assistant", "hola, ¿qué necesitas?")
    msgs = telegram_inbound._history_messages(cfg)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hola"


def test_history_caps_at_configured_pairs():
    cfg = _cfg(telegram_chat_id="h2", telegram_history_turns=2)
    _clear(cfg)
    for i in range(10):
        telegram_inbound._history_record(cfg, "user", f"u{i}")
        telegram_inbound._history_record(cfg, "assistant", f"a{i}")
    msgs = telegram_inbound._history_messages(cfg)
    assert len(msgs) == 4  # 2 pairs
    assert msgs[-1]["content"] == "a9"


def test_history_ttl_drops_old_turns():
    cfg = _cfg(telegram_chat_id="h3", telegram_history_ttl=100.0)
    _clear(cfg)
    telegram_inbound._history_record(cfg, "user", "viejo")
    key = telegram_inbound._history_key(cfg)
    telegram_inbound._CHAT_HISTORY[key][0]["ts"] = time.time() - 500
    telegram_inbound._history_record(cfg, "user", "nuevo")
    msgs = telegram_inbound._history_messages(cfg)
    assert [m["content"] for m in msgs] == ["nuevo"]


def test_history_truncates_long_messages():
    cfg = _cfg(telegram_chat_id="h4")
    _clear(cfg)
    telegram_inbound._history_record(cfg, "user", "x" * 9000)
    msgs = telegram_inbound._history_messages(cfg)
    assert len(msgs[0]["content"]) == telegram_inbound._HISTORY_MSG_CAP


def test_isolated_chats_do_not_share_history():
    a, b = _cfg(telegram_chat_id="ha"), _cfg(telegram_chat_id="hb")
    _clear(a), _clear(b)
    telegram_inbound._history_record(a, "user", "solo-en-a")
    assert telegram_inbound._history_messages(b) == []


# ── the remember marker ────────────────────────────────────────────────────────


class _FakeFact:
    def __init__(self, contenido):
        self.contenido = contenido


def _patch_facts(monkeypatch, existing=()):
    written = []

    async def fake_load(instance_id, **kw):
        return [_FakeFact(c) for c in existing]

    async def fake_add(instance_id, tipo, contenido, **kw):
        written.append({"tipo": tipo, "contenido": contenido, **kw})
        return "fact_1"

    import lhassa.memory.facts as facts_mod

    monkeypatch.setattr(facts_mod, "load_facts", fake_load)
    monkeypatch.setattr(facts_mod, "add_fact", fake_add)
    return written


def test_remember_marker_persists_and_strips(monkeypatch):
    written = _patch_facts(monkeypatch)
    reply = asyncio.run(
        telegram_inbound._apply_remember_marker(
            "Anotado.\n[[remember: the owner prefers concise replies]]",
            instance_id="inst_personal",
        )
    )
    assert reply == "Anotado."
    assert len(written) == 1
    assert written[0]["contenido"] == "the owner prefers concise replies"
    assert written[0]["procedencia"] == "telegram-chat"


def test_remember_marker_dedupes_against_existing(monkeypatch):
    written = _patch_facts(
        monkeypatch, existing=["The owner prefers concise replies always."]
    )
    reply = asyncio.run(
        telegram_inbound._apply_remember_marker(
            "Claro.\n[[remember: the owner prefers concise replies]]",
            instance_id="inst_personal",
        )
    )
    assert reply == "Claro."
    assert written == []  # substring overlap → skipped


def test_remember_failure_never_breaks_the_reply(monkeypatch):
    import lhassa.memory.facts as facts_mod

    async def boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(facts_mod, "load_facts", boom)
    reply = asyncio.run(
        telegram_inbound._apply_remember_marker(
            "Ok. [[remember: algo]]", instance_id="inst_personal"
        )
    )
    assert "[[" not in reply and reply.startswith("Ok.")


# ── instant execution of the research task ─────────────────────────────────────


def test_research_marker_schedules_immediate_run(monkeypatch):
    import lhassa.core.dispatch as dispatch_mod

    ran = []

    async def fake_mint(instance_id, accion, objetivo="", params=None, *, origin, **kw):
        return ("t_9", "pendiente")

    async def fake_run_now(instance_id, task_id, accion, params=None, **kw):
        ran.append((task_id, accion))
        return True

    monkeypatch.setattr(dispatch_mod, "mint_task", fake_mint)
    monkeypatch.setattr(dispatch_mod, "run_task_now", fake_run_now)

    async def main():
        reply = await telegram_inbound._apply_research_marker(
            "[[research: latest python]]",
            instance_id="inst_personal",
            config=_cfg(),
        )
        await asyncio.sleep(0)  # let the fire-and-forget task run
        return reply

    reply = asyncio.run(main())
    assert telegram_inbound._RESEARCH_ACK in reply
    assert ran == [("t_9", "research_reply")]


def test_approval_parked_research_is_not_run_now(monkeypatch):
    import lhassa.core.dispatch as dispatch_mod

    ran = []

    async def fake_mint(instance_id, accion, objetivo="", params=None, *, origin, **kw):
        return ("t_10", "esperando_aprobacion")

    async def fake_run_now(*a, **k):
        ran.append(a)
        return True

    monkeypatch.setattr(dispatch_mod, "mint_task", fake_mint)
    monkeypatch.setattr(dispatch_mod, "run_task_now", fake_run_now)

    async def main():
        reply = await telegram_inbound._apply_research_marker(
            "[[research: x]]", instance_id="inst_personal", config=_cfg()
        )
        await asyncio.sleep(0)
        return reply

    asyncio.run(main())
    assert ran == []  # a parked task waits for its human
