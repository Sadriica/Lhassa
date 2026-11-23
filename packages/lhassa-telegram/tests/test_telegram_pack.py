"""The telegram pack's registrations, its file capability, and the deprecated
``python -m lhassa.notify.telegram_inbound`` alias. Offline: no Bot API call."""

import asyncio
from types import SimpleNamespace

import lhassa.telegram.pack  # noqa: F401  (idempotent registration)
from lhassa.extensions import PACKS
from lhassa.notify.channels import CHANNELS, document_channel
from lhassa.telegram import api
from lhassa.telegram.channel import TelegramChannel


def test_pack_registers_itself_and_the_telegram_channel():
    assert "telegram" in PACKS
    assert isinstance(CHANNELS.get("telegram"), TelegramChannel)


def test_registration_is_idempotent():
    import importlib

    importlib.reload(lhassa.telegram.pack)
    assert "telegram" in PACKS and "telegram" in CHANNELS


def test_the_telegram_channel_is_the_file_channel():
    cfg = SimpleNamespace(
        notify_channels="telegram", telegram_bot_token="", telegram_chat_id=""
    )
    # Unconfigured, still the one file-capable channel: its own gate declines.
    assert document_channel(cfg) is CHANNELS.get("telegram")


def test_channel_send_document_delegates_to_the_bot_api(monkeypatch, tmp_path):
    calls = []

    async def _fake_send_document(path, *, caption="", config=None):
        calls.append((str(path), caption))
        return True

    monkeypatch.setattr(api, "send_document", _fake_send_document)
    ok = asyncio.run(
        TelegramChannel().send_document(tmp_path / "a.pdf", caption="hi", config=None)
    )
    assert ok is True and calls == [(str(tmp_path / "a.pdf"), "hi")]


def test_channel_send_document_never_raises(monkeypatch, tmp_path):
    async def _boom(path, *, caption="", config=None):
        raise RuntimeError("wire down")

    monkeypatch.setattr(api, "send_document", _boom)
    assert asyncio.run(TelegramChannel().send_document(tmp_path / "a.pdf")) is False


def test_deprecated_alias_hands_the_process_to_the_poller(monkeypatch):
    import runpy

    from lhassa.notify import telegram_inbound

    ran = []
    monkeypatch.setattr(
        runpy, "run_module", lambda name, **kw: ran.append((name, kw.get("run_name")))
    )
    assert telegram_inbound.main() == 0
    assert ran == [("lhassa.telegram.poller", "__main__")]
