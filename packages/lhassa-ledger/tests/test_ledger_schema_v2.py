# SPDX-License-Identifier: AGPL-3.0-or-later
"""Schema v2: migration in place, and the OLD writer still working afterwards."""

from __future__ import annotations

import sqlite3
import time

import pytest

from agent_ledger import query
from agent_ledger.cli import main as ledger_main
from agent_ledger.event import (
    KIND_DECISION,
    KIND_TOOL_CALL,
    OUTCOME_DENIED,
    OUTCOME_PENDING,
    AgentEvent,
    pending_event,
    resolution_event,
)
from agent_ledger.policy import Policy
from agent_ledger.store import Store

# The v1 schema and INSERT, copied verbatim from the standalone agent-ledger
# 0.1.0 store (the code the global Claude Code hook still runs today).
V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id                 TEXT PRIMARY KEY,
    ts                 REAL NOT NULL,
    host               TEXT NOT NULL,
    kind               TEXT NOT NULL,
    agent              TEXT NOT NULL DEFAULT '',
    session            TEXT NOT NULL DEFAULT '',
    turn               TEXT NOT NULL DEFAULT '',
    provider           TEXT NOT NULL DEFAULT '',
    model              TEXT NOT NULL DEFAULT '',
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL,
    cost_source        TEXT NOT NULL DEFAULT 'unknown',
    tool               TEXT NOT NULL DEFAULT '',
    target             TEXT NOT NULL DEFAULT '',
    outcome            TEXT NOT NULL DEFAULT 'ok',
    duration_ms        INTEGER NOT NULL DEFAULT 0,
    detail             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_host_ts ON events(host, ts);
