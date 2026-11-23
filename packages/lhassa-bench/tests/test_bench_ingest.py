# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ingesting experiment result files into bench_runs: mapping, idempotence, audit flag."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from lhassa.bench import ingest

STAMP = "2026-11-24T00:00:00+00:00"


def _write(path: Path, rows: list) -> Path:
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n")
    return path


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())
    return db


def test_a_harness_row_maps_to_fixture_subject_metric_and_duration(tmp_path):
    f = _write(tmp_path / "fase3_corrected_20260805.jsonl", [
        {"harness": "claude", "task": "hello", "seed": 0, "passed": True, "tokens_in": 100,
         "tokens_out": 20, "tokens_cache": 900, "latency_ms": 1500, "detail": "OK\n"},
    ])
    rows, skipped = ingest.rows_from_jsonl(f, "inst", created_at=STAMP)
    assert skipped == 0 and len(rows) == 1
    r = rows[0]
    assert r["fixture_id"] == "blackbox:fase3:hello"
    assert r["subject"] == "blackbox/fase3/claude"
    assert r["passed"] is True and r["metric"] == 120.0 and r["duration_ms"] == 1500
    d = json.loads(r["detail"])
    assert d["tokens_cache"] == 900 and d["seed"] == 0 and d["note"] == "OK"
    assert d["audit"] == "post-audit" and d["source"] == f.name


def test_arm_scale_and_extra_fields_are_kept(tmp_path):
    f = _write(tmp_path / "fase6_cliff_corrected.jsonl", [
        {"scale": "36k", "arm": "b", "passed": False, "tokens_in": 1, "tokens_out": 2,
         "tokens_cache": 0, "latency_ms": 5, "detail": ""},
    ])
    r = ingest.rows_from_jsonl(f, "inst", created_at=STAMP)[0][0]
    assert r["fixture_id"] == "blackbox:fase6_cliff:36k"
    assert r["subject"] == "blackbox/fase6_cliff/b"
    h = tmp_path / "fase8_recorder.jsonl"
    _write(h, [{"arm": "e", "task_id": "svc", "seed": 1, "passed": True, "tokens_in": 0,
                "tokens_out": 0, "tokens_cache": 0, "latency_ms": 9, "n_events": 4, "detail": ""}])
    d = json.loads(ingest.rows_from_jsonl(h, "inst", created_at=STAMP)[0][0]["detail"])
    assert d["n_events"] == 4 and d["seed"] == 1


@pytest.mark.parametrize(
    "name, flag",
    [("fase3_20260805.jsonl", "pre-audit"), ("gate_20260805.jsonl", "pre-audit"),
     ("gate_corrected_20260805.jsonl", "post-audit"), ("fase7_hidden.jsonl", "post-audit")],
)
def test_files_the_audit_invalidated_are_flagged(tmp_path, name, flag):
    f = _write(tmp_path / name, [{"arm": "a", "task": "t", "passed": True}])
    assert json.loads(ingest.rows_from_jsonl(f, "i", created_at=STAMP)[0][0]["detail"])["audit"] == flag


def test_bad_lines_are_skipped_and_counted_not_raised(tmp_path):
    f = _write(tmp_path / "x.jsonl", ["not json", "[1,2]", {"arm": "a", "task": "t", "passed": True}, ""])
    rows, skipped = ingest.rows_from_jsonl(f, "i", created_at=STAMP)
    assert len(rows) == 1 and skipped == 2


def test_ids_are_deterministic(tmp_path):
    f = _write(tmp_path / "a.jsonl", [{"arm": "a", "task": "t", "passed": True}] * 2)
    a = [r["id"] for r in ingest.rows_from_jsonl(f, "i", created_at=STAMP)[0]]
    b = [r["id"] for r in ingest.rows_from_jsonl(f, "i", created_at="other")[0]]
    assert a == b and len(set(a)) == 2  # the same line twice still gets two ids (line number)


def test_ingesting_twice_inserts_nothing_new(brain, tmp_path):
    f = _write(tmp_path / "fase7_hidden.jsonl", [
        {"arm": "a", "task_id": "hidden_spec", "seed": 0, "passed": True, "tokens_in": 5,
         "tokens_out": 5, "tokens_cache": 0, "latency_ms": 1, "spec_carried": "spec-in-prompt", "detail": ""},
        {"arm": "c", "task_id": "hidden_spec", "seed": 0, "passed": False, "tokens_in": 6,
         "tokens_out": 6, "tokens_cache": 0, "latency_ms": 2, "spec_carried": "none", "detail": ""},
    ])
    first = asyncio.run(ingest.ingest_files([f], "inst_t", created_at=STAMP))
    again = asyncio.run(ingest.ingest_files([f], "inst_t", created_at=STAMP))
    assert first["inserted"] == 2 and first["duplicates"] == 0
    assert again["inserted"] == 0 and again["duplicates"] == 2
    rows = asyncio.run(brain.list_bench_runs("inst_t", fixture_id="blackbox:fase7_hidden:hidden_spec", limit=10))
    assert len(rows) == 2 and {r["passed"] for r in rows} == {0, 1}


def test_the_real_blackbox_results_ingest_cleanly(brain):
    results = Path(__file__).resolve().parents[3] / "research" / "results"
    files = sorted(results.glob("*.jsonl"))
    if not files:
        pytest.skip("research/results not present")
    total = asyncio.run(ingest.ingest_files(files, "inst_t", created_at=STAMP))
    assert total["skipped"] == 0 and total["inserted"] == total["rows"] > 200
