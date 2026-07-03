"""Tests for lhassa.agents.author — the missing first mile of self-improvement 2b.

Fully offline: a fake CHEAP provider stands in for the routine model, a real
local git repo (tmp dir) backs versioning, quarantine runs for real but only
over harmless authored shell/py content, and the brain DB is redirected to a
tmp file. No network, no live LLM.
"""

import json
import shutil

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.agents.author import author_candidate, propose_tool

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git not installed")


class FakeConfig:
    """Minimal Config double covering what author.py + the router read."""

    def __init__(self, *, instance_id="inst_test"):
        self.model = "cheap-model"
        self.model_cheap = "cheap-model"
        self.model_smart = "smart-model"
        self.router_mode = "rules"
        self.instance_id = instance_id


class FakeProvider:
    """Replies with a canned JSON files payload (or raises, for the degrade path)."""

    def __init__(self, *, files_json="", raise_exc=None):
        self.name = "fake"
        self.zero_retention = False
        self.is_local = False
        self._files_json = files_json
        self._raise = raise_exc

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        from lhassa.llm.provider import Completion

        if self._raise:
            raise self._raise
        return Completion(text=self._files_json, model=model or "?", cost_usd=0.0)


def _files_payload(name: str) -> str:
    tool_py = "def double(n):\n    return n * 2\n"
    test_py = (
        f"from {name} import double\n\n"
        "def test_double():\n    assert double(3) == 6\n"
    )
    return json.dumps({"files": {f"{name}.py": tool_py, f"test_{name}.py": test_py}})


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


# ── author_candidate ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_author_candidate_returns_files_on_success():
    provider = FakeProvider(files_json=_files_payload("doubler"))
    files = await author_candidate(
        "doubler", "doubles a number", provider=provider, config=FakeConfig()
    )
    assert set(files) == {"doubler.py", "test_doubler.py"}
    assert "def double" in files["doubler.py"]


@pytest.mark.asyncio
async def test_author_candidate_degrades_on_provider_not_configured():
    from lhassa.llm.provider import ProviderNotConfigured

    provider = FakeProvider(raise_exc=ProviderNotConfigured("no key"))
    files = await author_candidate(
        "doubler", "doubles a number", provider=provider, config=FakeConfig()
    )
    assert files == {}


@pytest.mark.asyncio
async def test_author_candidate_degrades_on_budget_exceeded():
    from lhassa.llm.gateway import BudgetExceeded

    provider = FakeProvider(raise_exc=BudgetExceeded("over budget"))
    files = await author_candidate(
        "doubler", "doubles a number", provider=provider, config=FakeConfig()
    )
    assert files == {}


@pytest.mark.asyncio
async def test_author_candidate_degrades_on_malformed_reply():
    provider = FakeProvider(files_json="not json at all")
    files = await author_candidate(
        "doubler", "doubles a number", provider=provider, config=FakeConfig()
    )
    assert files == {}


# ── propose_tool (chains authoring -> versioning -> gates) ───────────────────


@needs_git
@pytest.mark.asyncio
async def test_propose_tool_graduates_a_correct_candidate(brain):
    provider = FakeProvider(files_json=_files_payload("doubler"))
    outcome = await propose_tool(
        "doubler",
        "doubles a number",
        provider=provider,
        config=FakeConfig(),
        instance_id="inst_test",
        baseline=100.0,
        metric_fn=lambda *_: 1.0,  # lower-is-better; trivially beats the baseline
    )
    assert outcome["authored"] is True
    assert outcome["graduated"] is True
    assert outcome["estado"] == "graduada"
    assert outcome.get("approval_id")

    row = await brain.get_tool("inst_test", "doubler")
    assert row["estado"] == "graduada"


@needs_git
@pytest.mark.asyncio
async def test_propose_tool_denies_without_baseline(brain):
    """Deny-by-default: no baseline/metric_fn -> gate 3 fails, no approval parked."""
    provider = FakeProvider(files_json=_files_payload("doubler2"))
    outcome = await propose_tool(
        "doubler2",
        "doubles a number",
        provider=provider,
        config=FakeConfig(),
        instance_id="inst_test",
    )
    assert outcome["authored"] is True
    assert outcome["graduated"] is False
    assert "approval_id" not in outcome


@pytest.mark.asyncio
async def test_propose_tool_reports_not_authored_when_degraded(brain):
    from lhassa.llm.provider import ProviderNotConfigured

    provider = FakeProvider(raise_exc=ProviderNotConfigured("no key"))
    outcome = await propose_tool(
        "doubler3", "doubles a number", provider=provider, config=FakeConfig(),
        instance_id="inst_test",
    )
    assert outcome["authored"] is False
    assert outcome["graduated"] is False
