"""Tests for the chat engine (``lhassa.chat``); the brain think fully mocked.

Transport-free: every turn is played through ``tests.chat_fakes.FakeTransport``
(the engine's own contract: an ``InboundMessage`` in, a ``Reply`` out), so these
run with core alone. The answer path is exercised at the **provider** level:
the router is patched to hand back a fake provider, so a test asserts the
tool-less brain think runs (``provider.complete`` called) and that the 26-tool
agent (``lhassa.cli.agent.run_once``) is **never** used. The Telegram wire
(getUpdates, offsets, the owner-only gate, inline keyboards) is tested in the
telegram pack.
"""

import asyncio
from types import SimpleNamespace

from lhassa.chat import callbacks, state, think
from lhassa.chat.types import Button, Reply
from lhassa.llm.provider import ProviderNotConfigured
from tests.chat_fakes import FakeTransport, run_and_drain


def _cfg(**over):
    base = dict(
        telegram_bot_token="TOKEN",
        telegram_chat_id="123",
        telegram_poll_enabled=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


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
        raise AssertionError("the chat door must not call the 26-tool agent (run_once)")

    monkeypatch.setattr(db_mod, "init_db", _noop_init)
    monkeypatch.setattr(load_mod, "load", _fake_load)
    monkeypatch.setattr(router_mod, "decide", _fake_decide)
    monkeypatch.setattr(episode_mod.Episode, "append", _noop_append)
    monkeypatch.setattr(agent_mod, "run_once", _boom_agent)
    return provider


def _say(text, cfg=None, *, transport=None):
    """Play one text turn; return the transport (its ``sent`` holds the replies)."""
    transport = transport or FakeTransport()
    asyncio.run(transport.say(text, config=cfg or _cfg()))
    return transport


# ── the plain-language path: the tool-less brain think ─────────────────────────


def test_plain_message_answered_by_tool_less_think(monkeypatch):
    provider = _patch_brain(monkeypatch, reply="It is noon.")
    sent = _say("what time is it").sent

    # Answered via the tool-less brain think (provider.complete), not the agent.
    assert len(provider.calls) == 1
    assert provider.calls[0]["messages"] == [{"role": "user", "content": "what time is it"}]
    assert sent == ["It is noon."]


def test_handle_returns_the_reply_and_sends_nothing(monkeypatch):
    """``handle`` is the pure half: it returns the Reply; only ``respond`` sends."""
    from lhassa import chat

    _patch_brain(monkeypatch, reply="I'm well.")
    transport = FakeTransport()
    reply = asyncio.run(chat.handle(transport.message("how are you"), config=_cfg()))
    assert reply == Reply(text="I'm well.")
    assert transport.sent == []


def test_blank_text_is_not_a_turn(monkeypatch):
    provider = _patch_brain(monkeypatch)
    from lhassa import chat

    assert asyncio.run(chat.handle(FakeTransport().message("   "), config=_cfg())) is None
    assert provider.calls == []


def test_brain_failure_falls_back(monkeypatch):
    # The provider raises mid-think: the brain degrades to its fallback reply,
    # never crashing the door.
    _patch_brain(monkeypatch, raise_exc=RuntimeError("LLM exploded"))
    assert _say("boom please").sent == [think._FALLBACK_REPLY]


def test_brain_falls_back_to_cloud_when_routine_provider_flakes(monkeypatch):
    # The routine provider (opencode) raises, but the cloud fallback answers;
    # the owner gets a real reply instead of the "can't reach my model" text.
    _patch_brain(monkeypatch, raise_exc=RuntimeError("opencode flaked"))
    cloud = _FakeProvider(reply="cloud says hi")
    cloud.name = "default"
    monkeypatch.setattr(think, "_fallback_chain", lambda config, primary: [(cloud, None)])

    assert _say("hola").sent == ["cloud says hi"]  # the chain answered
    assert len(cloud.calls) == 1


def test_brain_reply_pins_the_telegram_chat_provider(monkeypatch):
    # telegram_provider="default" answers the CHAT with the pinned (fast) provider
    # instead of the router's routine decision; the heartbeat seat is untouched.
    import lhassa.llm.provider as provider_mod
    import lhassa.llm.router as router_mod

    routine = _patch_brain(monkeypatch, reply="routine says hi")
    pinned = _FakeProvider(reply="gateway says hi")
    pinned.name = "default"
    monkeypatch.setattr(provider_mod, "get_model_provider", lambda config, name=None: pinned)
    monkeypatch.setattr(router_mod, "pick_model", lambda p, *, hard, config: "cheap-model")
    monkeypatch.setattr(think, "_fallback_chain", lambda config, primary: [])

    assert _say("hola", _cfg(telegram_provider="default")).sent == ["gateway says hi"]
    assert routine.calls == []  # the routine decision's provider never ran
    assert pinned.calls and pinned.calls[0]["model"] == "cheap-model"


def test_brain_reply_auto_keeps_the_routine_decision(monkeypatch):
    # telegram_provider="auto" (or empty): the router's routine decision answers.
    routine = _patch_brain(monkeypatch, reply="routine says hi")
    monkeypatch.setattr(think, "_fallback_chain", lambda config, primary: [])

    assert _say("hola", _cfg(telegram_provider="auto")).sent == ["routine says hi"]
    assert len(routine.calls) == 1


def test_brain_provider_not_configured_falls_back(monkeypatch):
    # Router resolves no provider: ProviderNotConfigured path, graceful fallback.
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

    assert _say("hello").sent == [think._FALLBACK_REPLY]


def test_empty_reasoning_reply_retries_with_bigger_cap(monkeypatch):
    """A reasoning model that bills tokens but returns EMPTY content (deepseek V4
    Flash hitting max_tokens mid-thought; observed live 2026-07-30) must be
    retried once with a bigger cap, and NEVER reported as 'can't reach my
    model': the model was reached and billed."""

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
    monkeypatch.setattr(think, "_fallback_chain", lambda config, primary: [])

    assert _say("hola").sent == ["ahora sí respondo"]
    assert len(prov.calls) == 2
    assert prov.calls[1]["max_tokens"] > prov.calls[0]["max_tokens"]


def test_empty_reply_twice_gets_the_honest_empty_message(monkeypatch):
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
    monkeypatch.setattr(think, "_fallback_chain", lambda config, primary: [])

    assert _say("hola").sent == [think._EMPTY_REPLY]  # never the 'can't reach' lie


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
    # codex CLI not installed: skipped before it is even resolved.
    monkeypatch.setattr(
        think.shutil, "which", lambda b: None if b == "codex" else "/usr/bin/" + b
    )

    cfg = _cfg(fallback_providers="claude,codex,default")
    chain = think._fallback_chain(cfg, SimpleNamespace(name="opencode"))

    assert [p.name for p, _ in chain] == ["claude", "default"]
    assert "codex" not in resolved  # uninstalled CLI never resolved


def test_fallback_chain_skips_same_kind_as_primary(monkeypatch):
    import lhassa.llm.provider as prov_mod

    monkeypatch.setattr(think.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(
        prov_mod, "get_model_provider", lambda config, *, name=None: _named(name)
    )

    cfg = _cfg(fallback_providers="opencode,claude,default")
    chain = think._fallback_chain(cfg, SimpleNamespace(name="opencode"))

    # opencode is the same kind as the primary: not retried.
    assert [p.name for p, _ in chain] == ["claude", "default"]


def test_chain_walks_cli_subscriptions_before_cloud(monkeypatch):
    # End to end: opencode (primary) flakes, claude raises, codex raises,
    # default (cloud) answers. Asserts resolve order and that each was attempted.
    import lhassa.llm.provider as prov_mod

    _patch_brain(monkeypatch, raise_exc=RuntimeError("opencode flaked"))
    claude = _named("claude", raise_exc=ProviderNotConfigured("no claude"))
    codex = _named("codex", raise_exc=ProviderNotConfigured("no codex"))
    cloud = _named("default", reply="cloud answer")
    providers = {"claude": claude, "codex": codex, "default": cloud}
    order = []

    def _fake_get(config, *, name=None):
        order.append(name)
        return providers[name]

    monkeypatch.setattr(prov_mod, "get_model_provider", _fake_get)
    monkeypatch.setattr(think.shutil, "which", lambda b: "/usr/bin/" + b)

    sent = _say("hey", _cfg(fallback_providers="claude,codex,default")).sent

    assert sent == ["cloud answer"]  # cloud answered only after both CLIs failed
    assert order == ["claude", "codex", "default"]  # chain resolved in order
    assert len(claude.calls) == 1 and len(codex.calls) == 1 and len(cloud.calls) == 1


def test_chain_kill_switch_stops_before_later_configured_provider(monkeypatch):
    from lhassa.llm.gateway import KillSwitchActive

    _patch_brain(monkeypatch, raise_exc=RuntimeError("primary flaked"))
    killed = _named("default", raise_exc=KillSwitchActive("manual stop"))
    later = _named("claude", reply="must not run")
    # Deliberately non-default order: the behavior must follow exception
    # semantics, not assumptions about which provider is last.
    monkeypatch.setattr(
        think, "_fallback_chain", lambda config, primary: [(killed, None), (later, None)]
    )

    assert _say("hey").sent == [think._FALLBACK_REPLY]
    assert len(killed.calls) == 1
    assert later.calls == []


# ── slash commands and the deterministic short-circuits ───────────────────────


def test_command_replies_without_brain(monkeypatch):
    # A slash command is answered locally by the command registry; the tool-less
    # brain think (provider.complete) must NOT run.
    provider = _patch_brain(monkeypatch)  # fake provider; asserts it is unused
    sent = _say("/help").sent

    assert provider.calls == []
    assert len(sent) == 1
    assert "I'm Lhassa" in sent[0]


def test_checkin_command_saves_without_brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    provider = _patch_brain(monkeypatch)

    sent = _say(
        "/checkin dormí poco pero estoy enfocado", _cfg(instance_id="inst_personal")
    ).sent

    assert provider.calls == []
    assert sent and sent[0].startswith("Noted")

    from lhassa.memory import checkins

    latest = asyncio.run(checkins.latest("inst_personal"))
    assert latest is not None
    assert "dormí poco" in latest.content


def test_plain_reply_to_pending_checkin_is_captured(tmp_path, monkeypatch):
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
    provider = _patch_brain(monkeypatch)

    sent = _say(
        "Fue un día pesado, mañana necesito enfocarme en CV.",
        _cfg(instance_id="inst_personal", digest_personal_checkin=True),
    ).sent

    assert provider.calls == []
    assert sent and sent[0].startswith("Noted for the day")
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

    today = asyncio.run(commands.dispatch("/today", config=_cfg(instance_id="inst_personal")))
    tomorrow = asyncio.run(
        commands.dispatch("/tomorrow", config=_cfg(instance_id="inst_personal"))
    )

    assert "For today I see" in today
    assert "send_digest" in today
    assert "For tomorrow I see" in tomorrow


def test_unknown_command_replies_without_brain(monkeypatch):
    provider = _patch_brain(monkeypatch)
    sent = _say("/nope").sent

    # Unknown "/command": a help hint, still without touching the brain.
    assert provider.calls == []
    assert sent == ["Unknown command /nope; try /help."]


def test_natural_acuerdame_with_time_routes_to_remind(monkeypatch):
    """"acuérdame de un compromiso a las 5pm": the owner's literal phrase;
    never touches the brain: the deterministic remind path answers."""
    provider = _patch_brain(monkeypatch)
    minted = []

    async def fake_remind(args, *, config=None):
        minted.append(args)
        return "⏰ Noted; I'll remind you at 17:00: un compromiso"

    from lhassa.notify import commands as commands_mod

    monkeypatch.setitem(commands_mod.COMMANDS._items, "remind", fake_remind)

    sent = _say("acuérdame de un compromiso a las 5pm").sent
    assert provider.calls == []  # no LLM
    assert minted == ["de un compromiso a las 5pm"]
    assert sent and sent[0].startswith("⏰ Noted")


def test_natural_acuerdame_without_time_falls_through(monkeypatch):
    """A time-less "acuérdame que…" is NOT a timed reminder; it falls through
    (to the remember/fact/brain paths), never mis-minting a task."""
    _patch_brain(monkeypatch, reply="anotado")
    called = []

    async def fake_remind(args, *, config=None):
        called.append(args)
        return "should not run"

    from lhassa.notify import commands as commands_mod

    monkeypatch.setitem(commands_mod.COMMANDS._items, "remind", fake_remind)

    sent = _say("acuérdame que la vida es bella").sent
    assert called == []
    assert sent  # answered by a later path (remember hook or the brain)


# ── /code: parks an approval with Approve/Deny buttons; approve launches it ────


async def _init_approval_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


def test_code_reply_carries_buttons_no_leaked_marker(tmp_path, monkeypatch):
    asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    from lhassa.agents import local_cli

    monkeypatch.setattr(local_cli, "detect_ai_cli", lambda: "claude")

    transport = _say("/code fix the flaky test", _cfg(instance_id="inst_personal"))

    assert len(transport.replies) == 1
    reply = transport.replies[0]
    assert "[[" not in reply.text  # marker never reaches the owner
    assert len(reply.buttons) == 2
    assert reply.buttons[0].data.startswith("approve:")
    assert reply.buttons[1].data.startswith("deny:")
    ap_id = reply.buttons[0].data.split(":", 1)[1]
    assert reply.buttons == callbacks.approval_buttons(ap_id)


def test_approval_buttons_are_label_data_pairs():
    assert callbacks.approval_buttons("ap1") == (
        Button("✅ Approve ap1", "approve:ap1"),
        Button("❌ Deny ap1", "deny:ap1"),
    )


# ── taps: inline Approve/Deny ──────────────────────────────────────────────────


def _tap(data, cfg):
    transport = FakeTransport()
    run_and_drain(transport.tap(data, config=cfg))
    return transport


def test_tap_approve_resolves_and_acks(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval("inst_personal", "task", {"accion": "run_command", "actor_id": "brain"})
    )

    transport = _tap(f"approve:{ap_id}", _cfg(instance_id="inst_personal"))

    assert len(transport.acks) == 1 and "Approved" in transport.acks[0]
    assert any("Approved" in s for s in transport.sent)
    assert asyncio.run(db.load_pending_approvals("inst_personal")) == []


def test_tap_deny_resolves_and_acks(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(db.add_approval("inst_personal", "task", {"accion": "run_command"}))

    transport = _tap(f"deny:{ap_id}", _cfg(instance_id="inst_personal"))

    assert "Denied" in transport.acks[0]
    assert asyncio.run(db.load_pending_approvals("inst_personal")) == []


def test_tap_with_malformed_data_is_acked_and_never_crashes(tmp_path, monkeypatch):
    asyncio.run(_init_approval_db(tmp_path, monkeypatch))

    transport = _tap("not-a-recognised-format", _cfg(instance_id="inst_personal"))

    assert transport.acks == ["Unrecognised action."]  # acked with a note
    assert transport.sent == []  # nothing posted in the chat


def test_tap_on_unknown_approval_says_so(tmp_path, monkeypatch):
    asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    transport = _tap("approve:ap_missing", _cfg(instance_id="inst_personal"))
    assert transport.acks == ["No pending approval ap_missing."]
    assert transport.sent == ["No pending approval ap_missing."]


def test_tap_approve_code_run_launches_and_reports(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "code_run",
            {"accion": "code_run", "task": "fix the flaky test", "cli": "claude", "cwd": "/tmp"},
        )
    )
    from lhassa.agents import code_run, local_cli

    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "make_agent", lambda cli=None: object())

    async def _fake_stream_run(agent, run_id, run, task):
        run.output = "did the fix\nall good"
        run.status = "done"

    monkeypatch.setattr(code_run, "stream_run", _fake_stream_run)

    sent = _tap(f"approve:{ap_id}", _cfg(instance_id="inst_personal")).sent

    assert any("Approved" in s for s in sent)
    assert any(s.startswith("Started: run #") and "claude" in s for s in sent)
    assert any("done" in s and "did the fix" in s for s in sent)
    # The ack goes out before the run's own messages.
    assert sent[0].startswith("Approved")


def test_tap_deny_code_run_does_not_launch(tmp_path, monkeypatch):
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "code_run",
            {"accion": "code_run", "task": "fix the flaky test", "cli": "claude", "cwd": "/tmp"},
        )
    )
    from lhassa.agents import code_run

    launched = []

    async def _fake_stream_run(agent, run_id, run, task):
        launched.append(run_id)

    monkeypatch.setattr(code_run, "stream_run", _fake_stream_run)

    sent = _tap(f"deny:{ap_id}", _cfg(instance_id="inst_personal")).sent

    assert any("Denied" in s for s in sent)
    assert launched == []
    assert asyncio.run(db.load_pending_approvals("inst_personal")) == []