CREATE INDEX IF NOT EXISTS idx_events_outcome ON events(outcome, ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
V1_COLUMNS = (
    "id", "ts", "host", "kind", "agent", "session", "turn", "provider", "model",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "cost_usd", "cost_source", "tool", "target", "outcome",
    "duration_ms", "detail",
)
V1_INSERT = (
    f"INSERT OR IGNORE INTO events ({', '.join(V1_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in V1_COLUMNS)})"
)


def _v1_row(eid: str, ts: float | None = None):
    return (eid, ts or time.time(), "claude-code", "tool_call", "PostToolUse", "s1", "",
            "", "", 0, 0, 0, 0, 0, 0.0, "table", "Edit", "/tmp/x.py", "ok", 0, '{"cwd": "/tmp"}')


def _old_writer(path, eid: str) -> None:
    """Exactly what the old hook's Store does: open, run its schema, INSERT."""
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1')")
    conn.execute(V1_INSERT, _v1_row(eid))
    conn.close()


@pytest.fixture()
def v1_db(tmp_path):
    """A synthetic v1 database with data. Never the owner's real one."""
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    for i in range(5):
        conn.execute(V1_INSERT, _v1_row(f"old-{i}"))
    conn.close()
    return path


def _columns(path):
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    conn.close()
    return cols


def test_a_v1_database_migrates_in_place_and_keeps_its_rows(v1_db):
    store = Store(v1_db)
    assert {"instance_id", "machine_id"} <= _columns(v1_db)
    assert store.count() == 5
    assert store.query("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == "2"
    row = store.query("SELECT instance_id, machine_id, target FROM events WHERE id='old-0'")[0]
    assert (row["instance_id"], row["machine_id"], row["target"]) == ("", "", "/tmp/x.py")


def test_migration_is_idempotent(v1_db):
    Store(v1_db).close()
    Store(v1_db).close()
    store = Store(v1_db)
    assert store.count() == 5
    assert store.query("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == "2"


def test_a_fresh_database_is_v2(tmp_path):
    store = Store(tmp_path / "new.db")
    assert {"instance_id", "machine_id"} <= _columns(tmp_path / "new.db")
    assert store.query("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == "2"


def test_the_old_writer_still_works_against_a_migrated_database(v1_db):
    Store(v1_db).close()           # migrate to v2
    _old_writer(v1_db, "from-old-hook")
    store = Store(v1_db)
    assert store.count() == 6
    row = store.query("SELECT instance_id, machine_id FROM events WHERE id='from-old-hook'")[0]
    assert (row["instance_id"], row["machine_id"]) == ("", "")
    # and the old writer's INSERT OR IGNORE of meta did not downgrade the version
    assert store.query("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == "2"


def test_new_and_old_writers_interleave(tmp_path):
    db = tmp_path / "mixed.db"
    store = Store(db)
    store.record(AgentEvent(host="orca", kind=KIND_TOOL_CALL, machine_id="samos", instance_id="i1"))
    _old_writer(db, "old-1")
    store.record(AgentEvent(host="orca", kind=KIND_TOOL_CALL, machine_id="samos", instance_id="i2"))
    _old_writer(db, "old-2")
    assert store.count() == 4


def test_ids_round_trip(tmp_path):
    store = Store(tmp_path / "l.db")
    store.record(AgentEvent(id="e1", host="orca", kind=KIND_TOOL_CALL, machine_id="samos", instance_id="i1"))
    got = next(store.events())
    assert (got.machine_id, got.instance_id) == ("samos", "i1")
    assert got.as_dict()["machine_id"] == "samos"


# -- pending helper and the CLI ----------------------------------------------

def test_pending_event_shape():
    ev = pending_event("orca", "rm", "/tmp/x", reason="needs approval", machine_id="m")
    assert (ev.kind, ev.outcome) == (KIND_DECISION, OUTCOME_PENDING)
    assert ev.detail == {"type": "policy_decision", "reason": "needs approval"}


def test_pending_queue_reads_pending_and_honours_resolutions(tmp_path):
    store = Store(tmp_path / "l.db")
    a = pending_event("orca", "rm", "/tmp/a")
    b = pending_event("orca", "rm", "/tmp/b")
    store.record(a)
    store.record(b)
    assert {r["id"] for r in query.pending_decisions(store)} == {a.id, b.id}
    store.record(resolution_event(a, OUTCOME_DENIED))
    assert [r["id"] for r in query.pending_decisions(store)] == [b.id]


def test_a_resolution_cannot_be_pending():
    with pytest.raises(ValueError):
        resolution_event("x", OUTCOME_PENDING, host="orca")


def test_cli_shows_machine_and_instance(tmp_path, capsys):
    db = tmp_path / "l.db"
    store = Store(db)
    store.record(AgentEvent(host="orca", kind=KIND_TOOL_CALL, tool="Edit",
                            machine_id="samos", instance_id="i1", cost_usd=0.0, cost_source="table"))
    store.record(pending_event("orca", "rm", "/tmp/a", machine_id="samos", instance_id="i1"))

    assert ledger_main(["--db", str(db), "tail"]) == 0
    assert "[samos/i1]" in capsys.readouterr().out

    assert ledger_main(["--db", str(db), "pending"]) == 1
    out = capsys.readouterr().out
    assert "1 actions waiting" in out and "[samos/i1]" in out

    assert ledger_main(["--db", str(db), "report", "--days", "1"]) == 0
    out = capsys.readouterr().out
    assert "by machine" in out and "samos" in out

    assert ledger_main(["--db", str(db), "json"]) == 0
    out = capsys.readouterr().out
    assert '"machine_id": "samos"' in out and '"instance_id": "i1"' in out


def test_cli_without_ids_prints_no_brackets(tmp_path, capsys):
    db = tmp_path / "l.db"
    Store(db).record(AgentEvent(host="orca", kind=KIND_TOOL_CALL, tool="Edit"))
    ledger_main(["--db", str(db), "tail"])
    out = capsys.readouterr().out
    assert "[" not in out
    ledger_main(["--db", str(db), "report"])
    assert "by machine" not in capsys.readouterr().out
    assert Policy  # imported for parity with the policy tests


def test_vocabulary_is_documented_in_code():
    from agent_ledger import event

    assert event.DETAIL_TYPES == {
        "resource_sample", "file_event", "shell_cmd", "window_focus", "policy_decision", "limit",
    }
    assert len(event.KINDS) == 4
