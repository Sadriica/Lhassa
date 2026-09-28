"""The loops table (Wave 0 of the intelligence plan); bounded iteration state.

Storage-only tests: the loop RUNTIME (forge iterate, /goal verify-terminate)
lands in Wave A; this pins the substrate those build on.
"""

import asyncio

import pytest


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


def test_loop_roundtrip_with_json_columns(brain):
    db = brain
    asyncio.run(db.insert_loop({
        "id": "loop_1", "instance_id": "inst_personal",
        "goal": "all tests in tests/auth pass",
        "kind": "closed", "origin": "forge", "max_turns": 3,
        "verifier_spec": {"type": "tests", "path": "tests/auth"},
        "created_at": "2026-07-28T00:00:00+00:00",
        "updated_at": "2026-07-28T00:00:00+00:00",
    }))
    row = asyncio.run(db.get_loop("inst_personal", "loop_1"))
    assert row["goal"].startswith("all tests")
    assert row["verifier_spec"] == {"type": "tests", "path": "tests/auth"}
    assert row["turn"] == 0 and row["max_turns"] == 3
    assert row["status"] == "plan"


def test_advance_records_turn_verdict_and_status(brain):
    db = brain
    asyncio.run(db.insert_loop({
        "id": "loop_2", "instance_id": "inst_personal", "goal": "g",
        "created_at": "2026-07-28T00:00:00+00:00",
        "updated_at": "2026-07-28T00:00:00+00:00",
    }))
    ok = asyncio.run(db.advance_loop(
        "inst_personal", "loop_2",
        status="iterate", verdict={"passed": False, "detail": "2 tests red"},
        bump_turn=True,
    ))
    assert ok
    row = asyncio.run(db.get_loop("inst_personal", "loop_2"))
    assert row["turn"] == 1
    assert row["status"] == "iterate"
    assert row["last_verdict"]["detail"] == "2 tests red"


def test_list_filters_by_status_and_unknown_loop_is_none(brain):
    db = brain
    for i, status in enumerate(["plan", "done", "capped"]):
        asyncio.run(db.insert_loop({
            "id": f"loop_{i}", "instance_id": "inst_personal", "goal": "g",
            "status": status,
            "created_at": f"2026-07-28T00:00:0{i}+00:00",
            "updated_at": f"2026-07-28T00:00:0{i}+00:00",
        }))
    capped = asyncio.run(db.list_loops("inst_personal", status="capped"))
    assert [r["id"] for r in capped] == ["loop_2"]
    assert asyncio.run(db.get_loop("inst_personal", "nope")) is None
