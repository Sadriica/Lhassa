"""Tests for lhassa/org/inbox_sense.py — enqueue → heartbeat closes the loop.

Fully offline: a tmp brain DB, no model, no real topology needed (messaging is
self-contained). Verifies the watchlist sense drains THIS instance's org inbox,
turns a TASK_ASSIGN into a Trigger, and marks the message delivered (fires once).
"""

import pytest
import pytest_asyncio  # noqa: F401

INST = "inst_personal"


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


@pytest.mark.asyncio
async def test_from_org_inbox_turns_task_assign_into_org_run_trigger(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind
    from lhassa.org.inbox_sense import _from_org_inbox

    msg = messaging.new_message(
        MessageKind.TASK_ASSIGN,
        from_instance="general",
        to_instance="lhassa",
        payload={"goal": "sync the sales report", "department": "sales"},
    )
    await messaging.enqueue(msg, instance_id=INST)

    out: list = []
    await _from_org_inbox(INST, out, peek=False)

    assert len(out) == 1
    trig = out[0]
    assert trig.kind == "org"
    assert trig.action == "org_run"
    assert trig.judgment is False
    assert trig.params["goal"] == "sync the sales report"
    assert trig.params["department"] == "sales"

    # Fires exactly once: the message is now "delivered", not "pending".
    still_pending = await messaging.inbox("lhassa", instance_id=INST, status="pending")
    assert still_pending == []

    out2: list = []
    await _from_org_inbox(INST, out2, peek=False)
    assert out2 == []


@pytest.mark.asyncio
async def test_from_org_inbox_judgment_flag_routes_to_judgment_trigger(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind
    from lhassa.org.inbox_sense import _from_org_inbox

    msg = messaging.new_message(
        MessageKind.TASK_ASSIGN,
        from_instance="general",
        to_instance="lhassa",
        payload={"goal": "decide how to handle this", "judgment": True},
    )
    await messaging.enqueue(msg, instance_id=INST)

    out: list = []
    await _from_org_inbox(INST, out, peek=False)

    assert len(out) == 1
    assert out[0].judgment is True
    assert out[0].maps_to_fixed_action() is False


@pytest.mark.asyncio
async def test_from_org_inbox_peek_does_not_consume(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind
    from lhassa.org.inbox_sense import _from_org_inbox

    msg = messaging.new_message(
        MessageKind.TASK_ASSIGN,
        from_instance="general",
        to_instance="lhassa",
        payload={"goal": "peek me"},
    )
    await messaging.enqueue(msg, instance_id=INST)

    out: list = []
    await _from_org_inbox(INST, out, peek=True)
    assert len(out) == 1

    # peek=True never marks anything delivered.
    still_pending = await messaging.inbox("lhassa", instance_id=INST, status="pending")
    assert len(still_pending) == 1


@pytest.mark.asyncio
async def test_from_org_inbox_no_messages_never_raises(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.inbox_sense import _from_org_inbox

    out: list = []
    await _from_org_inbox(INST, out, peek=False)
    assert out == []


@pytest.mark.asyncio
async def test_registered_check_reaches_watchlist_check(tmp_path, monkeypatch):
    """The registered sense fires through the real watchlist.check() entrypoint."""
    await _init(tmp_path, monkeypatch)
    from lhassa.core import watchlist
    from lhassa.org import inbox_sense, messaging
    from lhassa.org.messaging import MessageKind

    # Reset the registration flag so this test proves register() actually wires it,
    # independent of whatever earlier tests in the session already did.
    original = inbox_sense._REGISTERED
    inbox_sense._REGISTERED = False
    original_extra = list(watchlist._EXTRA_CHECKS)
    try:
        watchlist._EXTRA_CHECKS.clear()
        inbox_sense.register()
        assert inbox_sense._from_org_inbox in watchlist._EXTRA_CHECKS

        msg = messaging.new_message(
            MessageKind.TASK_ASSIGN,
            from_instance="general",
            to_instance="lhassa",
            payload={"goal": "wired through check()"},
        )
        await messaging.enqueue(msg, instance_id=INST)

        triggers = await watchlist.check(INST)
        assert any(t.action == "org_run" and t.objetivo == "wired through check()" for t in triggers)
    finally:
        inbox_sense._REGISTERED = original
        watchlist._EXTRA_CHECKS.clear()
        watchlist._EXTRA_CHECKS.extend(original_extra)
