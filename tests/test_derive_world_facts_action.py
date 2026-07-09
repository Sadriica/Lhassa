"""Tests for the ``derive_world_facts`` action (lhassa/actionpack/_derive.py).

Offline, no network: :func:`lhassa.calendar.derive.derive_world` is
monkeypatched to return fixed candidates so nothing touches a real calendar
feed. Uses a tmp brain DB (monkeypatched ``db.DB_PATH``) for real fact writes
via :mod:`lhassa.memory.facts`, matching the pattern in
tests/test_calendar_sync_action.py.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.actionpack import _derive
from lhassa.core import db


@pytest.fixture
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


def _cfg(**over):
    base = dict(instance_id="inst_personal")
    base.update(over)
    return SimpleNamespace(**base)


def _candidate(contenido, tags=("calendar", "course"), confianza=0.7):
    return {
        "contenido": contenido,
        "tags": list(tags),
        "procedencia": "calendar-derived",
        "confianza": confianza,
    }


def test_no_candidates_is_nothing_to_derive(_db, monkeypatch):
    async def fake_derive(instance_id, *, config=None, days=45):
        return []

    from lhassa.calendar import derive as derive_mod

    monkeypatch.setattr(derive_mod, "derive_world", fake_derive)

    out = asyncio.run(_derive.derive_world_facts({"config": _cfg()}))
    assert out == "nothing to derive"


def test_new_candidates_are_persisted(_db, monkeypatch):
    candidates = [
        _candidate("Has a recurring weekly commitment 'Algorithms 201' (Tue 15:00) — likely a course or class this term."),
        _candidate("Your calendar is busiest on Tue mornings.", tags=("calendar", "rhythm")),
    ]

    async def fake_derive(instance_id, *, config=None, days=45):
        return candidates

    from lhassa.calendar import derive as derive_mod

    monkeypatch.setattr(derive_mod, "derive_world", fake_derive)

    out = asyncio.run(_derive.derive_world_facts({"config": _cfg()}))
    assert "derived 2 world facts, 2 new" in out

    from lhassa.memory import facts as _facts

    stored = asyncio.run(_facts.load_facts("inst_personal"))
    assert len(stored) == 2
    assert all(f.procedencia == "calendar-derived" for f in stored)


def test_second_run_dedupes_against_existing_facts(_db, monkeypatch):
    candidates = [_candidate("Has a recurring weekly commitment 'Algorithms 201' (Tue 15:00) — likely a course or class this term.")]

    async def fake_derive(instance_id, *, config=None, days=45):
        return candidates

    from lhassa.calendar import derive as derive_mod

    monkeypatch.setattr(derive_mod, "derive_world", fake_derive)

    out1 = asyncio.run(_derive.derive_world_facts({"config": _cfg()}))
    assert "1 new" in out1

    out2 = asyncio.run(_derive.derive_world_facts({"config": _cfg()}))
    assert "0 new" in out2

    from lhassa.memory import facts as _facts

    stored = asyncio.run(_facts.load_facts("inst_personal"))
    assert len(stored) == 1  # no duplicate written


def test_never_raises_when_derive_world_errors(_db, monkeypatch):
    async def boom(instance_id, *, config=None, days=45):
        raise RuntimeError("boom")

    from lhassa.calendar import derive as derive_mod

    monkeypatch.setattr(derive_mod, "derive_world", boom)

    out = asyncio.run(_derive.derive_world_facts({"config": _cfg()}))
    assert "error" in out.lower()
