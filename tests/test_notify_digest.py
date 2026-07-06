"""Tests for the scheduled operative digest (Etapa 2) — fully offline, $0.

``build_digest`` never calls an LLM: every section is monkeypatched to a fake,
deterministic signal source (the monitor's ``gather_signals``, the cost ledger,
the approvals queue, the episode log, tracked agent jobs, the task table) so the
test never touches a real network or a real model.
"""

import asyncio
from types import SimpleNamespace

from lhassa.notify import digest


def _cfg(**over):
    base = dict(instance_id="inst_personal", notify_channels="log")
    base.update(over)
    return SimpleNamespace(**base)


async def _init_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


def _patch_signals(monkeypatch, *, worst="info", ok=True, failing=None, failures=0, flows=0):
    def fake_gather(config, *, instance_id=None):
        return {
            "health": {"worst_level": worst, "ok": ok, "failing": failing or []},
            "failures": [{"id": i} for i in range(failures)],
            "flows": [{"id": f"f{i}"} for i in range(flows)],
            "approvals": [],
        }

    import lhassa.org.monitor as monitor_mod

    monkeypatch.setattr(monitor_mod, "gather_signals", fake_gather)


def test_build_digest_has_all_sections_deterministic(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch, worst="warn", ok=False, failing=["disk"], failures=2, flows=3)

    async def fake_spend(config):
        return 0.42

    import lhassa.notify.commands as commands_mod

    monkeypatch.setattr(commands_mod, "_today_spend", fake_spend)

    text = asyncio.run(digest.build_digest(_cfg()))

    assert "Lhassa —" in text and "check-in" in text
    assert "Health needs attention (worst check: warn)" in text
    assert "failing: disk" in text
    assert "recent failures I'm tracking (2)" in text
    assert "3 work-flow(s)" in text
    assert "I've spent $0.4200 today." in text
    assert "Nothing waiting on you." in text
    assert "wake-ups" in text
    assert "agent jobs" in text.lower()
    assert "Nothing scheduled in the next 24h." in text


def test_build_digest_no_llm_call(tmp_path, monkeypatch):
    """Proves the digest never constructs/calls a model provider."""
    asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch)

    import lhassa.llm.provider as provider_mod

    def _boom(*a, **k):
        raise AssertionError("build_digest must never call an LLM")

    monkeypatch.setattr(provider_mod, "get_model_provider", _boom, raising=False)

    text = asyncio.run(digest.build_digest(_cfg()))
    assert isinstance(text, str) and text


def test_pending_approvals_are_counted(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch)

    asyncio.run(db.add_approval("inst_personal", "task", {"accion": "run_command"}))

    text = asyncio.run(digest.build_digest(_cfg()))
    assert "Waiting on you: 1 approval(s)" in text


def test_due_soon_lists_tasks_within_24h(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch)

    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    far = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    asyncio.run(db.add_task("inst_personal", "send_digest", due_at=soon))
    asyncio.run(db.add_task("inst_personal", "sync_repo", due_at=far))

    text = asyncio.run(digest.build_digest(_cfg()))
    assert "Coming up, your time (1)" in text
    assert "send_digest at " in text


def test_send_digest_delivers_through_notify(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch)

    calls = []

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        calls.append(text)
        return True

    import lhassa.notify.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "notify", fake_notify)

    result = asyncio.run(digest.send_digest({}, config=_cfg()))
    assert result == "send_digest: delivered"
    assert len(calls) == 1
    assert "check-in" in calls[0]


def test_send_digest_persona_revoices_through_the_model(tmp_path, monkeypatch):
    """digest_style='persona' sends the model's re-voiced text; the plain digest is
    the model's input, and the persona block is the system prompt."""
    asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch)

    seen = {}

    class _FakeProvider:
        name = "default"

        async def complete(self, messages, *, model=None, max_tokens=None, system=None):
            seen["input"] = messages[0]["content"]
            seen["system"] = system
            seen["model"] = model
            return SimpleNamespace(text="soy Lhassa y todo está bien ✨", cost_usd=0.0)

    import lhassa.llm.provider as provider_mod
    import lhassa.llm.router as router_mod

    monkeypatch.setattr(provider_mod, "get_model_provider", lambda config, name=None: _FakeProvider())
    monkeypatch.setattr(router_mod, "pick_model", lambda p, *, hard, config: "cheap-model")

    sent = []

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        sent.append(text)
        return True

    import lhassa.notify.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "notify", fake_notify)

    result = asyncio.run(
        digest.send_digest({}, config=_cfg(digest_style="persona", digest_language="es"))
    )
    assert result == "send_digest: delivered"
    assert sent == ["soy Lhassa y todo está bien ✨"]
    assert "check-in" in seen["input"]  # the plain digest fed the model
    assert "es" in seen["system"]  # the language instruction reached the prompt
    assert seen["model"] == "cheap-model"


def test_send_digest_persona_degrades_to_plain_on_model_failure(tmp_path, monkeypatch):
    """A broken model NEVER costs the owner their digest — plain text is sent."""
    asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch)

    import lhassa.llm.provider as provider_mod

    def _boom(config, name=None):
        raise RuntimeError("no model today")

    monkeypatch.setattr(provider_mod, "get_model_provider", _boom)

    sent = []

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        sent.append(text)
        return True

    import lhassa.notify.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "notify", fake_notify)

    result = asyncio.run(digest.send_digest({}, config=_cfg(digest_style="persona")))
    assert result == "send_digest: delivered"
    assert len(sent) == 1 and "check-in" in sent[0]  # the plain digest arrived


def test_send_digest_plain_never_touches_the_model(tmp_path, monkeypatch):
    """The default style stays $0 — constructing a provider is an error."""
    asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch)

    import lhassa.llm.provider as provider_mod

    def _boom(*a, **k):
        raise AssertionError("plain digest must never call an LLM")

    monkeypatch.setattr(provider_mod, "get_model_provider", _boom, raising=False)

    sent = []

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        sent.append(text)
        return True

    import lhassa.notify.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "notify", fake_notify)

    result = asyncio.run(digest.send_digest({}, config=_cfg(digest_style="plain")))
    assert result == "send_digest: delivered"
    assert len(sent) == 1


def test_send_digest_reports_no_channel_delivered(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    _patch_signals(monkeypatch)

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return False

    import lhassa.notify.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "notify", fake_notify)

    result = asyncio.run(digest.send_digest({}, config=_cfg()))
    assert result == "send_digest: no channel delivered"


def test_send_digest_never_raises_on_broken_signals(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))

    import lhassa.org.monitor as monitor_mod

    def boom(config, *, instance_id=None):
        raise RuntimeError("signals exploded")

    monkeypatch.setattr(monitor_mod, "gather_signals", boom)

    # Should degrade, never raise.
    result = asyncio.run(digest.send_digest({}, config=_cfg(notify_channels="")))
    assert isinstance(result, str)