def test_tap_approve_mint_task_runs_now_and_reports(tmp_path, monkeypatch):
    """Approving a parked mint_task (e.g. a protected-root read) fires the same
    early-runner+report the [[action:]] marker uses; the owner's tap is
    followed by the result, not by a wait for the next beat."""
    from lhassa.chat import markers

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

    async def _fake_run_report(instance_id, task_id, name, params, config, send=None):
        ran.append({"task_id": task_id, "name": name, "params": params})

    monkeypatch.setattr(markers, "run_action_and_report", _fake_run_report)

    _tap(f"approve:{ap_id}", _cfg(instance_id="inst_personal"))

    assert ran == [{"task_id": "t_prot", "name": "read_file",
                    "params": {"path": "/vault/nota.md"}}]
    # resolve() released the task; the (patched-out) runner would pick it up.
    pending = asyncio.run(db.load_pending_tasks("inst_personal"))
    assert [t["id"] for t in pending] == ["t_prot"]


# ── deterministic capability shortcuts (the seam a pack plugs into) ─────────


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

    from lhassa.core import episode as episode_mod

    logged = []

    async def _capture_append(self):
        logged.append(self)
        return "ep"

    monkeypatch.setattr(episode_mod.Episode, "append", _capture_append)

    cfg = _cfg()
    sent = _say("probe monday", cfg).sent

    assert provider.calls == []
    assert sent == ["probe says: probe monday"]
    assert len(logged) == 1
    assert logged[0].decision == "probe-query: now"
    assert logged[0].accion == "probe_view"
    assert logged[0].despertar_por == "telegram"
    assert logged[0].costo_usd == 0.0
    topic = state.topic_get(cfg, "123")
    assert topic["capability"] == "probe_view"
    assert topic["context"] == {"day": "monday"}


