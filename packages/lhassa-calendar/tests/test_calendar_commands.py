"""The calendar pack's chat commands: /calendar /calendars /event and the
``.ics`` extra it plugs into core's ``/remind``. Split from core's
``tests/test_telegram_commands.py``. No real network, no real LLM.
"""

import asyncio
from types import SimpleNamespace


from lhassa.notify import commands


def _cfg(**over):
    base = dict(
        heartbeat_provider="opencode",
        opencode_model="opencode/deepseek-v4-flash-free",
        opencode_bin="opencode",
        local_model_url="",
        local_model="",
        model="cloud-smart",
    )
    base.update(over)
    return SimpleNamespace(**base)


async def _init_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


# ── /remind + /event; the additive .ics calendar delivery ────────────────────


def _fake_deliver(calls):
    async def fake(*, document=None, caption="", kind="message", metadata=None,
                    origin_department="", config=None, **kwargs):
        calls.append(
            {
                "document": document,
                "caption": caption,
                "kind": kind,
                "metadata": metadata,
                "origin_department": origin_department,
            }
        )
        return True, "art_fake"

    return fake


def test_remind_also_delivers_a_calendar_event(tmp_path, monkeypatch):
    from lhassa.org import deliver as deliver_mod

    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    calls: list = []
    monkeypatch.setattr(deliver_mod, "deliver", _fake_deliver(calls))

    reply = asyncio.run(
        commands.dispatch(
            "/remind llamar al banco a las 5pm", config=_cfg(instance_id="inst_personal")
        )
    )

    # the internal reminder UX is unchanged
    assert reply.startswith("⏰ Noted")
    assert "llamar al banco" in reply
    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=10))
    match = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert len(match) == 1

    # AND a calendar_event was delivered
    assert len(calls) == 1
    call = calls[0]
    assert call["kind"] == "calendar_event"
    assert str(call["document"]).endswith(".ics")
    assert call["metadata"]["summary"] == "llamar al banco"
    assert call["origin_department"] == ""


def test_remind_survives_calendar_delivery_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    db = asyncio.run(_init_db(tmp_path, monkeypatch))

    async def boom(**kwargs):
        raise RuntimeError("deliver is down")

    from lhassa.org import deliver as deliver_mod

    monkeypatch.setattr(deliver_mod, "deliver", boom)

    reply = asyncio.run(
        commands.dispatch(
            "/remind llamar al banco a las 5pm", config=_cfg(instance_id="inst_personal")
        )
    )
    # the reminder still works even though the .ics delivery blew up
    assert reply.startswith("⏰ Noted")
    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=10))
    match = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert len(match) == 1


def test_event_command_delivers_ics_with_no_reminder_task(tmp_path, monkeypatch):
    from lhassa.org import deliver as deliver_mod

    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    calls: list = []
    monkeypatch.setattr(deliver_mod, "deliver", _fake_deliver(calls))

    reply = asyncio.run(
        commands.dispatch(
            "/event dentist appointment at 3pm", config=_cfg(instance_id="inst_personal")
        )
    )

    assert reply.startswith("📅 Sent")
    assert "dentist appointment" in reply

    assert len(calls) == 1
    assert calls[0]["kind"] == "calendar_event"
    assert str(calls[0]["document"]).endswith(".ics")

    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=10))
    match = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert match == []


def test_event_command_degrades_when_delivery_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    asyncio.run(_init_db(tmp_path, monkeypatch))

    async def fails(**kwargs):
        return False, None

    from lhassa.org import deliver as deliver_mod

    monkeypatch.setattr(deliver_mod, "deliver", fails)

    reply = asyncio.run(
        commands.dispatch(
            "/event dentist appointment at 3pm", config=_cfg(instance_id="inst_personal")
        )
    )
    assert "couldn't" in reply.lower()


def test_event_without_time_gives_usage_hint(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(commands.dispatch("/event algo sin hora", config=_cfg()))
    assert "what and when" in reply


# ── /calendar, /calendars ──────────────────────────────────────────────────────


def _cal_cfg(tmp_path, **over):
    return _cfg(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url="",
        **over,
    )


def test_calendars_empty_gives_a_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "No calendars configured" in reply


def test_calendar_add_list_remove_roundtrip(tmp_path):
    cfg = _cal_cfg(tmp_path)

    added = asyncio.run(
        commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg)
    )
    assert "Calendar added: Work" in added

    listed = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "Work" in listed
    # the url is masked, not echoed in full for a long url
    long_url = "https://example.edu/" + "x" * 60 + ".ics"
    asyncio.run(commands.dispatch(f"/calendar add Long {long_url}", config=cfg))
    listed2 = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert long_url not in listed2

    removed = asyncio.run(commands.dispatch("/calendar remove Work", config=cfg))
    assert "Calendar removed: Work" in removed

    listed3 = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "Work" not in listed3


