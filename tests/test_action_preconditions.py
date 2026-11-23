# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preconditions; an action that cannot act is skipped, not run to a no-op.

Three scheduled actions were running daily against a disabled knowledge graph
and unset knowledge roots. None of them failed; none of them COULD succeed, and
each no-op still paid a full down-execute-up delivery trail. These tests fix the
two properties that matter: nothing is logged for a skip, and the schedule
survives it so the action resumes by itself once configured.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())
    return db


# ── the registry ─────────────────────────────────────────────────────────────────


def test_an_action_without_a_precondition_is_always_runnable():
    from lhassa.core import actions

    assert actions.precondition_unmet("sync_repo", {}) == ""
    assert actions.precondition_unmet("does-not-exist", {}) == ""


def test_a_precondition_reports_why_the_action_cannot_act(monkeypatch):
    from lhassa.core import actions

    monkeypatch.setitem(actions._PRECONDITIONS, "probe", lambda p: "nothing configured")
    assert actions.precondition_unmet("probe", {}) == "nothing configured"


def test_a_raising_precondition_fails_open(monkeypatch):
    """A buggy capability check must never be able to silence a working action."""
    from lhassa.core import actions

    def boom(_params):
        raise RuntimeError("boom")

    monkeypatch.setitem(actions._PRECONDITIONS, "probe", boom)
    assert actions.precondition_unmet("probe", {}) == ""


def test_blocked_actions_lists_only_what_cannot_act(monkeypatch):
    from lhassa.core import actions

    monkeypatch.setitem(actions._PRECONDITIONS, "sync_repo", lambda p: "disabled")
    blocked = actions.blocked_actions()
    assert blocked.get("sync_repo") == "disabled"
    assert "tracking_postulaciones" not in blocked


# ── the real preconditions ───────────────────────────────────────────────────────


def test_kg_actions_are_blocked_while_the_graph_is_off():
    from lhassa.actionpack._kg import kg_possible

    class Off:
        memory_graph_enabled = False

    class On:
        memory_graph_enabled = True

    assert "disabled" in kg_possible({"config": Off})
    assert kg_possible({"config": On}) == ""


def test_ingest_knowledge_is_blocked_without_roots():
    from lhassa.actionpack._knowledge import ingest_knowledge_possible

    class NoRoots:
        knowledge_roots = ""

    class WithRoots:
        knowledge_roots = "/tmp/docs"

    assert "no knowledge roots" in ingest_knowledge_possible({"config": NoRoots})
    assert ingest_knowledge_possible({"config": WithRoots}) == ""


def test_temple_run_is_exactly_as_capable_as_the_action_it_fronts(monkeypatch):
    from lhassa.actionpack._temple import temple_run_possible
    from lhassa.core import actions

    monkeypatch.setitem(actions._PRECONDITIONS, "kg_sync", lambda p: "graph off")
    assert temple_run_possible({"action": "kg_sync"}) == "graph off"
    assert temple_run_possible({"action": "distill_memory"}) == ""
    assert temple_run_possible({}) == ""


# ── the heartbeat ────────────────────────────────────────────────────────────────


def test_a_blocked_action_leaves_no_episode_but_keeps_its_schedule(brain, monkeypatch):
    """The whole point: silence in the log, continuity in the schedule."""
    from lhassa.actionpack import register_all
    from lhassa.core import actions, dispatch, step
    from lhassa.security.trust import Origin, TrustLevel

    register_all()
    monkeypatch.setitem(actions._PRECONDITIONS, "temple_run", lambda p: "cannot act")

    async def scenario():
        await dispatch.mint_task(
            "inst_test",
            "temple_run",
            params={"action": "kg_sync"},
            origin=Origin(trust=TrustLevel(1), actor_id="seed:maintenance"),
            recurrence="86400",
        )
        await step.step("inst_test")

    asyncio.run(scenario())

    conn = sqlite3.connect(brain.DB_PATH)
    episodes = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE estado='hecha'"
    ).fetchone()[0]
    queued = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE estado='pendiente'"
    ).fetchone()[0]
    conn.close()

    assert episodes == 0, "a skipped action must not describe a non-event"
    assert done == 1, "the task completes, or the beat re-fires it forever"
    assert queued == 1, "the next occurrence is minted; the schedule survives"
