"""Tests for the mixture-of-agents consultation (Wave D).

Fully offline: every provider is a stub, no subprocess/network ever runs. Covers
the fan-out (honest panel entries on error/missing-binary/ok), the <2-answers
degrade path (no aggregator spend), the aggregation synthesis + cost summing,
the ``/moa`` chat command's footer, and ``propose_tool``'s opt-in
``moa_verdict`` review attaching to the graduation approval payload.
"""

from __future__ import annotations

import json
import shutil

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.llm import moa as moa_mod
from lhassa.llm.moa import MoaResult, moa
from lhassa.llm.provider import Completion
from lhassa.notify import commands

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git not installed")


class FakeConfig:
    def __init__(self, **overrides):
        self.model = "cloud-model"
        self.model_cheap = "cheap-model"
        self.model_smart = "smart-model"
        self.router_mode = "rules"
        self.heartbeat_provider = "cloud"
        self.local_model_url = ""
        self.pseudonymize_remote = False
        self.instance_id = "inst_test"
        # A binary that certainly does not exist; makes "claude" honestly
        # unavailable without touching the real PATH.
        self.claude_bin = "definitely-not-a-real-binary-xyz"
        self.codex_bin = "codex"
        self.opencode_bin = "opencode"
        for k, v in overrides.items():
            setattr(self, k, v)


class StubProvider:
    def __init__(self, name, *, text="an answer", cost=0.001, error=None):
        self.name = name
        self.zero_retention = False
        self.is_local = False
        self._text = text
        self._cost = cost
        self._error = error
        self.calls = 0

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        self.calls += 1
        if self._error:
            raise RuntimeError(self._error)
        return Completion(text=self._text, model=model or "?", cost_usd=self._cost)


# ── fan-out honesty ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fanout_is_honest_and_never_raises(monkeypatch):
    """claude: missing binary: codex: raises; default: ok; <2 successes -> no aggregator."""
    codex = StubProvider("codex", error="codex CLI exploded")
    default = StubProvider("default", text="the real answer", cost=0.002)

    def fake_get_model_provider(config, *, name=None):
        if name == "codex":
            return codex
        if name == "default":
            return default
        raise AssertionError(f"unexpected provider requested: {name}")

    agg_calls = {"n": 0}

    def fake_pick_provider(*, sensitive, config, hard=False):
        agg_calls["n"] += 1
        raise AssertionError("aggregator should not be reached with <2 answers")

    # Which CLI binaries exist is a property of the HOST, so it is pinned by
    # injection: this test needs `claude` absent and `codex` present. Leaving it
    # to shutil.which made the outcome depend on the dev machine; macOS CI,
    # where neither CLI is installed, caught it.
    monkeypatch.setattr(
        moa_mod, "_binary_missing", lambda name, config: name == "claude"
    )
    monkeypatch.setattr(moa_mod, "get_model_provider", fake_get_model_provider)
    monkeypatch.setattr(moa_mod._router, "pick_provider", fake_pick_provider)

    result = await moa(
        [{"role": "user", "content": "hello"}],
        providers=["claude", "codex", "default"],
        config=FakeConfig(),
    )

    assert isinstance(result, MoaResult)
    assert agg_calls["n"] == 0  # no aggregation spend

    by_name = {e["provider"]: e for e in result.panel}
    assert by_name["claude"]["ok"] is False
    assert by_name["claude"]["error"] == "unavailable"
    assert by_name["codex"]["ok"] is False
    assert "exploded" in by_name["codex"]["error"]
    assert by_name["default"]["ok"] is True
    assert by_name["default"]["text"] == "the real answer"

    # Degrade to the single successful answer.
    assert result.text == "the real answer"
    assert result.aggregator_provider is None


@pytest.mark.asyncio
async def test_zero_answers_degrades_honestly(monkeypatch):
    codex = StubProvider("codex", error="down")

    def fake_get_model_provider(config, *, name=None):
        return codex

    # Which CLI binaries exist is a property of the HOST, so it is pinned by
    # injection: this test needs `claude` absent and `codex` present. Leaving it
    # to shutil.which made the outcome depend on the dev machine; macOS CI,
    # where neither CLI is installed, caught it.
    monkeypatch.setattr(
        moa_mod, "_binary_missing", lambda name, config: name == "claude"
    )
    monkeypatch.setattr(moa_mod, "get_model_provider", fake_get_model_provider)

    result = await moa(
        [{"role": "user", "content": "hi"}],
        providers=["codex"],
        config=FakeConfig(),
    )
    assert result.panel[0]["ok"] is False
    assert "unavailable" in result.text or "provider" in result.text.lower()


