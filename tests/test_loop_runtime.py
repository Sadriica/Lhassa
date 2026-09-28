"""Tests for the loop RUNTIME (Wave A, TASK A2); /goal's verify-terminate loop.

``core/step.py:_judge`` reads ``trigger.params["_loop_id"]`` (stamped by
``notify/commands.py:_cmd_goal --verify ...``) each turn: enforces the turn cap
BEFORE any model call, runs the loop's ``verifier_spec`` (agents/verify.py,
$0/deterministic) on the model's reply, and either ends the loop ("done",
recurrence stopped) or iterates (bump_turn, feedback threaded into the NEXT
recurrence's params via ``trigger.params["_loop_feedback"]``).

Fully offline: a fake ModelProvider stands in for the routine model, the brain DB
is redirected to a tmp file. No network, no live LLM.
"""

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.llm.provider import Completion


class FakeProvider:
    """Offline ModelProvider double; records calls, returns a canned Completion."""

    def __init__(self, *, replies=None, name="fake"):
        self.name = name
        self.zero_retention = False
        self._replies = list(replies or [])
        self._last = self._replies[-1] if self._replies else "ok"
        self.calls = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        self.calls.append({"messages": messages, "model": model})
        text = self._replies.pop(0) if self._replies else self._last
        return Completion(text=text, model=model or "m", cost_usd=0.0)


@pytest_asyncio.fixture
async def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


async def _mk_loop(db, *, max_turns=10, turn=0, status="plan", verifier_spec=None):
    return await db.insert_loop({
        "id": db._new_id("loop"),
        "instance_id": "inst_personal",
        "goal": "the reply must say READY",
        "kind": "closed",
        "origin": "telegram-goal",
        "status": status,
        "turn": turn,
        "max_turns": max_turns,
        "verifier_spec": verifier_spec or {"type": "regex", "pattern": "READY"},
        "created_at": db._now(),
        "updated_at": db._now(),
    })


@pytest.mark.asyncio
async def test_verify_pass_marks_loop_done_and_stops_recurrence(brain):
    db = brain
    from lhassa.core import step

    loop_id = await _mk_loop(db)
    await db.add_task(
        "inst_personal", "goal", "watch for READY", params={"_judgment": True, "_loop_id": loop_id},
        recurrence="60", origen_trust=2, origen_actor="telegram",
    )

    prov = FakeProvider(replies=["all systems READY"])
    summary = await step.step("inst_personal", provider=prov)
    assert summary["judged"] == 1
    assert len(prov.calls) == 1

    row = await db.get_loop("inst_personal", loop_id)
    assert row["status"] == "done"
    assert row["turn"] == 0  # not bumped on a pass

    # Recurrence stopped: no new pending task was minted for the next occurrence.
    pending = await db.list_tasks("inst_personal", estado="pendiente")
    assert pending == []


@pytest.mark.asyncio
async def test_verify_fail_iterates_and_threads_feedback_then_passes(brain):
    db = brain
    from lhassa.core import step

    loop_id = await _mk_loop(db, max_turns=3)
    await db.add_task(
        "inst_personal", "goal", "watch for READY", params={"_judgment": True, "_loop_id": loop_id},
        recurrence="60", origen_trust=2, origen_actor="telegram",
    )

    prov = FakeProvider(replies=["still working on it"])
    summary = await step.step("inst_personal", provider=prov)
    assert summary["judged"] == 1

    row = await db.get_loop("inst_personal", loop_id)
    assert row["status"] == "iterate"
    assert row["turn"] == 1
    assert row["last_verdict"]["passed"] is False

    # Recurrence continued: a new occurrence was minted, carrying the feedback.
    pending = await db.list_tasks("inst_personal", estado="pendiente")
    assert len(pending) == 1
    next_params = pending[0]["params"]
    assert next_params.get("_loop_id") == loop_id
    assert "READY" in (next_params.get("_loop_feedback") or "")

    # Second turn: simulate the recurrence becoming due (the real minted next
    # occurrence carries a future due_at; retire it and re-mint as immediately
    # due with the SAME params; the loop-runtime bit under test is the
    # params/feedback threading, not the recurrence scheduler's timing).
    await db.set_task_state("inst_personal", pending[0]["id"], "hecha")
    await db.add_task(
        "inst_personal", "goal", "watch for READY", params=next_params,
        origen_trust=2, origen_actor="telegram",
    )
    prov2 = FakeProvider(replies=["all systems READY"])
    summary2 = await step.step("inst_personal", provider=prov2)
    assert summary2["judged"] == 1

    # The second turn's prompt carried the threaded feedback.
    prompt2 = prov2.calls[0]["messages"][0]["content"]
    assert "NO pasó" in prompt2 or "READY" in prompt2

    row2 = await db.get_loop("inst_personal", loop_id)
    assert row2["status"] == "done"
    assert (await db.list_tasks("inst_personal", estado="pendiente")) == []


@pytest.mark.asyncio
async def test_turn_cap_stops_before_any_model_call(brain):
    db = brain
    from lhassa.core import step

    # Already at the cap; the model must never run this turn.
    loop_id = await _mk_loop(db, max_turns=2, turn=2, status="iterate")
    await db.add_task(
        "inst_personal", "goal", "watch for READY", params={"_judgment": True, "_loop_id": loop_id},
        recurrence="60", origen_trust=2, origen_actor="telegram",
    )

    prov = FakeProvider(replies=["all systems READY"])
    summary = await step.step("inst_personal", provider=prov)
    assert summary["judged"] == 1
    assert prov.calls == []  # never called; the cap is enforced BEFORE work

    row = await db.get_loop("inst_personal", loop_id)
    assert row["status"] == "capped"

    # Recurrence stopped.
    assert (await db.list_tasks("inst_personal", estado="pendiente")) == []

    eps = await db.load_recent_episodes("inst_personal")
    assert any("capped" in (e.get("decision") or "") for e in eps)


@pytest.mark.asyncio
async def test_missing_loop_row_degrades_to_normal_judgment(brain):
    """A dangling/broken _loop_id must never break a normal /goal turn."""
    db = brain
    from lhassa.core import step

    await db.add_task(
        "inst_personal", "goal", "no loop here",
        params={"_judgment": True, "_loop_id": "nope-does-not-exist"},
        recurrence="60", origen_trust=2, origen_actor="telegram",
    )

    prov = FakeProvider(replies=["whatever"])
    summary = await step.step("inst_personal", provider=prov)
    assert summary["judged"] == 1
    assert len(prov.calls) == 1

    # Recurrence behaves normally (a new occurrence IS minted; a missing loop
    # row must not accidentally stop a goal's recurrence).
    pending = await db.list_tasks("inst_personal", estado="pendiente")
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_no_loop_id_behaves_exactly_like_a_plain_goal(brain):
    """Regression: a /goal task with no _loop_id is untouched by the loop code."""
    db = brain
    from lhassa.core import step

    await db.add_task(
        "inst_personal", "goal", "plain goal, no verify",
        params={"_judgment": True},
        recurrence="60", origen_trust=2, origen_actor="telegram",
    )

    prov = FakeProvider(replies=["fine"])
    summary = await step.step("inst_personal", provider=prov)
    assert summary["judged"] == 1

    pending = await db.list_tasks("inst_personal", estado="pendiente")
    assert len(pending) == 1
    assert "_loop_id" not in pending[0]["params"]
    assert "_loop_feedback" not in pending[0]["params"]
