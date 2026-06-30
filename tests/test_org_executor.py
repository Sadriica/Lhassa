"""Tests for lhassa.org.executor — the real department executor (Fase 7).

The department now actually THINKS: one-turn brain path (identity + layered memory +
routine provider + provider.complete) with the department's identity in the system
prompt. Kept offline-testable: the live model path is gated behind LHASSA_ORG_LIVE
(default OFF) and the provider/router are monkeypatched so NO real LLM call happens.
"""

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.llm.provider import Completion, ProviderNotConfigured


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


class _FakeProvider:
    """A stand-in ModelProvider that records the system prompt and never hits a model."""

    name = "fake"
    zero_retention = True
    is_local = False

    def __init__(self, *, raises=None, text="optimized: cache the hot path"):
        self.raises = raises
        self.text = text
        self.seen_system = None
        self.calls = 0

    async def complete(self, messages, *, model=None, max_tokens=1024, system=None, **opts):
        self.calls += 1
        self.seen_system = system
        if self.raises is not None:
            raise self.raises
        return Completion(text=self.text, model=model or "fake-model", cost_usd=0.0021)


def _patch_router(monkeypatch, provider, *, model="fake-model"):
    """Make router.decide (as imported by the executor) return ``provider``."""
    from lhassa.llm.router import Decision
    import lhassa.org.executor as ex

    def fake_decide(trigger, ctx=None, *, config, **kw):
        return Decision(
            kind="think", provider=provider, model=model, reason="test: routine"
        )

    monkeypatch.setattr(ex.router, "decide", fake_decide)


# ── live think path: structured result + department identity in the prompt ────────

@pytest.mark.asyncio
async def test_brain_executor_thinks_with_department_identity(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    monkeypatch.setenv("LHASSA_ORG_LIVE", "1")  # turn on the live path
    from lhassa.org.executor import brain_executor

    provider = _FakeProvider(text="optimized: cache the hot path")
    _patch_router(monkeypatch, provider)

    result = await brain_executor(
        "make the heartbeat faster", department="optimizacion", instance_id="inst_personal"
    )

    # structured result
    assert result["department"] == "optimizacion"
    assert result["summary"] == "optimized: cache the hot path"
    assert result["model"] == "fake-model"
    assert result["cost_usd"] == pytest.approx(0.0021)
    assert result["ok"] is True

    # the DEPARTMENT'S identity is reflected in the system prompt
    assert provider.seen_system is not None
    assert "optimizacion department brain" in provider.seen_system

    # an episode (despertar_por="org") was logged carrying the answer
    eps = await db.load_recent_episodes("inst_personal")
    org_eps = [e for e in eps if e["despertar_por"] == "org"]
    assert org_eps
    assert any("optimized: cache the hot path" in (e.get("resultado") or "") for e in org_eps)


# ── graceful degradation: ProviderNotConfigured never raises ──────────────────────

@pytest.mark.asyncio
async def test_brain_executor_degrades_on_provider_not_configured(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    monkeypatch.setenv("LHASSA_ORG_LIVE", "1")
    from lhassa.org.executor import brain_executor

    provider = _FakeProvider(raises=ProviderNotConfigured("no creds"))
    _patch_router(monkeypatch, provider)

    # must NOT raise — a failure becomes a structured result
    result = await brain_executor("anything", department="finanzas")
    assert result["ok"] is True
    assert result["department"] == "finanzas"
    assert result["cost_usd"] == 0.0
    assert "ProviderNotConfigured" in result["summary"]


@pytest.mark.asyncio
async def test_brain_executor_degrades_on_no_provider(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    monkeypatch.setenv("LHASSA_ORG_LIVE", "1")
    from lhassa.org.executor import brain_executor

    _patch_router(monkeypatch, None)  # router resolves no provider
    result = await brain_executor("x", department="documentacion")
    assert result["ok"] is True
    assert result["cost_usd"] == 0.0


# ── offline by default: no model call, deterministic structured result ────────────

@pytest.mark.asyncio
async def test_brain_executor_offline_by_default_makes_no_call(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)  # default OFF
    from lhassa.org.executor import brain_executor

    provider = _FakeProvider()
    _patch_router(monkeypatch, provider)

    result = await brain_executor("optimize stuff", department="optimizacion")
    assert provider.calls == 0  # no model was called
    assert result["ok"] is True
    assert result["model"] == "offline"
    assert result["cost_usd"] == 0.0
    assert "offline" in result["summary"].lower()


# ── DepartmentBrain.handle() with NO executor uses brain_executor ─────────────────

@pytest.mark.asyncio
async def test_handle_default_executor_is_brain_executor(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    monkeypatch.setenv("LHASSA_ORG_LIVE", "1")
    from lhassa.org.coordinator import GeneralBrain, DepartmentBrain
    from lhassa.org import messaging

    provider = _FakeProvider(text="department thought about it")
    _patch_router(monkeypatch, provider)

    general = GeneralBrain(instance_id="inst_personal")
    dept = DepartmentBrain(department="optimizacion", instance_id="inst_personal")

    await general.assign("speed it up", to_department="optimizacion", by_actor="owner")
    inbox = await messaging.inbox("dept:optimizacion", instance_id="inst_personal")

    # no executor injected → defaults to brain_executor → the mocked provider runs
    result = await dept.handle(inbox[0])
    assert provider.calls == 1
    assert result.payload["result"]["summary"] == "department thought about it"
    # the department's identity reached the prompt
    assert "optimizacion department brain" in provider.seen_system
