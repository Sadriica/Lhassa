"""Tests for the credential-gated Telegram notifier; httpx fully mocked."""

import asyncio
from types import SimpleNamespace

import httpx

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

    monkeypatch.setattr(httpx, "AsyncClient", _boom)

    cfg = SimpleNamespace(telegram_bot_token="", telegram_chat_id="")
    assert asyncio.run(telegram.send("hi", config=cfg)) is False

    # Only one credential present is also unconfigured → still no network.
    cfg2 = SimpleNamespace(telegram_bot_token="T", telegram_chat_id="")
    assert asyncio.run(telegram.send("hi", config=cfg2)) is False


def test_configured_posts(monkeypatch):
    _FakeClient.last = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    ok = asyncio.run(telegram.send("hello", config=cfg))
    assert ok is True
    assert _FakeClient.last["json"] == {"chat_id": "123", "text": "hello"}
    assert "botTOKEN" in _FakeClient.last["url"]


def test_http_error_returns_false(monkeypatch):
    class _ErrClient(_FakeClient):
        async def post(self, url, json=None):
            return _Resp(status=400, payload={"ok": False}, text="bad request")

    monkeypatch.setattr(httpx, "AsyncClient", _ErrClient)
    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    assert asyncio.run(telegram.send("hi", config=cfg)) is False


def test_send_omits_parse_mode_and_reply_markup_when_absent(monkeypatch):
    _FakeClient.last = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    assert asyncio.run(telegram.send("hello", config=cfg)) is True
    assert _FakeClient.last["json"] == {"chat_id": "123", "text": "hello"}
    assert "parse_mode" not in _FakeClient.last["json"]
    assert "reply_markup" not in _FakeClient.last["json"]


def test_send_includes_parse_mode_and_reply_markup_when_present(monkeypatch):
    _FakeClient.last = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    keyboard = {"inline_keyboard": [[{"text": "Approve", "callback_data": "approve:ap_1"}]]}
    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    ok = asyncio.run(
        telegram.send("hi <b>bold</b>", config=cfg, parse_mode="HTML", reply_markup=keyboard)
    )
    assert ok is True
    payload = _FakeClient.last["json"]
    assert payload["parse_mode"] == "HTML"
    assert payload["reply_markup"] == keyboard


def test_transport_error_returns_false(monkeypatch):
    class _RaisingClient(_FakeClient):
        async def post(self, url, json=None):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _RaisingClient)
    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    assert asyncio.run(telegram.send("hi", config=cfg)) is False


# ── Dispatcher (notify) ────────────────────────────────────────────────────────

from lhassa.notify import dispatch  # noqa: E402


def test_dispatch_unconfigured_is_noop_no_network(monkeypatch):
    # An unconfigured box must not even build a network call.
    def _boom(*a, **k):
        raise AssertionError("no network when unconfigured")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)

    cfg = SimpleNamespace(telegram_bot_token="", telegram_chat_id="")
    assert asyncio.run(dispatch.notify("hi", config=cfg)) is False


def test_dispatch_configured_calls_send(monkeypatch):
    # No notify_channels attribute → the dispatcher's "telegram" default, so only
    # the Telegram channel fires (the historical single-channel behaviour).
    sent = {}

    async def _fake_send(text, *, config=None):
        sent["text"] = text
        return True

    # Patch the telegram module's send; the registered TelegramChannel calls it.
    monkeypatch.setattr(telegram, "send", _fake_send)

    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    assert asyncio.run(dispatch.notify("alert!", level="warning", config=cfg)) is True
    assert sent["text"] == "alert!"


def test_dispatch_swallows_channel_error(monkeypatch):
    async def _raising_send(text, *, config=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(telegram, "send", _raising_send)

    cfg = SimpleNamespace(telegram_bot_token="TOKEN", telegram_chat_id="123")
    # Channel raised → treated as non-delivery, never propagates.
    assert asyncio.run(dispatch.notify("x", config=cfg)) is False
