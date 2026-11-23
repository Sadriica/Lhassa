"""In-memory fakes for the chat engine and the notify channels (no wire, no network).

``FakeTransport`` plays the part a chat transport (the telegram pack's poller)
plays for :mod:`lhassa.chat`: it builds :class:`~lhassa.chat.types.InboundMessage`
values and records every reply, acknowledgment and file fetch. ``RecordingChannel``
/ ``FileChannel`` stand in for a notify channel (``FileChannel`` also carries the
optional ``send_document`` capability :func:`lhassa.org.deliver.deliver` sends
files through); :func:`install_channel` puts one in the registry for one test.
Pack tests import this module the same way they import ``tests.conftest``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from lhassa import chat
from lhassa.chat.types import InboundDocument, InboundMessage


class FakeTransport:
    """A recording chat transport for engine tests."""

    def __init__(self, chat_id: str = "123"):
        self.chat_id = chat_id
        self.sent: list[str] = []
        self.replies: list = []
        self.acks: list[str] = []
        self.fetched: list[str] = []

    async def send(self, reply) -> bool:
        self.replies.append(reply)
        self.sent.append(reply.text)
        return True

    async def acknowledge(self, text: str) -> bool:
        self.acks.append(text)
        return True

    def message(
        self,
        text: str = "",
        *,
        chat_id: Optional[str] = None,
        document: Optional[InboundDocument] = None,
        callback: Optional[str] = None,
    ) -> InboundMessage:
        return InboundMessage(
            chat_id=self.chat_id if chat_id is None else chat_id,
            text=text,
            document=document,
            callback=callback,
            send=self.send,
            acknowledge=self.acknowledge,
        )

    def document(
        self,
        *,
        content: Optional[bytes] = b"",
        file_id: str = "FID",
        file_name: str = "cv.tex",
        caption: Optional[str] = None,
    ) -> InboundDocument:
        async def _fetch():
            self.fetched.append(file_id)
            return content

        return InboundDocument(
            file_id=file_id,
            file_name=file_name,
            caption=caption,
            fetch=_fetch,
            raw={"file_id": file_id, "file_name": file_name},
        )

    async def say(self, text: str, *, config, chat_id: Optional[str] = None):
        """One text turn, played the way a transport plays it."""
        return await chat.respond(self.message(text, chat_id=chat_id), config=config)

    async def tap(self, data: str, *, config):
        """One button tap carrying ``data``."""
        return await chat.respond(self.message(callback=data), config=config)

    async def upload(self, *, config, chat_id: Optional[str] = None, **document):
        """One uploaded document (see :meth:`document` for the fields)."""
        return await chat.respond(
            self.message(chat_id=chat_id, document=self.document(**document)), config=config
        )


async def drain() -> None:
    """Await every task the turn scheduled (follow-ups, run-now tasks)."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)


def run_and_drain(coro):
    """``asyncio.run`` a turn and the fire-and-forget work it started."""

    async def _go():
        result = await coro
        await drain()
        return result

    return asyncio.run(_go())


class RecordingChannel:
    """A notify channel that records what it was asked to send."""

    def __init__(self, name: str = "telegram", *, configured: bool = True, delivered: bool = True):
        self.name = name
        self._configured = configured
        self._delivered = delivered
        self.sent: list[str] = []
        self.kwargs: list[dict] = []

    def is_configured(self, config) -> bool:
        return self._configured

    async def send(self, text, *, level="info", config=None, **kwargs) -> bool:
        self.sent.append(text)
        self.kwargs.append(kwargs)
        return self._delivered


class FileChannel(RecordingChannel):
    """A :class:`RecordingChannel` that can also deliver files."""

    def __init__(
        self, name: str = "telegram", *, documents_ok: bool = True, on_document=None, **kw
    ):
        super().__init__(name, **kw)
        self._documents_ok = documents_ok
        self._on_document = on_document
        self.documents: list[dict] = []

    async def send_document(self, path, *, caption="", config=None) -> bool:
        self.documents.append({"path": str(path), "caption": caption})
        if self._on_document is not None:
            return await self._on_document(path, caption=caption, config=config)
        return self._documents_ok


def install_channel(monkeypatch, channel):
    """Register ``channel`` under its name for this test only (restores after)."""
    from lhassa.notify.channels import CHANNELS

    monkeypatch.setitem(CHANNELS._items, channel.name, channel)
    return channel