def test_calendar_add_rejects_bad_url(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar add Bad not-a-url", config=cfg))
    assert "Couldn't add" in reply


def test_calendar_add_missing_args_usage_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar add OnlyName", config=cfg))
    assert "Usage" in reply


def test_calendar_remove_unknown_name(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar remove Ghost", config=cfg))
    assert "No calendar named" in reply


def test_calendar_no_args_usage_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar", config=cfg))
    assert "Usage" in reply


def test_calendar_unknown_subcommand(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar frobnicate", config=cfg))
    assert "Unknown /calendar sub-command" in reply


def test_calendar_view_no_when_usage_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar view", config=cfg))
    assert "Usage" in reply


def test_calendar_view_unparseable_when(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar view blorp", config=cfg))
    assert "Couldn't work out a time reference" in reply


def test_calendar_view_forces_a_deterministic_answer(tmp_path, monkeypatch):
    """``/calendar view friday`` answers even though a bare 'friday' has no
    calendar keyword; the command itself is the intent signal."""
    cfg = _cal_cfg(tmp_path)

    from lhassa.calendar import feed as feed_mod

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return []

    monkeypatch.setattr(feed_mod, "upcoming", _fake_upcoming)

    reply = asyncio.run(commands.dispatch("/calendar view friday", config=cfg))
    assert reply == "Friday you don't have anything on your calendar."


def test_calendars_shows_env_shortcut_masked(tmp_path):
    full_url = "https://example.com/shortcut-secret-feed-" + "z" * 30 + ".ics"
    cfg = _cfg(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url=full_url,
    )
    reply = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "default" in reply
    assert full_url not in reply  # the full secret url is never echoed back


# ── /calendar primary + /calendar sync; master calendar reconcile ────────────


def test_calendar_primary_sets_master(tmp_path):
    cfg = _cal_cfg(tmp_path)
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))

    reply = asyncio.run(commands.dispatch("/calendar primary Work", config=cfg))
    assert "Master calendar set: Work" in reply

    from lhassa.calendar import store as _cal_store

    assert _cal_store.get_primary(config=cfg) == "Work"


def test_calendar_primary_unknown_name(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar primary Ghost", config=cfg))
    assert "No calendar named" in reply


def test_calendar_primary_missing_arg_usage_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar primary", config=cfg))
    assert "Usage" in reply


def test_calendars_marks_the_primary(tmp_path):
    cfg = _cal_cfg(tmp_path)
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))
    asyncio.run(commands.dispatch("/calendar add Personal https://example.com/p.ics", config=cfg))
    asyncio.run(commands.dispatch("/calendar primary Work", config=cfg))

    listed = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "Work (primary)" in listed
    assert "Personal (primary)" not in listed


def test_calendar_sync_reports_no_primary(tmp_path):
    cfg = _cal_cfg(tmp_path)
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))

    reply = asyncio.run(commands.dispatch("/calendar sync", config=cfg))
    assert "no primary" in reply.lower()


def test_calendar_sync_reports_found_and_delivered(tmp_path, monkeypatch):
    cfg = _cal_cfg(tmp_path, instance_id="inst_personal")
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))
    asyncio.run(commands.dispatch("/calendar primary Work", config=cfg))

    from lhassa.calendar.surfaces import actions as _calendar_sync

    async def fake_sync(params):
        return "calendar_sync: 2 unsynced event(s), 2 delivered"

    monkeypatch.setattr(_calendar_sync, "calendar_sync", fake_sync)

    reply = asyncio.run(commands.dispatch("/calendar sync", config=cfg))
    assert "I found 2 event" in reply
    assert "sent you 2" in reply


def test_calendar_sync_reports_nothing_to_sync(tmp_path, monkeypatch):
    cfg = _cal_cfg(tmp_path, instance_id="inst_personal")
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))
    asyncio.run(commands.dispatch("/calendar primary Work", config=cfg))

    from lhassa.calendar.surfaces import actions as _calendar_sync

    async def fake_sync(params):
        return "calendar_sync: 0 unsynced event(s), 0 delivered"

    monkeypatch.setattr(_calendar_sync, "calendar_sync", fake_sync)

    reply = asyncio.run(commands.dispatch("/calendar sync", config=cfg))
    assert "already in sync" in reply.lower()
