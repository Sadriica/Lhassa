"""The inbound poller must BACK OFF when the network is down.

Regression cover: ``poll_once`` swallows transport errors and returns the offset
unchanged — indistinguishable from a clean idle long-poll — so ``run_forever``'s
exponential backoff could never fire and the loop spun at the speed of the
failures (measured ~45 getUpdates attempts per second with DNS down). Offline:
httpx is monkeypatched, asyncio.sleep is recorded, no network.
"""

import asyncio

import pytest

from lhassa.notify import telegram_inbound


def _cfg():
    class _C:
        telegram_poll_enabled = True
        telegram_bot_token = "token"
        telegram_chat_id = "42"

    return _C()


class _BoomClient:
    """An AsyncClient whose GET always fails the way a DNS outage does."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        raise OSError("[Errno -3] Temporary failure in name resolution")


def test_poll_cycle_reports_transport_failure(monkeypatch):
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _BoomClient)
    offset, ok = asyncio.run(telegram_inbound.poll_cycle(_cfg(), offset=7))
    assert offset == 7  # cursor preserved — updates are not skipped
    assert ok is False


def test_poll_cycle_reports_http_error(monkeypatch):
    class _Resp:
        status_code = 502
        text = "bad gateway"

    class _Client(_BoomClient):
        async def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _Client)
    offset, ok = asyncio.run(telegram_inbound.poll_cycle(_cfg(), offset=3))
    assert offset == 3
    assert ok is False


def test_poll_cycle_reports_success_on_an_idle_long_poll(monkeypatch):
    """No updates is NOT a failure — it must not trigger a backoff."""

    class _Resp:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True, "result": []}

    class _Client(_BoomClient):
        async def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _Client)
    offset, ok = asyncio.run(telegram_inbound.poll_cycle(_cfg(), offset=9))
    assert offset == 9
    assert ok is True


def test_poll_once_keeps_its_offset_only_contract(monkeypatch):
    """The public wrapper still returns a bare offset for existing callers."""
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _BoomClient)
    assert asyncio.run(telegram_inbound.poll_once(_cfg(), offset=7)) == 7


def test_run_forever_backs_off_instead_of_hot_looping(monkeypatch):
    """A failing cycle must sleep, and the delay must grow."""
    slept: list = []
    cycles = {"n": 0}

    async def _fake_poll_cycle(config=None, *, offset=None):
        cycles["n"] += 1
        if cycles["n"] > 5:
            raise KeyboardInterrupt  # break out of the infinite loop
        return offset, False

    async def _fake_sleep(secs):
        slept.append(secs)

    monkeypatch.setattr(telegram_inbound, "poll_cycle", _fake_poll_cycle)
    monkeypatch.setattr(telegram_inbound.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(telegram_inbound, "_enabled", lambda cfg: True)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(telegram_inbound.run_forever(_cfg()))

    assert len(slept) == 5, "every failed cycle must sleep"
    assert slept == sorted(slept), "the delay must be non-decreasing"
    assert slept[-1] > slept[0], "the backoff must actually grow"
    assert max(slept) <= telegram_inbound._BACKOFF_MAX


def test_run_forever_resets_backoff_after_recovery(monkeypatch):
    """Once the network returns, the next outage starts from the short delay."""
    slept: list = []
    states = [False, False, False, True, False]
    idx = {"i": 0}

    async def _fake_poll_cycle(config=None, *, offset=None):
        i = idx["i"]
        idx["i"] += 1
        if i >= len(states):
            raise KeyboardInterrupt
        return offset, states[i]

    async def _fake_sleep(secs):
        slept.append(secs)

    monkeypatch.setattr(telegram_inbound, "poll_cycle", _fake_poll_cycle)
    monkeypatch.setattr(telegram_inbound.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(telegram_inbound, "_enabled", lambda cfg: True)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(telegram_inbound.run_forever(_cfg()))

    # Three failures grow the delay; the success resets it, so the fourth
    # failure sleeps the starting amount again.
    assert slept[0] == telegram_inbound._BACKOFF_START
    assert slept[2] > slept[0]
    assert slept[3] == telegram_inbound._BACKOFF_START
