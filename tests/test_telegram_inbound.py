"""Tests for the inbound Telegram poller; httpx and the brain think fully mocked.

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


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _fresh_chat_state():
    """Module-level chat history/turn counters never leak between tests."""
    from lhassa.notify import telegram_inbound as _ti

    _ti._CHAT_HISTORY.clear()
    _ti._CHAT_TURNS.clear()
    _ti._CHAT_TOPIC.clear()
    yield
    _ti._CHAT_HISTORY.clear()
    _ti._CHAT_TURNS.clear()
    _ti._CHAT_TOPIC.clear()



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
    # The routine provider (opencode) raises, but the cloud fallback answers;
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
    # instead of the router's routine decision; the heartbeat seat is untouched.
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
    # A slash command is answered locally by the command registry; the tool-less
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


def test_poll_once_checkin_command_saves_without_brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 205,
                "message": {
                    "chat": {"id": 123},
                    "text": "/checkin dormí poco pero estoy enfocado",
                },
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    provider = _patch_brain(monkeypatch)
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(
        telegram_inbound.poll_once(_cfg(instance_id="inst_personal"), offset=None)
    )

    assert provider.calls == []
    assert sent and sent[0].startswith("Noted")
    assert new_offset == 206

    from lhassa.memory import checkins

    latest = asyncio.run(checkins.latest("inst_personal"))
    assert latest is not None
    assert "dormí poco" in latest.content


def test_poll_once_plain_reply_to_pending_checkin_is_captured(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    from datetime import datetime, timedelta, timezone

    asyncio.run(
        db.add_owner_prompt(
            "inst_personal",
            kind="personal_checkin",
            slot="evening",
            prompt="how was your day?",
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
    )
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 206,
                "message": {
                    "chat": {"id": 123},
                    "text": "Fue un día pesado, mañana necesito enfocarme en CV.",
                },
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    provider = _patch_brain(monkeypatch)
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(
        telegram_inbound.poll_once(
            _cfg(instance_id="inst_personal", digest_personal_checkin=True),
            offset=None,
        )
    )

    assert provider.calls == []
    assert sent and sent[0].startswith("Noted for the day")
    assert new_offset == 207
    assert asyncio.run(db.pending_owner_prompt("inst_personal", kind="personal_checkin")) is None

    from lhassa.memory import checkins

    latest = asyncio.run(checkins.latest("inst_personal"))
    assert latest is not None
    assert "día pesado" in latest.content


def test_today_and_tomorrow_commands_render_personal_agenda(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    asyncio.run(db.add_task("inst_personal", "send_digest", due_at=None))

    from lhassa.notify import commands

    today = asyncio.run(
        commands.dispatch("/today", config=_cfg(instance_id="inst_personal"))
    )
    tomorrow = asyncio.run(
        commands.dispatch("/tomorrow", config=_cfg(instance_id="inst_personal"))
    )

    assert "For today I see" in today
    assert "send_digest" in today
    assert "For tomorrow I see" in tomorrow


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
    assert sent == ["Unknown command /nope; try /help."]


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


def test_chain_kill_switch_stops_before_later_configured_provider(monkeypatch):
    from lhassa.llm.gateway import KillSwitchActive

    payload = {
        "ok": True,
        "result": [
            {"update_id": 85, "message": {"chat": {"id": 123}, "text": "hey"}}
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    _patch_brain(monkeypatch, raise_exc=RuntimeError("primary flaked"))
    sent = _patch_send(monkeypatch)

    killed = _named("default", raise_exc=KillSwitchActive("manual stop"))
    later = _named("claude", reply="must not run")
    # Deliberately non-default order: the behavior must follow exception
    # semantics, not assumptions about which provider is last.
    monkeypatch.setattr(
        telegram_inbound,
        "_fallback_chain",
        lambda config, primary: [(killed, None), (later, None)],
    )

    assert asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None)) == 86
    assert sent == [telegram_inbound._FALLBACK_REPLY]
    assert len(killed.calls) == 1
    assert later.calls == []


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


# ── /code; parks an approval with an inline keyboard; approve launches it ────


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

    from lhassa.api import code_routes

    async def _fake_stream_run(agent, run_id, run, task):
        run.output = "did the fix\nall good"
        run.status = "done"

    monkeypatch.setattr(code_routes, "_stream_run", _fake_stream_run)

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


def test_callback_query_deny_code_run_does_not_launch(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "code_run",
            {"accion": "code_run", "task": "fix the flaky test", "cli": "claude", "cwd": "/tmp"},
        )
    )

    from lhassa.api import code_routes

    launched = []

    async def _fake_stream_run(agent, run_id, run, task):
        launched.append(run_id)

    monkeypatch.setattr(code_routes, "_stream_run", _fake_stream_run)

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 211,
                "callback_query": {
                    "id": "cbq_code2",
                    "data": f"deny:{ap_id}",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    _patch_answer_callback(monkeypatch)
    sent = _patch_send(monkeypatch)

    asyncio.run(_poll_and_drain(_cfg(instance_id="inst_personal")))

    assert any("Denied" in s for s in sent)
    assert launched == []
    remaining = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert remaining == []


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


def test_natural_acuerdame_with_time_routes_to_remind(monkeypatch):
    """"acuérdame de un compromiso a las 5pm": the owner's literal phrase;
    never touches the brain: the deterministic remind path answers."""
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 300,
                "message": {"chat": {"id": 123}, "text": "acuérdame de un compromiso a las 5pm"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    provider = _patch_brain(monkeypatch)
    sent = _patch_send(monkeypatch)

    minted = []

    async def fake_remind(args, *, config=None):
        minted.append(args)
        return "⏰ Noted; I'll remind you at 17:00: un compromiso"

    from lhassa.notify import commands as commands_mod

    monkeypatch.setitem(commands_mod.COMMANDS._items, "remind", fake_remind)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert provider.calls == []  # no LLM
    assert minted == ["de un compromiso a las 5pm"]
    assert sent and sent[0].startswith("⏰ Noted")


def test_natural_acuerdame_without_time_falls_through(monkeypatch):
    """A time-less "acuérdame que…" is NOT a timed reminder; it falls through
    (to the remember/fact/brain paths), never mis-minting a task."""
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 301,
                "message": {"chat": {"id": 123}, "text": "acuérdame que la vida es bella"},
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    _patch_brain(monkeypatch, reply="anotado")
    sent = _patch_send(monkeypatch)

    called = []

    async def fake_remind(args, *, config=None):
        called.append(args)
        return "should not run"

    from lhassa.notify import commands as commands_mod

    monkeypatch.setitem(commands_mod.COMMANDS._items, "remind", fake_remind)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert called == []
    assert sent  # answered by a later path (remember hook or the brain)


# ── Deterministic capability shortcuts (the seam a pack plugs into) ─────────


def _probe_shortcut_catalog(monkeypatch):
    """A one-entry catalog with an always-on shortcut, a continuation and a
    topic context: the shape the calendar pack registers, without the pack."""
    from lhassa.notify import capabilities

    async def _handler(text, *, config=None):
        return None

    async def _shortcut(text, *, config=None):
        if "probe" not in text.lower():
            return None
        return f"probe says: {text}", "probe-query: now"

    async def _continue(text, context, *, config=None):
        if "later" not in text.lower():
            return None
        return f"probe continues from {context.get('day')}"

    cap = capabilities.Capability(
        "probe_view",
        lambda text: "probe" in text.lower(),
        "stand-in",
        _handler,
        continue_followup=_continue,
        shortcut=_shortcut,
        topic_context=lambda text: {"day": "monday" if "monday" in text else ""},
    )
    monkeypatch.setattr(capabilities, "CAPABILITIES", [cap])
    return cap


def test_capability_shortcut_answers_without_provider(monkeypatch):
    """An always-on shortcut is served before the think ($0, no provider call)
    and logs one episode with the decision the shortcut names."""
    _probe_shortcut_catalog(monkeypatch)
    provider = _patch_brain(monkeypatch)
    sent = _patch_send(monkeypatch)
    monkeypatch.setattr(
        telegram_inbound.httpx,
        "AsyncClient",
        _client_returning(_payload_for("probe monday", 400)),
    )

    from lhassa.core import episode as episode_mod

    logged = []

    async def _capture_append(self):
        logged.append(self)
        return "ep"

    monkeypatch.setattr(episode_mod.Episode, "append", _capture_append)

    cfg = _cfg()
    asyncio.run(telegram_inbound.poll_once(cfg, offset=None))

    assert provider.calls == []
    assert sent == ["probe says: probe monday"]
    assert len(logged) == 1
    assert logged[0].decision == "probe-query: now"
    assert logged[0].accion == "probe_view"
    assert logged[0].costo_usd == 0.0
    topic = telegram_inbound._topic_get(cfg, "123")
    assert topic["capability"] == "probe_view"
    assert topic["context"] == {"day": "monday"}


def test_followup_continues_a_shortcut_topic_without_provider(monkeypatch):
    _probe_shortcut_catalog(monkeypatch)
    provider = _patch_brain(monkeypatch)
    sent = _patch_send(monkeypatch)
    cfg = _cfg()

    monkeypatch.setattr(
        telegram_inbound.httpx,
        "AsyncClient",
        _client_returning(_payload_for("probe monday", 410)),
    )
    asyncio.run(telegram_inbound.poll_once(cfg, offset=None))
    monkeypatch.setattr(
        telegram_inbound.httpx,
        "AsyncClient",
        _client_returning(_payload_for("y later?", 411)),
    )
    asyncio.run(telegram_inbound.poll_once(cfg, offset=None))

    assert provider.calls == []
    assert sent[-1] == "probe continues from monday"
    # a falsy refreshed context value never clobbers the sticky one
    assert telegram_inbound._topic_get(cfg, "123")["context"] == {"day": "monday"}


def test_no_registered_capability_falls_through_to_think(monkeypatch):
    """Core alone (an empty catalog): a calendar question reaches the think."""
    from lhassa.notify import capabilities

    monkeypatch.setattr(capabilities, "CAPABILITIES", [])
    provider = _patch_brain(monkeypatch, reply="I have no calendar here.")
    sent = _patch_send(monkeypatch)
    monkeypatch.setattr(
        telegram_inbound.httpx,
        "AsyncClient",
        _client_returning(_payload_for("¿qué hay en mi calendario el viernes?", 420)),
    )
    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert len(provider.calls) == 1
    assert sent == ["I have no calendar here."]


# ── the self-routing consciousness (lhassa.notify.capabilities) ─────────────────


def test_unrecognized_message_is_not_tagged(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {"update_id": 403, "message": {"chat": {"id": 123}, "text": "tell me a joke"}},
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    _patch_brain(monkeypatch, reply="Why did the chicken...")
    _patch_send(monkeypatch)

    import lhassa.core.episode as episode_mod

    logged = []

    async def _capture_append(self):
        logged.append(self)
        return "ep"

    monkeypatch.setattr(episode_mod.Episode, "append", _capture_append)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert len(logged) == 1
    assert "[routable:" not in logged[0].decision


def test_callback_query_approve_mint_route_turns_on_the_route(tmp_path, monkeypatch):
    """Approving a mint_route proposal via the inline keyboard applies it;
    the SAME approval->apply pattern as an approved code_run launching."""
    _probe_shortcut_catalog(monkeypatch)
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "mint_route",
            {"accion": "mint_route", "capability": "probe_view", "count": 6},
        )
    )

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 220,
                "callback_query": {
                    "id": "cbq_route",
                    "data": f"approve:{ap_id}",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    _patch_answer_callback(monkeypatch)
    sent = _patch_send(monkeypatch)

    from lhassa.notify import capabilities

    cfg = _cfg(instance_id="inst_personal", routes_file=str(tmp_path / "routes.json"))
    asyncio.run(_poll_and_drain(cfg))

    assert any("Approved" in s for s in sent)
    assert capabilities.is_routed("probe_view", config=cfg)


def test_callback_query_deny_mint_route_does_not_turn_on_the_route(tmp_path, monkeypatch):
    _probe_shortcut_catalog(monkeypatch)
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "mint_route",
            {"accion": "mint_route", "capability": "probe_view", "count": 6},
        )
    )

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 221,
                "callback_query": {
                    "id": "cbq_route2",
                    "data": f"deny:{ap_id}",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    _patch_answer_callback(monkeypatch)
    sent = _patch_send(monkeypatch)

    from lhassa.notify import capabilities

    cfg = _cfg(instance_id="inst_personal", routes_file=str(tmp_path / "routes.json"))
    asyncio.run(_poll_and_drain(cfg))

    assert any("Denied" in s for s in sent)
    assert not capabilities.is_routed("probe_view", config=cfg)


# ── sticky topic + follow-up thread continuity ───────────────────────────────


def test_topic_set_then_get(monkeypatch):
    cfg = _cfg()
    assert telegram_inbound._topic_get(cfg, "123") is None
    telegram_inbound._topic_set(cfg, "123", "calendar_view", {"foo": "bar"})
    topic = telegram_inbound._topic_get(cfg, "123")
    assert topic is not None
    assert topic["capability"] == "calendar_view"
    assert topic["context"] == {"foo": "bar"}


def test_topic_expires_after_ttl(monkeypatch):
    cfg = _cfg(chat_topic_ttl=1.0)
    telegram_inbound._topic_set(cfg, "123", "calendar_view")

    real_time = telegram_inbound.time.time

    monkeypatch.setattr(
        telegram_inbound.time, "time", lambda: real_time() + 10.0
    )
    assert telegram_inbound._topic_get(cfg, "123") is None


def _payload_for(text, update_id=500):
    return {
        "ok": True,
        "result": [
            {"update_id": update_id, "message": {"chat": {"id": 123}, "text": text}}
        ],
    }


def test_followup_with_no_active_topic_falls_through_to_think(monkeypatch):
    """A connector-led message with NO prior sticky topic falls through to the
    normal tool-less think; is_followup alone is never enough."""
    provider = _patch_brain(monkeypatch, reply="Sure, what about it?")
    sent = _patch_send(monkeypatch)

    monkeypatch.setattr(
        telegram_inbound.httpx,
        "AsyncClient",
        _client_returning(_payload_for("y el sabado?", 502)),
    )
    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert len(provider.calls) == 1
    assert sent == ["Sure, what about it?"]


def test_callback_query_approve_mint_task_runs_now_and_reports(tmp_path, monkeypatch):
    """Approving a parked mint_task (e.g. a protected-root read) fires the same
    early-runner+report the [[action:]] marker uses; the owner's tap is
    followed by the result, not by a wait for the next beat."""
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    asyncio.run(
        db.add_task(
            "inst_personal", "read_file", params={"path": "/vault/nota.md"},
            origen_actor="telegram", origen_trust=2,
            estado="esperando_aprobacion", task_id="t_prot",
        )
    )
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal", "task",
            {"accion": "read_file", "params": {"path": "/vault/nota.md"},
             "task_id": "t_prot", "source": "mint_task", "actor_id": "telegram"},
        )
    )

    ran = []

    async def _fake_run_report(instance_id, task_id, name, params, config):
        ran.append({"task_id": task_id, "name": name, "params": params})

    monkeypatch.setattr(telegram_inbound, "_run_action_and_report", _fake_run_report)

    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 400,
                "callback_query": {
                    "id": "cbq_p",
                    "data": f"approve:{ap_id}",
                    "message": {"chat": {"id": 123}},
                },
            }
        ],
    }
    monkeypatch.setattr(telegram_inbound.httpx, "AsyncClient", _client_returning(payload))
    _patch_answer_callback(monkeypatch)
    _patch_send(monkeypatch)

    asyncio.run(_poll_and_drain(_cfg(instance_id="inst_personal"), offset=None))

    assert ran == [{"task_id": "t_prot", "name": "read_file",
                    "params": {"path": "/vault/nota.md"}}]
    # resolve() released the task; the (patched-out) runner would pick it up.
    pending = asyncio.run(db.load_pending_tasks("inst_personal"))
    assert [t["id"] for t in pending] == ["t_prot"]


def test_empty_reasoning_reply_retries_with_bigger_cap(monkeypatch):
    """A reasoning model that bills tokens but returns EMPTY content (deepseek V4
    Flash hitting max_tokens mid-thought; observed live 2026-07-30) must be
    retried once with a bigger cap, and NEVER reported as 'can't reach my
    model': the model was reached and billed."""
    payload = {
        "ok": True,
        "result": [
            {"update_id": 500, "message": {"chat": {"id": 123}, "text": "hola"}}
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    class _EmptyThenReal(_FakeProvider):
        async def complete(self, messages, *, model=None, max_tokens=None, system=None):
            self.calls.append({"max_tokens": max_tokens})
            if len(self.calls) == 1:
                return SimpleNamespace(text="", cost_usd=0.0004, output_tokens=512)
            return SimpleNamespace(text="ahora sí respondo", cost_usd=0.0006, output_tokens=40)

    prov = _EmptyThenReal()
    prov.name = "default"
    _patch_brain(monkeypatch)
    import lhassa.llm.router as router_mod
    monkeypatch.setattr(
        router_mod, "decide",
        lambda trigger, ctx, config: SimpleNamespace(provider=prov, model="m", reason="t"),
    )
    monkeypatch.setattr(telegram_inbound, "_fallback_chain", lambda config, primary: [])
    sent = _patch_send(monkeypatch)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert sent == ["ahora sí respondo"]
    assert len(prov.calls) == 2
    assert prov.calls[1]["max_tokens"] > prov.calls[0]["max_tokens"]


def test_empty_reply_twice_gets_the_honest_empty_message(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {"update_id": 501, "message": {"chat": {"id": 123}, "text": "hola"}}
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    class _AlwaysEmpty(_FakeProvider):
        async def complete(self, messages, *, model=None, max_tokens=None, system=None):
            self.calls.append({})
            return SimpleNamespace(text="", cost_usd=0.0004, output_tokens=512)

    prov = _AlwaysEmpty()
    prov.name = "default"
    _patch_brain(monkeypatch)
    import lhassa.llm.router as router_mod
    monkeypatch.setattr(
        router_mod, "decide",
        lambda trigger, ctx, config: SimpleNamespace(provider=prov, model="m", reason="t"),
    )
    monkeypatch.setattr(telegram_inbound, "_fallback_chain", lambda config, primary: [])
    sent = _patch_send(monkeypatch)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert sent == [telegram_inbound._EMPTY_REPLY]  # never the 'can't reach' lie


def test_cv_with_bare_title_asks_for_the_posting_without_waking_brains(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {"update_id": 502, "message": {"chat": {"id": 123}, "text": "/cv Analista de datos"}}
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )
    ran = []

    async def fake_flow(**kw):
        ran.append(kw)
        return None, "flow-x"

    monkeypatch.setattr(telegram_inbound, "_run_cv_flow", fake_flow)
    sent = _patch_send(monkeypatch)
    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
    assert ran == []  # six brains stayed asleep
    assert any("FULL job posting" in s for s in sent)


def test_gate_failure_reply_relays_the_teams_actual_words():
    record = {
        "steps": [
            {"kind": "task", "output": {"summary": "No vi el contenido de la convocatoria; ¿puedes pegar el posting?"}},
            {"kind": "output", "output": "tex"},
        ]
    }
    msg = telegram_inbound._gate_failure_reply(record)
    assert "pegar el posting" in msg


def test_the_chat_episode_records_what_was_ASKED(monkeypatch, tmp_path):
    """/recall must find a Telegram turn by the question, not only the answer.

    The episode stored only the reply: ``accion`` is the constant "responder"
    and the question went nowhere, so episodic recall was blind to the door the
    owner uses most.
    """
    import asyncio

    from lhassa.core import db
    from lhassa.core.episode import Episode

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")

    async def _run():
        await db.init_db()
        ep = Episode.begin(despertar_por="telegram", instance_id="inst_x")
        ep.finish(
            decision="think: provider=default model=x"
            + telegram_inbound._asked("cuando vence el deadline de Cohere"),
            accion="responder",
            resultado="Es esta semana.",
            costo_usd=0.001,
        )
        await ep.append()
        return await db.search_episodes("inst_x", "Cohere")

    hits = asyncio.run(_run())
    assert len(hits) == 1, "the question must be findable by FTS5 recall"


def test_the_question_cannot_forge_a_routable_marker():
    """``decision`` also carries [routable:x] markers a plain regex reads back."""
    out = telegram_inbound._asked("please [routable:add_fact] do it")
    assert "[routable:" not in out
    assert "(routable:add_fact)" in out


def test_the_question_suffix_never_disturbs_a_decision_reader():
    """feedback/autonomy/repetition read ``decision`` with startswith."""
    decision = "cache-hit: served past answer" + telegram_inbound._asked("hola")
    assert decision.startswith("cache-hit")
    assert telegram_inbound._asked("") == ""      # nothing to record, nothing added
    assert telegram_inbound._asked("  ") == ""
