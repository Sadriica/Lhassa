"""Tests for the inbound Telegram poller — httpx and run_once fully mocked.

Never hits the real Telegram API and never calls a real LLM: ``getUpdates`` goes
through a fake httpx client and ``lhassa.cli.agent.run_once`` is monkeypatched.
"""

import asyncio
from types import SimpleNamespace

from lhassa.notify import telegram, telegram_inbound


def _cfg(**over):
    base = dict(
        telegram_bot_token="TOKEN",
        telegram_chat_id="123",
        telegram_poll_enabled=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Resp:
    def __init__(self, status=200, payload=None, text="ok"):
        self.status_code = status
        self._payload = payload if payload is not None else {"ok": True, "result": []}
        self.text = text

    def json(self):
        return self._payload


def _client_returning(payload):
    """An httpx.AsyncClient stand-in whose GET returns ``payload``."""

    class _Client:
        last = None

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            _Client.last = {"url": url, "params": params}
            return _Resp(payload=payload)

    return _Client


def _patch_run_once(monkeypatch, response="the answer"):
    calls = []

    async def _fake_run_once(prompt, *, config=None):
        calls.append(prompt)
        return SimpleNamespace(response=response)

    import lhassa.cli.agent as agent_mod

    monkeypatch.setattr(agent_mod, "run_once", _fake_run_once)
    return calls


def _patch_send(monkeypatch):
    sent = []

    async def _fake_send(text, *, config=None):
        sent.append(text)
        return True

    monkeypatch.setattr(telegram, "send", _fake_send)
    return sent


def test_poll_once_disabled_no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no network when poll disabled")

    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _boom)

    cfg = _cfg(telegram_poll_enabled=False)
    assert asyncio.run(telegram_inbound.poll_once(cfg, offset=5)) == 5


def test_poll_once_unconfigured_no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no network when unconfigured")

    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _boom)

    cfg = _cfg(telegram_bot_token="")  # enabled but no token
    assert asyncio.run(telegram_inbound.poll_once(cfg, offset=None)) is None


def test_poll_once_answers_allowed_chat_and_advances_offset(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 100,
                "message": {"chat": {"id": 123}, "text": "what time is it"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    calls = _patch_run_once(monkeypatch, response="It is noon.")
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert calls == ["what time is it"]
    assert sent == ["It is noon."]
    assert new_offset == 101  # update_id + 1


def test_poll_once_passes_offset_in_params(monkeypatch):
    client = _client_returning({"ok": True, "result": []})
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", client)

    out = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=42))
    assert out == 42  # nothing new
    assert client.last["params"]["offset"] == 42


def test_poll_once_ignores_other_chat_ids(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 7,
                "message": {"chat": {"id": 999}, "text": "let me in"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    calls = _patch_run_once(monkeypatch)
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    # Foreign chat → never answered, but the offset still advances past it.
    assert calls == []
    assert sent == []
    assert new_offset == 8


def test_poll_once_malformed_update_does_not_raise(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {"update_id": 1},  # no message at all
            {"update_id": 2, "message": {"text": "no chat key"}},
            {"update_id": 3, "message": {"chat": {"id": 123}}},  # no text
            {"update_id": 4, "message": {"chat": {"id": 123}, "text": "ok now"}},
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    calls = _patch_run_once(monkeypatch, response="done")
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    # Only the one well-formed, owner message is answered; offset clears the batch.
    assert calls == ["ok now"]
    assert sent == ["done"]
    assert new_offset == 5


def test_poll_once_http_error_keeps_offset(monkeypatch):
    class _ErrClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _Resp(status=500, payload={"ok": False}, text="server error")

    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _ErrClient)
    assert asyncio.run(telegram_inbound.poll_once(_cfg(), offset=9)) == 9


def test_poll_once_transport_error_keeps_offset(monkeypatch):
    class _RaisingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _RaisingClient)
    assert asyncio.run(telegram_inbound.poll_once(_cfg(), offset=3)) == 3


def test_run_forever_returns_immediately_when_disabled(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no network when disabled")

    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _boom)

    cfg = _cfg(telegram_poll_enabled=False)
    # Must return (not loop) — a plain await that completes proves it.
    asyncio.run(telegram_inbound.run_forever(cfg))


def test_run_once_failure_is_caught(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 50,
                "message": {"chat": {"id": 123}, "text": "boom please"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    import lhassa.cli.agent as agent_mod

    async def _raising_run_once(prompt, *, config=None):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr(agent_mod, "run_once", _raising_run_once)
    sent = _patch_send(monkeypatch)

    # No raise; offset still advances; a best-effort apology is sent.
    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert new_offset == 51
    assert len(sent) == 1  # the apology
