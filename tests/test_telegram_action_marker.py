"""The Telegram [[action: …]] marker; chat-requested actions, allowlist-gated.

Mirrors test_research_bridge: the chat think stays tool-less; the marker is the
signal, the chat engine mints the signed task (same HUMANO/telegram origin as /goal),
and the ack is only ever written after the mint succeeded. All offline.
"""

import asyncio
from types import SimpleNamespace

from lhassa.chat import markers


def _cfg(**over):
    base = dict(instance_id="inst_personal")
    base.update(over)
    return SimpleNamespace(**base)


def _patch_mint(monkeypatch, *, estado="pendiente", raise_exc=None):
    calls = []

    async def fake_mint(instance_id, accion, objetivo="", params=None, *, origin, **kw):
        if raise_exc is not None:
            raise raise_exc
        calls.append(
            {"instance_id": instance_id, "accion": accion, "params": params, "origin": origin}
        )
        return ("task_1", estado)

    async def fake_run_now(*a, **k):
        return True

    import lhassa.core.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "mint_task", fake_mint)
    monkeypatch.setattr(dispatch_mod, "run_task_now", fake_run_now)
    return calls


def _apply(reply, monkeypatch=None, **cfg):
    return asyncio.run(
        markers.apply_action_marker(
            reply, instance_id="inst_personal", config=_cfg(**cfg)
        )
    )


def test_marker_mints_a_registered_action_and_is_stripped(monkeypatch):
    calls = _patch_mint(monkeypatch)
    reply = _apply("Claro, lo reviso.\n[[action: check_spend]]", monkeypatch)
    assert len(calls) == 1
    assert calls[0]["accion"] == "check_spend"
    assert calls[0]["params"] == {}
    assert "[[" not in reply
    assert "Claro, lo reviso." in reply
    assert "check_spend" in reply  # the run ack names the action


def test_marker_params_are_parsed_key_value(monkeypatch):
    calls = _patch_mint(monkeypatch)
    _apply('x\n[[action: sync_repo repo=lhassa mode="fast pull"]]', monkeypatch)
    assert calls[0]["params"] == {"repo": "lhassa", "mode": "fast pull"}


def test_unknown_action_is_refused_honestly_without_a_mint(monkeypatch):
    calls = _patch_mint(monkeypatch)
    reply = _apply("hm\n[[action: rm_everything now=1]]", monkeypatch)
    assert calls == []
    assert "rm_everything" in reply
    assert "[[" not in reply


def test_approval_parked_action_gets_the_parked_ack(monkeypatch):
    calls = _patch_mint(monkeypatch, estado="esperando_aprobacion")
    reply = _apply("ok\n[[action: run_script]]", monkeypatch)
    assert len(calls) == 1
    assert "/approvals" in reply


def test_mint_failure_degrades_without_a_false_promise(monkeypatch):
    _patch_mint(monkeypatch, raise_exc=RuntimeError("db down"))
    reply = _apply("hola\n[[action: check_spend]]", monkeypatch)
    assert "hola" in reply
    assert "⚙️" not in reply and "shortly" not in reply


def test_no_marker_passes_through_untouched(monkeypatch):
    calls = _patch_mint(monkeypatch)
    assert _apply("solo charla", monkeypatch) == "solo charla"
    assert calls == []