# ── aggregation ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregation_synthesizes_and_sums_cost(monkeypatch):
    codex = StubProvider("codex", text="codex says X", cost=0.001)
    default = StubProvider("default", text="default says Y", cost=0.0005)
    aggregator = StubProvider("cloud-agg", text="synthesized verdict", cost=0.0002)

    def fake_get_model_provider(config, *, name=None):
        return {"codex": codex, "default": default}[name]

    def fake_pick_provider(*, sensitive, config, hard=False):
        return aggregator

    # Which CLI binaries exist is a property of the HOST, so it is pinned by
    # injection: this test needs `claude` absent and `codex` present. Leaving it
    # to shutil.which made the outcome depend on the dev machine; macOS CI,
    # where neither CLI is installed, caught it.
    monkeypatch.setattr(
        moa_mod, "_binary_missing", lambda name, config: name == "claude"
    )
    monkeypatch.setattr(moa_mod, "get_model_provider", fake_get_model_provider)
    monkeypatch.setattr(moa_mod._router, "pick_provider", fake_pick_provider)

    result = await moa(
        [{"role": "user", "content": "should I do X or Y?"}],
        providers=["codex", "default"],
        config=FakeConfig(),
    )

    assert aggregator.calls == 1
    assert result.text == "synthesized verdict"
    assert result.aggregator_provider == "cloud-agg"
    assert result.total_cost_usd == pytest.approx(0.001 + 0.0005 + 0.0002)


@pytest.mark.asyncio
async def test_aggregator_failure_degrades_to_first_panel_answer(monkeypatch):
    codex = StubProvider("codex", text="codex says X", cost=0.001)
    default = StubProvider("default", text="default says Y", cost=0.0005)
    aggregator = StubProvider("cloud-agg", error="aggregator blew up")

    def fake_get_model_provider(config, *, name=None):
        return {"codex": codex, "default": default}[name]

    def fake_pick_provider(*, sensitive, config, hard=False):
        return aggregator

    # Which CLI binaries exist is a property of the HOST, so it is pinned by
    # injection: this test needs `claude` absent and `codex` present. Leaving it
    # to shutil.which made the outcome depend on the dev machine; macOS CI,
    # where neither CLI is installed, caught it.
    monkeypatch.setattr(
        moa_mod, "_binary_missing", lambda name, config: name == "claude"
    )
    monkeypatch.setattr(moa_mod, "get_model_provider", fake_get_model_provider)
    monkeypatch.setattr(moa_mod._router, "pick_provider", fake_pick_provider)

    result = await moa(
        [{"role": "user", "content": "should I do X or Y?"}],
        providers=["codex", "default"],
        config=FakeConfig(),
    )

    assert result.text == "codex says X"  # first successful panelist
    assert result.aggregator_provider is None
    assert result.total_cost_usd == pytest.approx(0.001 + 0.0005)


@pytest.mark.asyncio
async def test_no_providers_configured_degrades():
    cfg = FakeConfig(moa_providers=" , , ")
    result = await moa([{"role": "user", "content": "hi"}], config=cfg)
    assert result.panel == []
    assert result.total_cost_usd == 0.0


# ── /moa command ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_moa_command_requires_question():
    reply = await commands.dispatch("/moa", config=FakeConfig())
    assert "Usage: /moa" in reply


@pytest.mark.asyncio
async def test_moa_command_reports_synthesized_answer_and_footer(monkeypatch):
    async def fake_moa(messages, *, providers=None, config=None, system=None, max_tokens=800):
        return MoaResult(
            text="synthesized answer",
            panel=[
                {"provider": "claude", "ok": True, "text": "a", "cost_usd": 0.0003},
                {"provider": "codex", "ok": False, "error": "unavailable", "cost_usd": 0.0},
                {"provider": "default", "ok": True, "text": "c", "cost_usd": 0.0004},
            ],
            aggregator_provider="default",
            total_cost_usd=0.0007,
        )

    monkeypatch.setattr("lhassa.llm.moa.moa", fake_moa)

    reply = await commands.dispatch("/moa should I refactor this?", config=FakeConfig())
    assert "synthesized answer" in reply
    assert "panel:" in reply
    assert "claude ✓" in reply
    assert "codex ✗ (unavailable)" in reply
    assert "deepseek ✓" in reply  # "default" is labeled "deepseek" in the footer
    assert "$0.0007" in reply