def test_followup_continues_a_shortcut_topic_without_provider(monkeypatch):
    _probe_shortcut_catalog(monkeypatch)
    provider = _patch_brain(monkeypatch)
    cfg = _cfg()
    transport = FakeTransport()

    _say("probe monday", cfg, transport=transport)
    _say("y later?", cfg, transport=transport)

    assert provider.calls == []
    assert transport.sent[-1] == "probe continues from monday"
    # a falsy refreshed context value never clobbers the sticky one
    assert state.topic_get(cfg, "123")["context"] == {"day": "monday"}


def test_no_registered_capability_falls_through_to_think(monkeypatch):
    """Core alone (an empty catalog): a calendar question reaches the think."""
    from lhassa.notify import capabilities

    monkeypatch.setattr(capabilities, "CAPABILITIES", [])
    provider = _patch_brain(monkeypatch, reply="I have no calendar here.")

    assert _say("¿qué hay en mi calendario el viernes?").sent == ["I have no calendar here."]
    assert len(provider.calls) == 1


def test_followup_with_no_active_topic_falls_through_to_think(monkeypatch):
    """A connector-led message with NO prior sticky topic falls through to the
    normal tool-less think; is_followup alone is never enough."""
    provider = _patch_brain(monkeypatch, reply="Sure, what about it?")

    assert _say("y el sabado?").sent == ["Sure, what about it?"]
    assert len(provider.calls) == 1


