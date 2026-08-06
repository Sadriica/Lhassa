"""Tests for the notify voice layer (:mod:`lhassa.notify.voice`) and its wiring
into :func:`lhassa.notify.dispatch.notify`.

Offline throughout: ``persona`` style is exercised against a stub provider
(never a real model call), and every degrade path is asserted to still
deliver — a notification must ALWAYS arrive.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.notify import dispatch, voice


# ── classify ────────────────────────────────────────────────────────────────


def test_classify_explicit_kind_wins():
    assert voice.classify("anything at all", kind="approval") == "approval"
    assert voice.classify("⚙️ check_spend: check_spend: x", kind="reminder") == "reminder"


def test_classify_sniffs_action_result():
    assert voice.classify("⚙️ check_spend: check_spend: 2040 tokens") == "action-result"


def test_classify_sniffs_reminder():
    assert voice.classify("Don't forget the reminder about the dentist") == "reminder"
    assert voice.classify("recuérdame llamar a mamá") == "reminder"


def test_classify_sniffs_approval():
    assert voice.classify("A new approval is waiting, see /approvals") == "approval"


def test_classify_sniffs_alert_from_watcher_shapes():
    assert voice.classify("Heads up — monitor · interns: 1 new failure(s)") == "alert"
    assert voice.classify("Something needs a look — monitor · interns: alert") == "alert"


def test_classify_falls_back_to_generic():
    assert voice.classify("hello there") == "generic"
    assert voice.classify("") == "generic"


# ── template ────────────────────────────────────────────────────────────────


def test_template_action_result_dedupes_doubled_name():
    text = "⚙️ check_spend: check_spend: 2040 output tokens today, cost $0.0053"
    out = voice.template(text, "action-result")
    # check_spend has a phrasebook lead ("I checked spend") — no mechanical
    # "Check-spend:" title-casing for a known action.
    assert out.startswith("Done — I checked spend:")
    assert "2040 output tokens today" in out
    assert "check_spend: check_spend" not in out


def test_template_action_result_falls_back_to_title_case_for_unknown_action():
    text = "⚙️ totally_unknown_action: totally_unknown_action: did a thing"
    out = voice.template(text, "action-result")
    assert out == "Done — Totally-unknown-action: did a thing"


def test_template_naturalizes_key_value_tail():
    out = voice.template("status=ok reason=timeout", "generic")
    assert "status: ok" in out
    assert "reason: timeout" in out


def test_template_reminder_adds_framing():
    out = voice.template("Buy milk", "reminder")
    assert out == "You asked me to remind you: Buy milk"


def test_template_approval_adds_framing():
    out = voice.template("2 pending approvals", "approval")
    assert out.startswith("Waiting on your word: ")
    assert "2 pending approvals" in out


def test_template_alert_adds_framing_when_not_already_voiced():
    out = voice.template("a new failure was seen", "alert")
    # "Heads up — " is the ONE lead phrase — sourced from persona.LEAD_PHRASES,
    # the same table persona.format_alert draws from (no more duplicated,
    # drifted copies of "heads up").
    assert out == "Heads up — a new failure was seen"


def test_template_alert_is_a_noop_on_already_voiced_watcher_text():
    # persona.format_alert already leads with "Heads up — " / "Something needs
    # a look — "; template() must not double-frame it.
    already = "Heads up — monitor · interns: 1 new failure(s)"
    assert voice.template(already, "alert") == already

    already2 = "Something needs a look — monitor · interns: health worsened"
    assert voice.template(already2, "alert") == already2


def test_template_report_is_pass_through_verbatim():
    text = "🧭 Lhassa — morning check-in. I kept the wheel turning at $0."
    assert voice.template(text, "report") == text


def test_template_verbatim_when_unsure_generic():
    # No known shape, no key=value tail — passed straight through.
    text = "the quick brown fox"
    assert voice.template(text, "generic") == text


def test_template_never_drops_information():
    text = "⚙️ weird_shape without a colon anywhere"
    out = voice.template(text, "action-result")
    assert "weird_shape without a colon anywhere" in out


def test_template_idempotent():
    once = voice.template("Buy milk", "reminder")
    twice = voice.template(once, "reminder")
    assert once == twice

    once_a = voice.template("⚙️ check_spend: check_spend: 2040 tokens", "action-result")
    twice_a = voice.template(once_a, "action-result")
    assert once_a == twice_a


def test_template_empty_text_is_safe():
    assert voice.template("", "alert") == ""
    assert voice.template(None, "alert") is None


# ── revoice ─────────────────────────────────────────────────────────────────


def _cfg(**kw):
    base = dict(instance_id="inst_personal", notify_style="template")
    base.update(kw)
    return SimpleNamespace(**base)


def test_revoice_plain_style_is_unchanged():
    text = "⚙️ check_spend: check_spend: 2040 tokens"
    out = asyncio.run(voice.revoice(text, kind="action-result", config=_cfg(notify_style="plain")))
    assert out == text


def test_revoice_template_style_is_deterministic():
    text = "Buy milk"
    out = asyncio.run(voice.revoice(text, kind="reminder", config=_cfg(notify_style="template")))
    assert out == "You asked me to remind you: Buy milk"


def test_revoice_default_style_is_template(monkeypatch):
    # No notify_style attribute at all -> defaults to "template".
    cfg = SimpleNamespace(instance_id="inst_personal")
    out = asyncio.run(voice.revoice("Buy milk", kind="reminder", config=cfg))
    assert out == "You asked me to remind you: Buy milk"


class _FakeProvider:
    name = "default"

    def __init__(self, text="soy Lhassa y todo bien", cost_usd=0.0):
        self._text = text
        self._cost = cost_usd
        self.seen = None

    async def complete(self, messages, *, model=None, max_tokens=None, system=None):
        self.seen = {"input": messages[0]["content"], "system": system, "model": model}
        return SimpleNamespace(text=self._text, cost_usd=self._cost)


def test_revoice_persona_style_rewrites_through_the_model(monkeypatch):
    import lhassa.llm.provider as provider_mod
    import lhassa.llm.router as router_mod

    fake = _FakeProvider(text="Buenas, ya lo tengo anotado.")
    monkeypatch.setattr(provider_mod, "get_model_provider", lambda config, name=None: fake)
    monkeypatch.setattr(router_mod, "pick_model", lambda p, *, hard, config: "cheap-model")

    out = asyncio.run(
        voice.revoice("Buy milk", kind="reminder", config=_cfg(notify_style="persona"))
    )
    assert out == "Buenas, ya lo tengo anotado."
    assert fake.seen is not None
    assert "You asked me to remind you: Buy milk" in fake.seen["input"]
    assert "narrative.md" in fake.seen["system"] or "first person" in fake.seen["system"]


def test_revoice_persona_style_degrades_to_template_on_failure(monkeypatch):
    import lhassa.llm.provider as provider_mod

    def _boom(config, name=None):
        raise RuntimeError("no model today")

    monkeypatch.setattr(provider_mod, "get_model_provider", _boom)

    out = asyncio.run(
        voice.revoice("Buy milk", kind="reminder", config=_cfg(notify_style="persona"))
    )
    # Degrades to the template() result — the message still arrives.
    assert out == "You asked me to remind you: Buy milk"


def test_revoice_persona_style_degrades_when_completion_raises(monkeypatch):
    import lhassa.llm.provider as provider_mod
    import lhassa.llm.router as router_mod

    class _RaisingProvider:
        name = "default"

        async def complete(self, *a, **k):
            raise RuntimeError("provider unreachable")

    monkeypatch.setattr(provider_mod, "get_model_provider", lambda config, name=None: _RaisingProvider())
    monkeypatch.setattr(router_mod, "pick_model", lambda p, *, hard, config: "cheap-model")

    out = asyncio.run(
        voice.revoice("Buy milk", kind="reminder", config=_cfg(notify_style="persona"))
    )
    assert out == "You asked me to remind you: Buy milk"


def test_revoice_report_kind_is_pass_through_even_in_template_style():
    digest_text = "🧭 Lhassa — morning check-in. I kept the wheel turning at $0."
    out = asyncio.run(voice.revoice(digest_text, kind="report", config=_cfg()))
    assert out == digest_text


# ── dispatch.notify integration ─────────────────────────────────────────────


class _Recorder:
    name = "rec"

    def __init__(self):
        self.sent = []

    def is_configured(self, config):
        return True

    async def send(self, text, *, level="info", config=None, **kwargs):
        self.sent.append(text)
        return True


@pytest.fixture
def recording_channel():
    """Registers a recording channel named ``rec`` for the duration of a test."""
    from lhassa.notify import channels as ch

    recorder = _Recorder()
    ch.register_channel(recorder.name, recorder, override=True)
    yield recorder
    if recorder.name in ch.CHANNELS:
        ch.CHANNELS.unregister(recorder.name)


def test_dispatch_notify_threads_kind_through_voice(recording_channel):
    recorder = recording_channel
    cfg = _cfg(notify_channels="rec")

    delivered = asyncio.run(dispatch.notify("Buy milk", kind="reminder", config=cfg))
    assert delivered is True
    assert recorder.sent == ["You asked me to remind you: Buy milk"]


def test_dispatch_notify_survives_a_raising_voice_layer(recording_channel, monkeypatch):
    recorder = recording_channel
    cfg = _cfg(notify_channels="rec")

    async def _boom(text, *, kind=None, config=None):
        raise RuntimeError("voice layer exploded")

    monkeypatch.setattr(voice, "revoice", _boom)

    delivered = asyncio.run(dispatch.notify("Buy milk", kind="reminder", config=cfg))
    assert delivered is True
    # The original text still fans out — voicing must never cost a notification.
    assert recorder.sent == ["Buy milk"]


def test_dispatch_notify_report_kind_passes_digest_text_verbatim(recording_channel):
    recorder = recording_channel
    cfg = _cfg(notify_channels="rec")

    digest_text = "🧭 Lhassa — morning check-in. I kept the wheel turning at $0."
    delivered = asyncio.run(dispatch.notify(digest_text, kind="report", config=cfg))
    assert delivered is True
    assert recorder.sent == [digest_text]
