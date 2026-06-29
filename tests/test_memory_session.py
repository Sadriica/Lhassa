"""Tests for potts.memory.session."""

import pytest

from potts.llm.gateway import BudgetExceeded, CompletionResult


def _result(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        provider="openai",
        model="test-model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        cache_hit=False,
    )


@pytest.mark.asyncio
async def test_extract_session_payload_routes_through_gateway(monkeypatch):
    import potts.memory.session as session

    calls = {}

    async def fake_complete(messages, **kwargs):
        calls["messages"] = messages
        calls["kwargs"] = kwargs
        return _result('{"facts":[],"summary":"ok"}')

    monkeypatch.setattr(session.gateway, "complete", fake_complete)

    raw = await session._extract_session_payload("conversation")

    assert raw == '{"facts":[],"summary":"ok"}'
    assert calls["messages"] == [{"role": "user", "content": "conversation"}]
    assert calls["kwargs"]["system"] == session.EXTRACT_SYSTEM
    assert calls["kwargs"]["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_extract_session_payload_strips_markdown_fence(monkeypatch):
    import potts.memory.session as session

    async def fake_complete(messages, **kwargs):
        return _result('```json\n{"facts":[],"summary":"ok"}\n```')

    monkeypatch.setattr(session.gateway, "complete", fake_complete)

    raw = await session._extract_session_payload("conversation")
    assert raw == '{"facts":[],"summary":"ok"}'


@pytest.mark.asyncio
async def test_close_session_persists_facts_and_summary(monkeypatch):
    import potts.memory.session as session

    saved_facts = []
    saved_summaries = []

    async def fake_complete(messages, **kwargs):
        return _result('{"facts":[{"key":"name","value":"Sam"}],"summary":"chat"}')

    async def fake_upsert(key, value):
        saved_facts.append((key, value))

    async def fake_save_session(summary):
        saved_summaries.append(summary)

    monkeypatch.setattr(session.gateway, "complete", fake_complete)
    monkeypatch.setattr(session, "upsert_fact", fake_upsert)
    monkeypatch.setattr(session, "save_session", fake_save_session)

    await session.close_session(
        [
            {"role": "user", "content": "hi, I'm Sam"},
            {"role": "assistant", "content": "hello Sam"},
        ]
    )

    assert saved_facts == [("name", "Sam")]
    assert saved_summaries == ["chat"]


@pytest.mark.asyncio
async def test_close_session_degrades_on_budget_exceeded(monkeypatch):
    import potts.memory.session as session

    async def fake_complete(messages, **kwargs):
        raise BudgetExceeded("daily budget reached")

    def fail(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("should not persist when budget is exceeded")

    monkeypatch.setattr(session.gateway, "complete", fake_complete)
    monkeypatch.setattr(session, "upsert_fact", fail)
    monkeypatch.setattr(session, "save_session", fail)

    # Must not raise — shutdown should never crash on a budget cap.
    await session.close_session(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
