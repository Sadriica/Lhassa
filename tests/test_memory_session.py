"""Tests for potts.memory.session."""

import pytest


@pytest.mark.asyncio
async def test_extract_session_payload_falls_back_to_groq(monkeypatch):
    import potts.memory.session as session

    calls = []

    class Config:
        model = "primary-model"
        groq_api_key = "gsk_test"

    async def fake_extract_openai_compat(client, model, prompt):
        calls.append(("primary", model))
        raise RuntimeError("response_format unsupported")

    async def fake_extract_with_groq_fallback(prompt, api_key):
        calls.append(("fallback", api_key))
        return '{"facts":[],"summary":"ok"}'

    monkeypatch.setattr(session, "_extract_openai_compat", fake_extract_openai_compat)
    monkeypatch.setattr(session, "_extract_with_groq_fallback", fake_extract_with_groq_fallback)

    raw = await session._extract_session_payload(
        Config(),
        client=object(),
        provider="openrouter",
        prompt="conversation",
    )

    assert raw == '{"facts":[],"summary":"ok"}'
    assert calls == [("primary", "primary-model"), ("fallback", "gsk_test")]


@pytest.mark.asyncio
async def test_extract_session_payload_does_not_fallback_without_groq_key(monkeypatch):
    import potts.memory.session as session

    class Config:
        model = "primary-model"
        groq_api_key = ""

    async def fake_extract_openai_compat(client, model, prompt):
        raise RuntimeError("response_format unsupported")

    monkeypatch.setattr(session, "_extract_openai_compat", fake_extract_openai_compat)

    with pytest.raises(RuntimeError, match="response_format unsupported"):
        await session._extract_session_payload(
            Config(),
            client=object(),
            provider="openrouter",
            prompt="conversation",
        )