@pytest.mark.asyncio
async def test_moa_command_never_raises_on_failure(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr("lhassa.llm.moa.moa", boom)
    reply = await commands.dispatch("/moa anything", config=FakeConfig())
    assert isinstance(reply, str)
    assert reply  # a friendly degrade, never a raised exception


# ── propose_tool's opt-in moa_verdict review ────────────────────────────────


def _passing_payload(name: str) -> str:
    tool_py = "def double(n):\n    return n * 2\n"
    test_py = (
        f"from {name} import double\n\n\ndef test_double():\n    assert double(3) == 6\n"
    )
    return json.dumps({"files": {f"{name}.py": tool_py, f"test_{name}.py": test_py}})


class ScriptedProvider:
    def __init__(self, replies: list):
        self.name = "fake"
        self.zero_retention = False
        self.is_local = False
        self._replies = list(replies)
        self._last = replies[-1] if replies else ""
        self.calls: list = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        self.calls.append(messages)
        text = self._replies.pop(0) if self._replies else self._last
        return Completion(text=text, model=model or "?", cost_usd=0.0)


class ForgeConfig:
    def __init__(self, *, instance_id="inst_test"):
        self.model = "cheap-model"
        self.model_cheap = "cheap-model"
        self.model_smart = "smart-model"
        self.router_mode = "rules"
        self.instance_id = instance_id


@pytest_asyncio.fixture
async def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.config import Config

    repo = tmp_path / "tools"
    monkeypatch.setattr(Config, "tools_repo_dir", str(repo))
    monkeypatch.setattr(Config, "github_token", "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


@needs_git
@pytest.mark.asyncio
async def test_moa_verdict_false_leaves_approval_unchanged(brain, monkeypatch):
    from lhassa.agents import author as author_mod

    calls = {"n": 0}

    async def fake_moa_consult(*a, **k):
        calls["n"] += 1
        return MoaResult(text="should not be called", panel=[], total_cost_usd=0.0)

    monkeypatch.setattr(author_mod, "_moa_consult", fake_moa_consult)

    provider = ScriptedProvider([_passing_payload("regtool")])
    outcome = await author_mod.propose_tool(
        "regtool",
        "regression lock tool",
        provider=provider,
        config=ForgeConfig(),
        instance_id="inst_test",
        max_turns=1,
        baseline=100.0,
        metric_fn=lambda *_: 1.0,
        # moa_verdict defaults to False
    )

    assert outcome["graduated"] is True
    assert calls["n"] == 0  # moa never consulted; byte-identical behavior

    row = await brain.get_approval("inst_test", outcome["approval_id"])
    assert "moa_review" not in (row.get("payload") or {})


@needs_git
@pytest.mark.asyncio
async def test_moa_verdict_true_attaches_review_to_approval(brain, monkeypatch):
    from lhassa.agents import author as author_mod

    async def fake_moa_consult(messages, *, config=None):
        return MoaResult(
            text="Looks safe; both panelists agree.",
            panel=[
                {"provider": "claude", "ok": True, "text": "safe", "cost_usd": 0.001},
                {"provider": "codex", "ok": True, "text": "safe", "cost_usd": 0.001},
            ],
            aggregator_provider="default",
            total_cost_usd=0.0025,
        )

    monkeypatch.setattr(author_mod, "_moa_consult", fake_moa_consult)

    provider = ScriptedProvider([_passing_payload("reviewedtool")])
    outcome = await author_mod.propose_tool(
        "reviewedtool",
        "a tool that gets a panel review",
        provider=provider,
        config=ForgeConfig(),
        instance_id="inst_test",
        max_turns=1,
        baseline=100.0,
        metric_fn=lambda *_: 1.0,
        moa_verdict=True,
    )

    assert outcome["graduated"] is True
    row = await brain.get_approval("inst_test", outcome["approval_id"])
    review = (row.get("payload") or {}).get("moa_review")
    assert review is not None
    assert review["text"] == "Looks safe; both panelists agree."
    assert review["total_cost_usd"] == pytest.approx(0.0025)


@needs_git
@pytest.mark.asyncio
async def test_moa_verdict_true_degrades_without_blocking_graduation(brain, monkeypatch):
    """A failing MoA review is advisory-only; it must never block graduation."""
    from lhassa.agents import author as author_mod

    async def broken_moa_consult(*a, **k):
        raise RuntimeError("panel unreachable")

    monkeypatch.setattr(author_mod, "_moa_consult", broken_moa_consult)

    provider = ScriptedProvider([_passing_payload("stilltool")])
    outcome = await author_mod.propose_tool(
        "stilltool",
        "graduates even if the panel review fails",
        provider=provider,
        config=ForgeConfig(),
        instance_id="inst_test",
        max_turns=1,
        baseline=100.0,
        metric_fn=lambda *_: 1.0,
        moa_verdict=True,
    )

    assert outcome["graduated"] is True
    row = await brain.get_approval("inst_test", outcome["approval_id"])
    assert "moa_review" not in (row.get("payload") or {})
