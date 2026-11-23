"""Tests for the one-shot legacy reminder migration.

``tools.handlers.schedule.migrate_legacy_reminders`` copies still-pending rows
from the legacy ``memory/store.py`` ``scheduled_tasks`` table into the real
``tasks`` table (``core/db.py``) as ``send_reminder`` tasks, then marks the
legacy rows ``migrated`` (never deleted). Guarded by a ``watch_state`` marker
so a second run is a no-op; verified explicitly below.
"""

from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _register_send_reminder():
    """Register ``send_reminder`` in the ACTIONS registry for this test module.

    Production registration happens in ``actionpack/__init__.py`` +
    ``security/allowlist.py`` (out of scope for this pass; see
    ``doc/scheduler.md``); ``mint_task`` only requires the accion to be a known
    member of ``core.actions.ACTIONS`` to accept a non-judgment task, so
    registering it directly here (via the public registry API, not by editing
    those files) is enough to exercise the real ``mint_task`` path.
    """
    from lhassa.core.actions import _REGISTRY
    from lhassa.actionpack._reminder import send_reminder

    if _REGISTRY.get_or_none("send_reminder") is None:
        from lhassa.core.actions import register_action

        register_action("send_reminder", send_reminder)
    yield


@pytest.fixture
def legacy_store(tmp_path, monkeypatch):
    """Redirect the legacy memory store DB to a tmp file, with two pending rows."""
    import lhassa.memory.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "legacy_memory.db")
    _run(store.init_db())
    return store


@pytest.mark.asyncio
async def test_migration_copies_pending_legacy_reminders(legacy_store, monkeypatch):
    import lhassa.core.db as db
    from lhassa.tools.handlers.schedule import migrate_legacy_reminders

    await db.init_db()
    inst = "inst_personal"

    id1 = await legacy_store.add_scheduled_task(
        "call John", "2026-04-01T14:20:00+00:00", "Time to call John"
    )
    id2 = await legacy_store.add_scheduled_task(
        "check backup", "2026-04-01T16:00:00+00:00", "Backup reminder"
    )
    # An already-fired legacy reminder should NOT be migrated.
    id3 = await legacy_store.add_scheduled_task(
        "old one", "2020-01-01T00:00:00+00:00", "stale"
    )
    await legacy_store.mark_task_done(id3)

    summary = await migrate_legacy_reminders(inst)
    assert "migrated 2" in summary

    tasks = await db.list_tasks(inst)
    reminders = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert len(reminders) == 2

    objetivos = {t["objetivo"] for t in reminders}
    assert objetivos == {"call John", "check backup"}
    for t in reminders:
        assert t["estado"] == "pendiente"
        assert t["params"]["text"] in ("Time to call John", "Backup reminder")

    # Legacy rows are marked migrated, never deleted.
    all_legacy = await legacy_store.load_all_scheduled_tasks()
    statuses = {row["id"]: row["status"] for row in all_legacy}
    assert statuses[id1] == "migrated"
    assert statuses[id2] == "migrated"
    assert statuses[id3] == "done"  # untouched; was never pending
    assert len(all_legacy) == 3  # nothing deleted


@pytest.mark.asyncio
async def test_migration_is_idempotent_no_duplicates(legacy_store, monkeypatch):
    import lhassa.core.db as db
    from lhassa.tools.handlers.schedule import migrate_legacy_reminders

    await db.init_db()
    inst = "inst_personal"

    await legacy_store.add_scheduled_task(
        "call John", "2026-04-01T14:20:00+00:00", "Time to call John"
    )

    first = await migrate_legacy_reminders(inst)
    assert "migrated 1" in first

    second = await migrate_legacy_reminders(inst)
    assert "already applied" in second

    tasks = await db.list_tasks(inst)
    reminders = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert len(reminders) == 1  # not duplicated by the second run


@pytest.mark.asyncio
async def test_migration_with_no_legacy_rows_is_a_clean_noop(legacy_store):
    import lhassa.core.db as db
    from lhassa.tools.handlers.schedule import migrate_legacy_reminders

    await db.init_db()
    inst = "inst_personal"

    summary = await migrate_legacy_reminders(inst)
    assert "migrated 0" in summary

    tasks = await db.list_tasks(inst)
    assert not [t for t in tasks if t.get("accion") == "send_reminder"]
