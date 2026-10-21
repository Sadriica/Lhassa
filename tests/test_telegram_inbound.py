"""Tests for the Telegram transport (the getUpdates poller); httpx fully mocked.

Never hits the real Telegram API and never calls a real LLM. ``getUpdates``
goes through a fake httpx client and every Bot API call (sendMessage,
answerCallbackQuery, getFile) is a monkeypatched fake. What is answered is the
chat engine's business (``tests/test_chat_engine.py``); these pin the wire:
the gate, the offset, the owner-only door, malformed updates, taps and their
acknowledgments, inline keyboards, and document hand-off.
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


class _FakeProvider:
    """Records the tool-less completion calls the brain think makes."""

    def __init__(self, reply="brain reply", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    async def complete(self, messages, *, model=None, max_tokens=None, system=None):
        self.calls.append({"messages": messages, "model": model, "system": system})
        if self.raise_exc is not None:
            raise self.raise_exc
        return SimpleNamespace(text=self.reply, cost_usd=0.0)


def _patch_brain(monkeypatch, *, reply="brain reply", raise_exc=None):
    """Mock the tool-less brain think and forbid the 26-tool agent.

    Returns the fake provider so a test can assert it was (or wasn't) called.
    """
    import lhassa.cli.agent as agent_mod
    import lhassa.core.db as db_mod
    import lhassa.core.episode as episode_mod
    import lhassa.llm.router as router_mod
    import lhassa.memory.load as load_mod

    provider = _FakeProvider(reply=reply, raise_exc=raise_exc)

    async def _noop_init():
        return None

    async def _fake_load(instance_id=None, **kwargs):
        return SimpleNamespace(to_prompt=lambda: "")

    def _fake_decide(trigger, ctx=None, *, config, **kwargs):
        return SimpleNamespace(provider=provider, model="cheap", reason="routine:telegram")

    async def _noop_append(self):
        return "ep"

    async def _boom_agent(*a, **k):
        raise AssertionError("inbound must not call the 26-tool agent (run_once)")

    monkeypatch.setattr(db_mod, "init_db", _noop_init)
    monkeypatch.setattr(load_mod, "load", _fake_load)
    monkeypatch.setattr(router_mod, "decide", _fake_decide)
    monkeypatch.setattr(episode_mod.Episode, "append", _noop_append)
    monkeypatch.setattr(agent_mod, "run_once", _boom_agent)
    return provider


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
    provider = _patch_brain(monkeypatch, reply="It is noon.")
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    # Answered via the tool-less brain think (provider.complete), not the agent.
    assert len(provider.calls) == 1
    assert provider.calls[0]["messages"] == [{"role": "user", "content": "what time is it"}]
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
    provider = _patch_brain(monkeypatch)
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    # Foreign chat → never answered, but the offset still advances past it.
    assert provider.calls == []
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
    provider = _patch_brain(monkeypatch, reply="done")
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    # Only the one well-formed, owner message is answered; offset clears the batch.
    assert len(provider.calls) == 1
    assert provider.calls[0]["messages"] == [{"role": "user", "content": "ok now"}]
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
    # Must return (not loop); a plain await that completes proves it.
    asyncio.run(telegram_inbound.run_forever(cfg))


async def _init_approval_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


def _patch_answer_callback(monkeypatch):
    calls = []

    async def _fake_answer(callback_query_id, *, text="", config=None):
        calls.append({"id": callback_query_id, "text": text})
        return True

    from lhassa.notify import telegram_api

    monkeypatch.setattr(telegram_api, "answer_callback_query", _fake_answer)
    return calls


def test_callback_query_approve_resolves_and_acks(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal", "task", {"accion": "run_command", "actor_id": "brain"}
        )
    )

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 200,
                "callback_query": {
                    "id": "cbq_1",
                    "data": f"approve:{ap_id}",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    ack_calls = _patch_answer_callback(monkeypatch)
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(
        telegram_inbound.poll_once(_cfg(instance_id="inst_personal"), offset=None)
    )

    assert new_offset == 201
    assert len(ack_calls) == 1 and ack_calls[0]["id"] == "cbq_1"
    assert "Approved" in ack_calls[0]["text"]
    assert any("Approved" in s for s in sent)
    remaining = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert remaining == []


def test_callback_query_deny_resolves_and_acks(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(db.add_approval("inst_personal", "task", {"accion": "run_command"}))

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 201,
                "callback_query": {
                    "id": "cbq_2",
                    "data": f"deny:{ap_id}",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    ack_calls = _patch_answer_callback(monkeypatch)
    _patch_send(monkeypatch)

    asyncio.run(telegram_inbound.poll_once(_cfg(instance_id="inst_personal"), offset=None))

    assert "Denied" in ack_calls[0]["text"]
    remaining = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert remaining == []


def test_callback_query_from_foreign_chat_is_ignored_but_acked(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(db.add_approval("inst_personal", "task", {"accion": "run_command"}))

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 202,
                "callback_query": {
                    "id": "cbq_3",
                    "data": f"approve:{ap_id}",
                    "message": {"chat": {"id": 999}},  # not the owner chat
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    ack_calls = _patch_answer_callback(monkeypatch)

    asyncio.run(telegram_inbound.poll_once(_cfg(instance_id="inst_personal"), offset=None))

    # Acked (so the tapper's spinner clears) but NOT resolved.
    assert len(ack_calls) == 1
    remaining = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert len(remaining) == 1


def test_callback_query_malformed_data_never_crashes(tmp_path, monkeypatch):
    asyncio.run(_init_approval_db(tmp_path, monkeypatch))

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 203,
                "callback_query": {
                    "id": "cbq_4",
                    "data": "not-a-recognised-format",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    ack_calls = _patch_answer_callback(monkeypatch)

    new_offset = asyncio.run(
        telegram_inbound.poll_once(_cfg(instance_id="inst_personal"), offset=None)
    )
    assert new_offset == 204  # offset still advances
    assert len(ack_calls) == 1  # still acked, just with an "unrecognised" note


async def _poll_and_drain(cfg, *, offset=None):
    """Run one poll_once cycle AND await any fire-and-forget tasks it scheduled
    (e.g. ``_launch_code_run`` via ``asyncio.ensure_future``) before the event
    loop this call owns goes away."""
    new_offset = await telegram_inbound.poll_once(cfg, offset=offset)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)
    return new_offset


def _text_update(update_id, text, *, chat_id=123):
    return {
        "ok": True,
        "result": [
            {
                "update_id": update_id,
                "message": {"chat": {"id": chat_id}, "text": text},
            }
        ],
    }


def test_code_reply_carries_inline_keyboard_no_leaked_marker(tmp_path, monkeypatch):
    asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    from lhassa.agents import local_cli

    monkeypatch.setattr(local_cli, "detect_ai_cli", lambda: "claude")

    sent = []

    async def _fake_send(text, *, config=None, reply_markup=None, parse_mode=None):
        sent.append({"text": text, "reply_markup": reply_markup})
        return True

    monkeypatch.setattr(telegram, "send", _fake_send)
    monkeypatch.setattr(
        telegram_inbound.httpx,
        "AsyncClient",
        _client_returning(_text_update(300, "/code fix the flaky test")),
    )

    new_offset = asyncio.run(
        telegram_inbound.poll_once(_cfg(instance_id="inst_personal"), offset=None)
    )

    assert new_offset == 301
    assert len(sent) == 1
    assert "[[" not in sent[0]["text"]  # marker never reaches the owner
    kb = sent[0]["reply_markup"]
    assert kb and "inline_keyboard" in kb
    row = kb["inline_keyboard"][0]
    assert row[0]["callback_data"].startswith("approve:")
    assert row[1]["callback_data"].startswith("deny:")


def test_callback_query_approve_code_run_launches_and_reports(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "code_run",
            {"accion": "code_run", "task": "fix the flaky test", "cli": "claude", "cwd": "/tmp"},
        )
    )

    from lhassa.agents import local_cli

    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "make_agent", lambda cli=None: object())

    from lhassa.agents import code_run

    async def _fake_stream_run(agent, run_id, run, task):
        run.output = "did the fix\nall good"
        run.status = "done"

    monkeypatch.setattr(code_run, "stream_run", _fake_stream_run)

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 210,
                "callback_query": {
                    "id": "cbq_code",
                    "data": f"approve:{ap_id}",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    _patch_answer_callback(monkeypatch)
    sent = _patch_send(monkeypatch)

    asyncio.run(_poll_and_drain(_cfg(instance_id="inst_personal")))

    assert any("Approved" in s for s in sent)
    assert any(s.startswith("Started: run #") and "claude" in s for s in sent)
    assert any("done" in s and "did the fix" in s for s in sent)


def test_callback_query_missing_fields_does_not_raise(monkeypatch):
    """A totally empty/garbage callback_query must never crash the poll loop."""
    payload = {
        "ok": True,
        "result": [{"update_id": 204, "callback_query": {}}],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))

    new_offset = asyncio.run(
        telegram_inbound.poll_once(_cfg(instance_id="inst_personal"), offset=None)
    )
    assert new_offset == 205




def test_tap_is_acknowledged_before_the_chat_message(tmp_path, monkeypatch):
    """answerCallbackQuery (clears the spinner) goes out before the ack line."""
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval("inst_personal", "task", {"accion": "run_command", "actor_id": "brain"})
    )
    order = []

    async def _fake_answer(callback_query_id, *, text="", config=None):
        order.append(("ack", text))
        return True

    async def _fake_send(text, *, config=None):
        order.append(("send", text))
        return True

    from lhassa.notify import telegram_api

    monkeypatch.setattr(telegram_api, "answer_callback_query", _fake_answer)
    monkeypatch.setattr(telegram, "send", _fake_send)
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 230,
                "callback_query": {
                    "id": "cbq_order",
                    "data": f"approve:{ap_id}",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))

    asyncio.run(telegram_inbound.poll_once(_cfg(instance_id="inst_personal"), offset=None))

    assert [kind for kind, _ in order] == ["ack", "send"]
    assert order[0][1] == order[1][1] == f"Approved {ap_id}."


def test_uploaded_document_reaches_document_hooks_with_a_getfile_fetch(monkeypatch):
    """A document update becomes an InboundDocument whose fetch() is getFile."""
    from lhassa.notify import inbound_hooks, telegram_api

    seen = {}

    async def _on_document(document, caption, *, config, chat_id="", reply, fetch):
        seen.update(document=document, caption=caption, chat_id=chat_id, data=await fetch())
        await reply("stored")
        return True

    monkeypatch.setattr(inbound_hooks, "INBOUND_HOOKS", inbound_hooks.Registry("inbound hook"))
    inbound_hooks.register_inbound_hook("probe", on_document=_on_document)

    async def _fake_get_file(file_id, *, config=None):
        return b"file:" + file_id.encode()

    monkeypatch.setattr(telegram_api, "get_file", _fake_get_file)
    sent = _patch_send(monkeypatch)
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 240,
                "message": {
                    "chat": {"id": 123},
                    "document": {"file_id": "FID", "file_name": "cv.tex"},
                    "caption": "Backend @ Acme",
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))

    assert asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None)) == 241
    assert seen == {
        "document": {"file_id": "FID", "file_name": "cv.tex"},
        "caption": "Backend @ Acme",
        "chat_id": "123",
        "data": b"file:FID",
    }
    assert sent == ["stored"]
