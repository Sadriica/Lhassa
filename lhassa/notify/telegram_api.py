# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telegram Bot API file helpers; send a local file, fetch an uploaded one.

The text notifier (:mod:`lhassa.notify.telegram`) only sends ``sendMessage``. The
Telegram → CV work-flow bridge additionally needs to (a) DOWNLOAD an uploaded CV
document and (b) SEND BACK a tailored ``.tex`` file. Those two file operations live
here so the text path stays untouched.

Same discipline as the rest of :mod:`lhassa.notify`: **credential-gated** (no token
or chat id → no network, returns falsy) and **fully defensive** (any HTTP/transport
error is logged and yields a falsy result; these helpers never raise into the
poller).

  * :func:`get_file`      : ``getFile`` → ``file_path`` → GET the CDN URL → ``bytes``.
  * :func:`send_document` : multipart ``sendDocument`` of a local path to the owner chat.
  * :func:`set_my_commands`: ``setMyCommands`` (register the bot's command menu).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from lhassa.notify.telegram import _credentials

# Bot API endpoints. ``<token>`` is interpolated per call; the file CDN path is a
# separate host shape (``/file/bot<token>/<file_path>``).
_GET_FILE_URL = "https://api.telegram.org/bot{token}/getFile"
_FILE_URL = "https://api.telegram.org/file/bot{token}/{file_path}"
_SEND_DOCUMENT_URL = "https://api.telegram.org/bot{token}/sendDocument"
_SET_COMMANDS_URL = "https://api.telegram.org/bot{token}/setMyCommands"
_ANSWER_CALLBACK_URL = "https://api.telegram.org/bot{token}/answerCallbackQuery"

# A file transfer can take longer than a text message.
_TIMEOUT = 60.0


def _response_ok(response, label: str) -> bool:
    """True iff ``response`` is HTTP 200 with an ``ok: true`` Bot API body.

    The shared "was this Bot API call accepted?" check, logging (never raising) on a
    bad status, undecodable JSON, or an ``ok: false`` payload.
    """
    if response.status_code != 200:
        logger.warning(
            f"Telegram {label} failed: HTTP {response.status_code} {response.text[:200]}"
        )
        return False
    try:
        data = response.json()
    except Exception:
        logger.warning(f"Telegram {label}: could not decode response JSON")
        return False
    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(f"Telegram {label} rejected: {str(data)[:200]}")
        return False
    return True


async def get_file(file_id: str, *, config=None) -> Optional[bytes]:
    """Download the Telegram file with ``file_id`` and return its bytes.

    Two hops per the Bot API: ``getFile`` resolves a temporary ``file_path``, then a
    GET on the file CDN (``/file/bot<token>/<file_path>``) returns the content.
    Credential-gated and fully defensive: no token/chat id → ``None`` with no network;
    any error is logged and yields ``None``. Never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    token, chat_id = _credentials(config)
    if not token or not chat_id:
        return None
    if not file_id:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            meta = await client.get(
                _GET_FILE_URL.format(token=token), params={"file_id": file_id}
            )
            if meta.status_code != 200:
                logger.warning(
                    f"Telegram getFile failed: HTTP {meta.status_code} {meta.text[:200]}"
                )
                return None
            data = meta.json()
            if not isinstance(data, dict) or not data.get("ok"):
                logger.warning(f"Telegram getFile rejected: {str(data)[:200]}")
                return None
            file_path = (data.get("result") or {}).get("file_path")
            if not file_path:
                logger.warning("Telegram getFile: no file_path in response")
                return None
            dl = await client.get(_FILE_URL.format(token=token, file_path=file_path))
            if dl.status_code != 200:
                logger.warning(f"Telegram file download failed: HTTP {dl.status_code}")
                return None
            return dl.content
    except Exception as exc:  # transport/JSON error; never propagate
        logger.warning(f"Telegram getFile failed (transport): {exc}")
        return None


async def send_document(path, *, caption: str = "", config=None) -> bool:
    """Send the local file at ``path`` to the owner chat via ``sendDocument``.

    Multipart/form-data upload over httpx. Credential-gated (no token/chat id → no
    network, returns ``False``) and defensive: a missing file or any HTTP error is
    logged and yields ``False``. Never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    token, chat_id = _credentials(config)
    if not token or not chat_id:
        return False

    file_path = Path(path)
    try:
        if not file_path.is_file():
            logger.warning(f"Telegram sendDocument: no such file {file_path}")
            return False
        content = file_path.read_bytes()
    except OSError as exc:
        logger.warning(f"Telegram sendDocument: could not read {file_path}: {exc}")
        return False

    url = _SEND_DOCUMENT_URL.format(token=token)
    files = {"document": (file_path.name, content, "application/octet-stream")}
    payload: dict = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, data=payload, files=files)
    except Exception as exc:  # transport error; never propagate
        logger.warning(f"Telegram sendDocument failed (transport): {exc}")
        return False

    if response.status_code != 200:
        logger.warning(
            f"Telegram sendDocument failed: HTTP {response.status_code} {response.text[:200]}"
        )
        return False

    try:
        data = response.json()
    except Exception:
        logger.warning("Telegram sendDocument: could not decode response JSON")
        return False

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(f"Telegram sendDocument rejected: {str(data)[:200]}")
        return False

    return True


def _owner_scope(chat_id: str):
    """A ``BotCommandScopeChat`` for the owner's private chat, or ``None``.

    ``setMyCommands`` can be scoped to a specific chat; a numeric chat id yields a
    ``{"type": "chat", "chat_id": …}`` scope, else we fall back to the default scope
    (``None`` → the caller omits ``scope``).
    """
    try:
        return {"type": "chat", "chat_id": int(chat_id)}
    except (TypeError, ValueError):
        return None


async def set_my_commands(commands, *, config=None) -> bool:
    """Register the bot's command menu via ``setMyCommands``.

    ``commands`` is a list of ``(command, description)`` pairs (a leading ``/`` on the
    command is stripped, the name lower-cased). Scoped to the owner's private chat when
    the chat id is numeric, else the default scope. Credential-gated (no token/chat id →
    no network, ``False``) and defensive; it never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    token, chat_id = _credentials(config)
    if not token or not chat_id:
        return False

    cmds = [
        {"command": str(command).lstrip("/").lower(), "description": str(description)}
        for command, description in (commands or [])
    ]
    if not cmds:
        return False

    payload: dict = {"commands": cmds}
    scope = _owner_scope(chat_id)
    if scope is not None:
        payload["scope"] = scope

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                _SET_COMMANDS_URL.format(token=token), json=payload
            )
    except Exception as exc:  # transport error; never propagate
        logger.warning(f"Telegram setMyCommands failed (transport): {exc}")
        return False

    return _response_ok(response, "setMyCommands")


async def answer_callback_query(
    callback_query_id: str, *, text: str = "", config=None
) -> bool:
    """Acknowledge an inline-keyboard tap via ``answerCallbackQuery``.

    Clears the button's client-side loading spinner. ``text`` (optional) is shown as
    a brief toast on the tapping client. Credential-gated (no token/chat id → no
    network, ``False``) and defensive; it never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    token, chat_id = _credentials(config)
    if not token or not chat_id:
        return False
    if not callback_query_id:
        return False

    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                _ANSWER_CALLBACK_URL.format(token=token), json=payload
            )
    except Exception as exc:  # transport error; never propagate
        logger.warning(f"Telegram answerCallbackQuery failed (transport): {exc}")
        return False

    return _response_ok(response, "answerCallbackQuery")