# ── the self-routing consciousness (lhassa.notify.capabilities) ─────────────────


def test_unrecognized_message_is_not_tagged(monkeypatch):
    _patch_brain(monkeypatch, reply="Why did the chicken...")
    import lhassa.core.episode as episode_mod

    logged = []

    async def _capture_append(self):
        logged.append(self)
        return "ep"

    monkeypatch.setattr(episode_mod.Episode, "append", _capture_append)

    _say("tell me a joke")

    assert len(logged) == 1
    assert "[routable:" not in logged[0].decision


def test_tap_approve_mint_route_turns_on_the_route(tmp_path, monkeypatch):
    """Approving a mint_route proposal via the buttons applies it; the SAME
    approval->apply pattern as an approved code_run launching."""
    _probe_shortcut_catalog(monkeypatch)
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "mint_route",
            {"accion": "mint_route", "capability": "probe_view", "count": 6},
        )
    )
    from lhassa.notify import capabilities

    cfg = _cfg(instance_id="inst_personal", routes_file=str(tmp_path / "routes.json"))
    sent = _tap(f"approve:{ap_id}", cfg).sent

    assert any("Approved" in s for s in sent)
    assert capabilities.is_routed("probe_view", config=cfg)


def test_tap_deny_mint_route_does_not_turn_on_the_route(tmp_path, monkeypatch):
    _probe_shortcut_catalog(monkeypatch)
    db = asyncio.run(_init_approval_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "mint_route",
            {"accion": "mint_route", "capability": "probe_view", "count": 6},
        )
    )
    from lhassa.notify import capabilities

    cfg = _cfg(instance_id="inst_personal", routes_file=str(tmp_path / "routes.json"))
    sent = _tap(f"deny:{ap_id}", cfg).sent

    assert any("Denied" in s for s in sent)
    assert not capabilities.is_routed("probe_view", config=cfg)


