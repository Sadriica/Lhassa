"""Tests for the forge's bounded iteration loop (Wave A, TASK A1).

``propose_tool`` now loops up to ``max_turns``: author -> version -> gate, and on
a RETRYABLE gate failure (segura/correcta) it re-authors with the failing gate's
detail threaded in as feedback. Fully offline except for the real local git repo
+ real quarantine sandbox (both already exercised by ``tests/test_author.py``).
"""

import json
import shutil

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.agents.author import propose_tool

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git not installed")


class FakeConfig:
    def __init__(self, *, instance_id="inst_test"):
        self.model = "cheap-model"
        self.model_cheap = "cheap-model"
        self.model_smart = "smart-model"
        self.router_mode = "rules"
        self.instance_id = instance_id


def _failing_payload(name: str) -> str:
    # A test file that always fails; used to fail gate 1 (segura), a retryable
    # gate, on the first turn.
    tool_py = "def double(n):\n    return n * 2\n"
    test_py = f"from {name} import double\n\n\ndef test_double():\n    assert False\n"
    return json.dumps({"files": {f"{name}.py": tool_py, f"test_{name}.py": test_py}})


def _passing_payload(name: str) -> str:
    tool_py = "def double(n):\n    return n * 2\n"
    test_py = (
        f"from {name} import double\n\n\ndef test_double():\n    assert double(3) == 6\n"
    )
    return json.dumps({"files": {f"{name}.py": tool_py, f"test_{name}.py": test_py}})


class ScriptedProvider:
    """Replies with the next canned payload on each call; records every prompt."""

    def __init__(self, replies: list):
        self.name = "fake"
        self.zero_retention = False
        self.is_local = False
        self._replies = list(replies)
        self._last = replies[-1] if replies else ""
        self.calls: list = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        from lhassa.llm.provider import Completion

        self.calls.append(messages)
        text = self._replies.pop(0) if self._replies else self._last
        return Completion(text=text, model=model or "?", cost_usd=0.0)


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
async def test_iterate_recovers_on_turn_two(brain):
    provider = ScriptedProvider(
        [_failing_payload("adder"), _passing_payload("adder")]
    )
    outcome = await propose_tool(
        "adder",
        "adds by doubling",
        provider=provider,
        config=FakeConfig(),
        instance_id="inst_test",
        max_turns=3,
        baseline=100.0,
        metric_fn=lambda *_: 1.0,
    )

    assert outcome["graduated"] is True
    assert outcome["turns"] == 2
    assert len(provider.calls) == 2

    # Turn 2's prompt carries the turn-1 gate failure as feedback, never a raw
    # pass/fail judgment of the model's own output.
    turn2_prompt = provider.calls[1][0]["content"]
    assert "Feedback:" in turn2_prompt
    assert "gate" in turn2_prompt.lower()

    loop_id = outcome["loop_id"]
    assert loop_id
    row = await brain.get_loop("inst_test", loop_id)
    assert row["status"] == "done"
    assert row["turn"] == 1  # bumped once (turn-1 failure), not on the done turn

    episodes = await brain.load_recent_episodes("inst_test", n=10)
    forge_eps = [e for e in episodes if e.get("despertar_por") == "forge"]
    assert len(forge_eps) == 2


@needs_git
@pytest.mark.asyncio
async def test_iterate_caps_when_always_failing(brain):
    provider = ScriptedProvider([_failing_payload("nope")])  # always the same failure
    outcome = await propose_tool(
        "nope",
        "never works",
        provider=provider,
        config=FakeConfig(),
        instance_id="inst_test",
        max_turns=2,
        baseline=100.0,
        metric_fn=lambda *_: 1.0,
    )

    assert outcome["graduated"] is False
    assert outcome.get("capped") is True
    assert len(provider.calls) == 2  # never exceeds max_turns

    loop_id = outcome["loop_id"]
    row = await brain.get_loop("inst_test", loop_id)
    assert row["status"] == "capped"

    episodes = await brain.load_recent_episodes("inst_test", n=10)
    forge_eps = [e for e in episodes if e.get("despertar_por") == "forge"]
    assert len(forge_eps) == 2


@pytest.mark.asyncio
async def test_mejor_no_baseline_stops_early_not_retried(brain):
    """A missing baseline/metric_fn cannot be fixed by feedback; stop at turn 1."""
    provider = ScriptedProvider([_passing_payload("solo")])
    outcome = await propose_tool(
        "solo",
        "solo tool",
        provider=provider,
        config=FakeConfig(),
        instance_id="inst_test",
        max_turns=3,
        # no baseline/metric_fn given
    )

    assert outcome["authored"] is True
    assert outcome["graduated"] is False
    assert len(provider.calls) == 1  # never retried
    assert "approval_id" not in outcome

    loop_id = outcome["loop_id"]
    row = await brain.get_loop("inst_test", loop_id)
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_budget_exceeded_mid_loop_stops_without_raising(brain):
    from lhassa.llm.gateway import BudgetExceeded

    class RaisingProvider:
        name = "fake"
        zero_retention = False
        is_local = False

        async def complete(self, *a, **k):
            raise BudgetExceeded("over budget")

    outcome = await propose_tool(
        "budgeted",
        "irrelevant",
        provider=RaisingProvider(),
        config=FakeConfig(),
        instance_id="inst_test",
        max_turns=3,
    )

    assert outcome["authored"] is False
    assert outcome["graduated"] is False

    loop_id = outcome["loop_id"]
    row = await brain.get_loop("inst_test", loop_id)
    assert row["status"] == "iterate"
