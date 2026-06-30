"""Tests for the credential-gated Telegram notifier — httpx fully mocked."""

import asyncio
from types import SimpleNamespace

from lhassa.notify import telegram


class _Resp:
    def __init__(self, status=200, payload=None, text="ok"):
        self.status_code = status
        self._payload = payload if payload is not None else {"ok": True}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    last = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        _FakeClient.last = {"url": url, "json": json}
        return _Resp()


def test_is_configured():
    assert telegram.is_configured(SimpleNamespace(telegram_bot_token="T", telegram_chat_id="1")) is True
    assert telegram.is_configured(SimpleNamespace(telegram_bot_token="", telegram_chat_id="1")) is False
    assert telegram.is_configured(SimpleNamespace(telegram_bot_token="T", telegram_chat_id="")) is False


def test_unconfigured_returns_false_no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no network call when unconfigured")

    monkeypatch.setattr(telegram.httpx, "AsyncClient", _boom)

    cfg = SimpleNamespace(telegram_bot_token="", telegram_chat_id="")
    assert asyncio.run(telegram.send("hi", config=cfg)) is False

    # Only one credential present is also unconfigured → still no network.
    cfg2 = SimpleNamespace(telegram_bot_token="T", telegram_chat_id="")
    assert asyncio.run(telegram.send("hi", config=cfg2)) is False


def test_configured_posts(monkeypatch):
    _FakeClient.last = None
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _FakeClient)

    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    ok = asyncio.run(telegram.send("hello", config=cfg))
    assert ok is True
    assert _FakeClient.last["json"] == {"chat_id": "123", "text": "hello"}
    assert "botTOKEN" in _FakeClient.last["url"]


def test_http_error_returns_false(monkeypatch):
    class _ErrClient(_FakeClient):
        async def post(self, url, json=None):
            return _Resp(status=400, payload={"ok": False}, text="bad request")

    monkeypatch.setattr(telegram.httpx, "AsyncClient", _ErrClient)
    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    assert asyncio.run(telegram.send("hi", config=cfg)) is False


def test_transport_error_returns_false(monkeypatch):
    class _RaisingClient(_FakeClient):
        async def post(self, url, json=None):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(telegram.httpx, "AsyncClient", _RaisingClient)
    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    assert asyncio.run(telegram.send("hi", config=cfg)) is False