# ── sticky topic ──────────────────────────────────────────────────────────────


def test_topic_set_then_get():
    cfg = _cfg()
    assert state.topic_get(cfg, "123") is None
    state.topic_set(cfg, "123", "calendar_view", {"foo": "bar"})
    topic = state.topic_get(cfg, "123")
    assert topic is not None
    assert topic["capability"] == "calendar_view"
    assert topic["context"] == {"foo": "bar"}


def test_topic_expires_after_ttl(monkeypatch):
    cfg = _cfg(chat_topic_ttl=1.0)
    state.topic_set(cfg, "123", "calendar_view")

    real_time = state.time.time
    monkeypatch.setattr(state.time, "time", lambda: real_time() + 10.0)
    assert state.topic_get(cfg, "123") is None


# ── the episode records what was ASKED ────────────────────────────────────────


def test_the_chat_episode_records_what_was_ASKED(monkeypatch, tmp_path):
    """/recall must find a chat turn by the question, not only the answer.

    The episode stored only the reply: ``accion`` is the constant "responder"
    and the question went nowhere, so episodic recall was blind to the door the
    owner uses most.
    """
    from lhassa.core import db
    from lhassa.core.episode import Episode

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")

    async def _run():
        await db.init_db()
        ep = Episode.begin(despertar_por="telegram", instance_id="inst_x")
        ep.finish(
            decision="think: provider=default model=x"
            + think.asked("cuando vence el deadline de Cohere"),
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
    out = think.asked("please [routable:add_fact] do it")
    assert "[routable:" not in out
    assert "(routable:add_fact)" in out


def test_the_question_suffix_never_disturbs_a_decision_reader():
    """feedback/autonomy/repetition read ``decision`` with startswith."""
    decision = "cache-hit: served past answer" + think.asked("hola")
    assert decision.startswith("cache-hit")
    assert think.asked("") == ""      # nothing to record, nothing added
    assert think.asked("  ") == ""


# ── inbound hooks get a transport-neutral reply and fetch ─────────────────────


def test_text_hook_replies_through_the_transport(monkeypatch):
    from lhassa.notify import inbound_hooks

    provider = _patch_brain(monkeypatch)

    async def _on_text(text, *, config, chat_id="", reply):
        if text.startswith("/probe"):
            await reply(f"hook got {text} in {chat_id}")
            return True
        return False

    monkeypatch.setitem(
        inbound_hooks.INBOUND_HOOKS._items,
        "probe",
        inbound_hooks.InboundHook(name="probe", on_text=_on_text),
    )
    transport = _say("/probe now")
    assert transport.sent == ["hook got /probe now in 123"]
    assert provider.calls == []


def test_document_goes_to_document_hooks_with_fetch(monkeypatch):
    from lhassa.notify import inbound_hooks

    seen = {}

    async def _on_document(document, caption, *, config, chat_id="", reply, fetch):
        seen.update(document=document, caption=caption, data=await fetch())
        await reply("got it")
        return True

    # Only this hook (a pack's, e.g. the cv bridge, would otherwise come first).
    monkeypatch.setattr(inbound_hooks, "INBOUND_HOOKS", inbound_hooks.Registry("inbound hook"))
    inbound_hooks.register_inbound_hook("probe", on_document=_on_document)
    transport = FakeTransport()
    reply = asyncio.run(
        transport.upload(config=_cfg(), content=b"bytes", caption="a caption")
    )
    assert reply is None  # the hook replied itself
    assert seen == {
        "document": {"file_id": "FID", "file_name": "cv.tex"},
        "caption": "a caption",
        "data": b"bytes",
    }
    assert transport.fetched == ["FID"]
    assert transport.sent == ["got it"]


def test_document_with_no_hook_is_skipped(monkeypatch):
    from lhassa.notify import inbound_hooks

    monkeypatch.setattr(inbound_hooks, "INBOUND_HOOKS", inbound_hooks.Registry("inbound hook"))
    transport = FakeTransport()
    assert asyncio.run(transport.upload(config=_cfg())) is None
    assert transport.sent == [] and transport.fetched == []


def test_the_chat_reply_is_scrubbed_of_em_dashes():
    """The rule is enforced on the reply, not just asked for in the prompt.

    The chat instruction already says "never use an em dash" and a model still
    ships one; that is why ``persona.scrub_dashes`` exists and why the notify
    and digest passes run it. The chat door was the last one only asking.
    """
    import inspect

    from lhassa import persona

    assert "\u2014" not in persona.scrub_dashes("listo, jefe \u2014 todo bien")
    source = inspect.getsource(think.brain_reply)
    assert "scrub_dashes" in source, "the chat reply must be scrubbed before it is sent"


def test_the_deprecated_poller_alias_degrades_without_the_telegram_pack(monkeypatch):
    """``python -m lhassa.notify.telegram_inbound`` (one-release alias) exits
    non-zero with an install hint when the telegram pack is absent; importing
    it never imports the pack."""
    import runpy

    from lhassa import actionpack
    from lhassa.extensions import PACKS
    from lhassa.notify import telegram_inbound

    monkeypatch.setattr(actionpack, "register_all", lambda *a, **k: None)
    monkeypatch.setattr(PACKS, "_items", {k: v for k, v in PACKS.items().items() if k != "telegram"})

    def _boom(*a, **k):
        raise AssertionError("must not run the poller without its pack")

    monkeypatch.setattr(runpy, "run_module", _boom)
    assert telegram_inbound.main() == 1
