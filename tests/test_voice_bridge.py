"""
Tests for the voice ↔ brain bridge (``lhassa.voice.brain_bridge``).

These exercise ``answer()`` directly with a mocked provider and a temp brain DB —
NO pipecat, NO audio hardware, NO real model keys. The live audio path (pipecat
FrameProcessor wiring, Deepgram STT, Cartesia TTS, a microphone) can only be
validated with real creds + hardware and is marked [SUPUESTO] in the report.
"""

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.config import Config
from lhassa.llm.provider import Completion, ProviderNotConfigured
from lhassa.voice import brain_bridge


async def _init_db(tmp_path, monkeypatch):
    """Point the grande at a temp file and create the schema."""
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


class _MockProvider:
    """A ModelProvider stand-in: returns a canned Completion (or raises)."""

    name = "mock"
    zero_retention = True
    is_local = True  # behave like the local heartbeat provider

    def __init__(self, *, text="The kettle is on.", raises=None):
        self._text = text
        self._raises = raises
        self.calls: list[dict] = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        if self._raises is not None:
            raise self._raises
        return Completion(text=self._text, model=model or "mock", cost_usd=0.0)


def _config():
    """A Config that routes the routine path to whatever the router resolves."""
    cfg = Config()
    cfg.pseudonymize_remote = False  # don't wrap the mock provider
    return cfg


# ── happy path: a spoken turn is answered through the brain ───────────────────────

@pytest.mark.asyncio
async def test_answer_returns_text_and_logs_voz_episode(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    provider = _MockProvider(text="The kettle is on, friend.")
    # Make the router resolve our mock provider (routine path).
    monkeypatch.setattr(brain_bridge.router, "get_model_provider", lambda *a, **k: provider)

    reply = await brain_bridge.answer("put the kettle on", config=_config())

    assert reply == "The kettle is on, friend."
    # The mock was actually called, with a system prompt carrying the persona.
    assert provider.calls, "provider.complete was not called"
    system = provider.calls[0]["opts"].get("system", "")
    assert "Lhassa" in system  # persona identity is in the system prompt
    assert "voice interface" in system  # spoken-output instruction is layered in

    rows = await db.load_recent_episodes("inst_personal")
    assert len(rows) == 1
    ep = rows[0]
    assert ep["despertar_por"] == "voz"
    assert ep["accion"] == "responder"
    assert ep["resultado"] == "The kettle is on, friend."


# ── degradation: a missing provider must not raise into the pipeline ──────────────

@pytest.mark.asyncio
async def test_answer_degrades_on_provider_not_configured(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    provider = _MockProvider(raises=ProviderNotConfigured("no local model"))
    monkeypatch.setattr(brain_bridge.router, "get_model_provider", lambda *a, **k: provider)

    reply = await brain_bridge.answer("anything", config=_config())

    # Spoken fallback, no exception.
    assert reply == brain_bridge.FALLBACK_REPLY
    # The episode is still logged, marked deferred.
    rows = await db.load_recent_episodes("inst_personal")
    assert len(rows) == 1
    ep = rows[0]
    assert ep["despertar_por"] == "voz"
    assert "deferred" in ep["decision"]
    assert "ProviderNotConfigured" in ep["resultado"]


# ── an unexpected provider error also degrades (never raises) ─────────────────────

@pytest.mark.asyncio
async def test_answer_degrades_on_unexpected_error(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    provider = _MockProvider(raises=RuntimeError("boom"))
    monkeypatch.setattr(brain_bridge.router, "get_model_provider", lambda *a, **k: provider)

    reply = await brain_bridge.answer("anything", config=_config())

    assert reply == brain_bridge.FALLBACK_REPLY
    rows = await db.load_recent_episodes("inst_personal")
    assert len(rows) == 1
    assert rows[0]["despertar_por"] == "voz"
    assert "error" in rows[0]["decision"]


# ── empty / whitespace turn: nothing to say, episode still logged ─────────────────

@pytest.mark.asyncio
async def test_answer_empty_turn_returns_empty(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    # No provider needed — empty turns never reach the model.
    reply = await brain_bridge.answer("   ", config=_config())

    assert reply == ""
    rows = await db.load_recent_episodes("inst_personal")
    assert len(rows) == 1
    assert rows[0]["despertar_por"] == "voz"


# ── the module imports with no pipecat/audio runtime, and exposes the adapter ─────

def test_module_exposes_adapter_without_pipecat():
    # Importing brain_bridge (done at module top) must not require pipecat.
    assert callable(brain_bridge.answer)
    assert callable(brain_bridge.make_brain_processor)
