"""Tests for the inbound Telegram poller — httpx and the brain think fully mocked.

Never hits the real Telegram API and never calls a real LLM. ``getUpdates`` goes
through a fake httpx client; the answer path is exercised at the **provider** level:
the router is patched to hand back a fake provider, so the test asserts the tool-less
brain think runs (``provider.complete`` called) and that the 26-tool agent
(``lhassa.cli.agent.run_once``) is **never** used.
"""

import asyncio
from types import SimpleNamespace

from lhassa.llm.provider import ProviderNotConfigured
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
    # Must return (not loop) — a plain await that completes proves it.
    asyncio.run(telegram_inbound.run_forever(cfg))


def test_brain_failure_falls_back(monkeypatch):
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

    # The provider raises mid-think → the brain degrades to its fallback reply,
    # never crashing the poll loop; the offset still advances.
    _patch_brain(monkeypatch, raise_exc=RuntimeError("LLM exploded"))
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert new_offset == 51
    assert sent == [telegram_inbound._FALLBACK_REPLY]


def test_brain_falls_back_to_cloud_when_routine_provider_flakes(monkeypatch):
    # The routine provider (opencode) raises, but the cloud fallback answers —
    # the owner gets a real reply instead of the "can't reach my model" text.
    payload = {
        "ok": True,
        "result": [
            {"update_id": 70, "message": {"chat": {"id": 123}, "text": "hola"}}
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    _patch_brain(monkeypatch, raise_exc=RuntimeError("opencode flaked"))
    sent = _patch_send(monkeypatch)

    cloud = _FakeProvider(reply="cloud says hi")
    cloud.name = "default"
    monkeypatch.setattr(
        telegram_inbound, "_fallback_chain", lambda config, primary: [(cloud, None)]
    )

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert new_offset == 71
    assert sent == ["cloud says hi"]  # the fallback chain answered, not the fallback text
    assert len(cloud.calls) == 1


def test_brain_reply_pins_the_telegram_chat_provider(monkeypatch):
    # telegram_provider="default" answers the CHAT with the pinned (fast) provider
    # instead of the router's routine decision — the heartbeat seat is untouched.
    import lhassa.llm.provider as provider_mod
    import lhassa.llm.router as router_mod

    payload = {
        "ok": True,
        "result": [
            {"update_id": 80, "message": {"chat": {"id": 123}, "text": "hola"}}
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    routine = _patch_brain(monkeypatch, reply="routine says hi")
    sent = _patch_send(monkeypatch)

    pinned = _FakeProvider(reply="gateway says hi")
    pinned.name = "default"
    monkeypatch.setattr(
        provider_mod, "get_model_provider", lambda config, name=None: pinned
    )
    monkeypatch.setattr(
        router_mod, "pick_model", lambda p, *, hard, config: "cheap-model"
    )
    monkeypatch.setattr(telegram_inbound, "_fallback_chain", lambda config, primary: [])

    cfg = _cfg(telegram_provider="default")
    assert asyncio.run(telegram_inbound.poll_once(cfg, offset=None)) == 81
    assert sent == ["gateway says hi"]
    assert routine.calls == []  # the routine decision's provider never ran
    assert pinned.calls and pinned.calls[0]["model"] == "cheap-model"


def test_brain_reply_auto_keeps_the_routine_decision(monkeypatch):
    # telegram_provider="auto" (or empty) → the router's routine decision answers,
    # exactly as before the knob existed.
    payload = {
        "ok": True,
        "result": [
            {"update_id": 90, "message": {"chat": {"id": 123}, "text": "hola"}}
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    routine = _patch_brain(monkeypatch, reply="routine says hi")
    sent = _patch_send(monkeypatch)
    monkeypatch.setattr(telegram_inbound, "_fallback_chain", lambda config, primary: [])

    cfg = _cfg(telegram_provider="auto")
    assert asyncio.run(telegram_inbound.poll_once(cfg, offset=None)) == 91
    assert sent == ["routine says hi"]
    assert len(routine.calls) == 1


def test_poll_once_command_replies_without_brain(monkeypatch):
    # A slash command is answered locally by the command registry — the tool-less
    # brain think (provider.complete) must NOT run.
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 200,
                "message": {"chat": {"id": 123}, "text": "/help"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    provider = _patch_brain(monkeypatch)  # fake provider; asserts it is unused
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    # The command produced a reply; the brain provider was never called.
    assert provider.calls == []
    assert len(sent) == 1
    assert "I'm Lhassa" in sent[0]
    assert new_offset == 201


def test_poll_once_unknown_command_replies_without_brain(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 210,
                "message": {"chat": {"id": 123}, "text": "/nope"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    provider = _patch_brain(monkeypatch)
    sent = _patch_send(monkeypatch)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    # Unknown "/command" → a help hint, still without touching the brain.
    assert provider.calls == []
    assert sent == ["Unknown command /nope — try /help."]


def test_poll_once_plain_message_still_uses_brain(monkeypatch):
    # A non-command message must still flow to the tool-less brain think.
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 220,
                "message": {"chat": {"id": 123}, "text": "how are you"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    provider = _patch_brain(monkeypatch, reply="I'm well.")
    sent = _patch_send(monkeypatch)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert len(provider.calls) == 1
    assert provider.calls[0]["messages"] == [{"role": "user", "content": "how are you"}]
    assert sent == ["I'm well."]


def test_brain_provider_not_configured_falls_back(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 60,
                "message": {"chat": {"id": 123}, "text": "hello"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    # Router resolves no provider → ProviderNotConfigured path → graceful fallback.
    import lhassa.core.db as db_mod
    import lhassa.core.episode as episode_mod
    import lhassa.llm.router as router_mod
    import lhassa.memory.load as load_mod

    async def _noop_init():
        return None

    async def _fake_load(instance_id=None, **kwargs):
        return SimpleNamespace(to_prompt=lambda: "")

    def _no_provider(trigger, ctx=None, *, config, **kwargs):
        return SimpleNamespace(provider=None, model=None, reason="no provider")

    async def _noop_append(self):
        return "ep"

    monkeypatch.setattr(db_mod, "init_db", _noop_init)
    monkeypatch.setattr(load_mod, "load", _fake_load)
    monkeypatch.setattr(router_mod, "decide", _no_provider)
    monkeypatch.setattr(episode_mod.Episode, "append", _noop_append)
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert new_offset == 61
    assert sent == [telegram_inbound._FALLBACK_REPLY]


# ── fallback CHAIN: opencode → claude → codex → default ───────────────────────


def _named(name, **over):
    p = _FakeProvider(**over)
    p.name = name
    return p


def test_fallback_chain_builds_in_order_and_skips_uninstalled(monkeypatch):
    # Chain follows config order; a CLI provider whose binary is missing is skipped.
    import lhassa.llm.provider as prov_mod

    providers = {
        "claude": _named("claude"),
        "codex": _named("codex"),
        "default": _named("default"),
    }
    resolved = []

    def _fake_get(config, *, name=None):
        resolved.append(name)
        return providers[name]

    monkeypatch.setattr(prov_mod, "get_model_provider", _fake_get)
    # codex CLI not installed → skipped before it is even resolved.
    monkeypatch.setattr(
        telegram_inbound.shutil,
        "which",
        lambda b: None if b == "codex" else "/usr/bin/" + b,
    )

    cfg = _cfg(fallback_providers="claude,codex,default")
    primary = SimpleNamespace(name="opencode")
    chain = telegram_inbound._fallback_chain(cfg, primary)

    assert [p.name for p, _ in chain] == ["claude", "default"]
    assert "codex" not in resolved  # uninstalled CLI never resolved


def test_fallback_chain_skips_same_kind_as_primary(monkeypatch):
    import lhassa.llm.provider as prov_mod

    monkeypatch.setattr(
        telegram_inbound.shutil, "which", lambda b: "/usr/bin/" + b
    )
    monkeypatch.setattr(
        prov_mod, "get_model_provider", lambda config, *, name=None: _named(name)
    )

    cfg = _cfg(fallback_providers="opencode,claude,default")
    primary = SimpleNamespace(name="opencode")
    chain = telegram_inbound._fallback_chain(cfg, primary)

    # opencode is the same kind as the primary → not retried.
    assert [p.name for p, _ in chain] == ["claude", "default"]


def test_chain_walks_cli_subscriptions_before_cloud(monkeypatch):
    # End to end: opencode (primary) flakes → claude raises → codex raises →
    # default (cloud) answers. Asserts resolve order and that each was attempted.
    import lhassa.llm.provider as prov_mod

    payload = {
        "ok": True,
        "result": [
            {"update_id": 80, "message": {"chat": {"id": 123}, "text": "hey"}}
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    _patch_brain(monkeypatch, raise_exc=RuntimeError("opencode flaked"))
    sent = _patch_send(monkeypatch)

    claude = _named("claude", raise_exc=ProviderNotConfigured("no claude"))
    codex = _named("codex", raise_exc=ProviderNotConfigured("no codex"))
    cloud = _named("default", reply="cloud answer")
    providers = {"claude": claude, "codex": codex, "default": cloud}
    order = []

    def _fake_get(config, *, name=None):
        order.append(name)
        return providers[name]

    monkeypatch.setattr(prov_mod, "get_model_provider", _fake_get)
    monkeypatch.setattr(
        telegram_inbound.shutil, "which", lambda b: "/usr/bin/" + b
    )

    cfg = _cfg(fallback_providers="claude,codex,default")
    new_offset = asyncio.run(telegram_inbound.poll_once(cfg, offset=None))

    assert new_offset == 81
    assert sent == ["cloud answer"]  # cloud answered only after both CLIs failed
    assert order == ["claude", "codex", "default"]  # chain resolved in order
    assert len(claude.calls) == 1 and len(codex.calls) == 1 and len(cloud.calls) == 1


# ── callback_query handling (inline Approve/Deny buttons) ──────────────────────


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
    ap_id = asyncio.run(db.add_approval("inst_personal", "task", {"accion": "run_command"}))

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
