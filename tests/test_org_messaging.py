"""Tests for lhassa.org.messaging; the typed inter-brain task queue (Fase 7)."""

import pytest
import pytest_asyncio  # noqa: F401


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


# ── schema ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_messages_table_created(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert "org_messages" in tables
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA table_info(org_messages)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "instance_id" in cols


# ── typed-only ─────────────────────────────────────────────────────────────────

def test_unknown_free_prose_kind_rejected():
    from lhassa.org import messaging
    with pytest.raises(messaging.UnknownMessageKind):
        messaging.new_message(
            "please go do whatever you think is best",
            from_instance="general",
            to_instance="dept:x",
        )


def test_message_construction_rejects_unknown_kind():
    from lhassa.org.messaging import Message, UnknownMessageKind
    with pytest.raises(UnknownMessageKind):
        Message(id="m1", kind="FREEFORM", from_instance="a", to_instance="b")


def test_message_kind_is_closed_set():
    from lhassa.org.messaging import MessageKind
    assert {k.value for k in MessageKind} == {
        "TASK_ASSIGN", "TASK_RESULT", "STATUS_REQUEST", "STATUS_REPORT"
    }


def test_string_kind_coerced_to_enum():
    from lhassa.org.messaging import MessageKind, new_message
    msg = new_message("TASK_ASSIGN", from_instance="general", to_instance="dept:x")
    assert msg.kind is MessageKind.TASK_ASSIGN


def test_invalid_status_rejected():
    from lhassa.org.messaging import MessageKind, Message
    with pytest.raises(ValueError):
        Message(id="m1", kind=MessageKind.TASK_ASSIGN, from_instance="a",
                to_instance="b", status="bogus")


# ── persistence (the durable queue) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_enqueue_and_inbox_roundtrip(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind

    msg = messaging.new_message(
        MessageKind.TASK_ASSIGN,
        from_instance="general",
        to_instance="dept:sales",
        payload={"goal": "draft a quote"},
    )
    await messaging.enqueue(msg, instance_id="inst_personal")

    inbox = await messaging.inbox("dept:sales", instance_id="inst_personal")
    assert len(inbox) == 1
    assert inbox[0].kind is MessageKind.TASK_ASSIGN
    assert inbox[0].payload == {"goal": "draft a quote"}
    assert inbox[0].from_instance == "general"


@pytest.mark.asyncio
async def test_inbox_scoped_to_recipient_and_instance(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind

    await messaging.enqueue(
        messaging.new_message(MessageKind.TASK_ASSIGN, from_instance="general",
                              to_instance="dept:a"),
        instance_id="inst_personal",
    )
    await messaging.enqueue(
        messaging.new_message(MessageKind.TASK_ASSIGN, from_instance="general",
                              to_instance="dept:b"),
        instance_id="inst_personal",
    )
    await messaging.enqueue(
        messaging.new_message(MessageKind.TASK_ASSIGN, from_instance="general",
                              to_instance="dept:a"),
        instance_id="other_tenant",
    )

    inbox_a = await messaging.inbox("dept:a", instance_id="inst_personal")
    assert len(inbox_a) == 1


@pytest.mark.asyncio
async def test_corr_id_correlates_result_to_assignment(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind

    assign = messaging.new_message(
        MessageKind.TASK_ASSIGN, from_instance="general", to_instance="dept:x",
        corr_id="corr-1",
    )
    result = messaging.new_message(
        MessageKind.TASK_RESULT, from_instance="dept:x", to_instance="general",
        corr_id="corr-1",
    )
    await messaging.enqueue(assign, instance_id="inst_personal")
    await messaging.enqueue(result, instance_id="inst_personal")

    both = await messaging.by_corr("corr-1", instance_id="inst_personal")
    assert len(both) == 2
    results = await messaging.by_corr(
        "corr-1", instance_id="inst_personal", kind=MessageKind.TASK_RESULT
    )
    assert len(results) == 1
    assert results[0].kind is MessageKind.TASK_RESULT


@pytest.mark.asyncio
async def test_mark_advances_status(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind

    msg = messaging.new_message(MessageKind.TASK_ASSIGN, from_instance="general",
                                to_instance="dept:x")
    await messaging.enqueue(msg, instance_id="inst_personal")

    await messaging.mark(msg.id, "done", instance_id="inst_personal")
    pending = await messaging.inbox("dept:x", instance_id="inst_personal",
                                    status="pending")
    assert pending == []
    done = await messaging.inbox("dept:x", instance_id="inst_personal", status="done")
    assert len(done) == 1


@pytest.mark.asyncio
async def test_mark_rejects_invalid_status(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind

    msg = messaging.new_message(MessageKind.TASK_ASSIGN, from_instance="general",
                                to_instance="dept:x")
    await messaging.enqueue(msg, instance_id="inst_personal")
    with pytest.raises(ValueError):
        await messaging.mark(msg.id, "garbage", instance_id="inst_personal")
