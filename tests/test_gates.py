"""Tests for lhassa.agents.gates — the four gates of self-improvement 2b.

Offline: the candidate is a harmless ``bash -c`` snippet; correctness gates use
``true``/``false`` (or a mocked subprocess seam); the metric is a plain Python
function; the brain DB is redirected to a temp file. No real network.
"""

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.agents.gates import (
    GATE_CORRECTA,
    GATE_MEJOR,
    GATE_SEGURA,
    promote,
    retire,
    run_gates,
    should_retire,
)


@pytest_asyncio.fixture
async def brain(tmp_path, monkeypatch):
    """Redirect the grande to a temp DB and create the schema."""
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


# A candidate that is harmless and exits cleanly under the sandbox.
HARMLESS = "echo candidate-ran"


# ── run_gates ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate1_passes_for_harmless_candidate():
    results = await run_gates(HARMLESS)
    # No tests_spec → stops after gate 2 fails, but gate 1 must have passed.
    assert results[0].gate == GATE_SEGURA
    assert results[0].passed is True


@pytest.mark.asyncio
async def test_all_three_gates_pass():
    results = await run_gates(
        HARMLESS,
        tests_spec={"type": "command", "cmd": "true"},
        metric_fn=lambda *_: 5.0,   # lower is better; 5 < 10 → mejor
        baseline=10.0,
    )
    assert [r.gate for r in results] == [GATE_SEGURA, GATE_CORRECTA, GATE_MEJOR]
    assert all(r.passed for r in results)


@pytest.mark.asyncio
async def test_stops_at_gate2_and_does_not_run_gate3():
    results = await run_gates(
        HARMLESS,
        tests_spec={"type": "command", "cmd": "false"},  # exit 1 → correcta fails
        metric_fn=lambda *_: 1.0,
        baseline=10.0,
    )
    assert [r.gate for r in results] == [GATE_SEGURA, GATE_CORRECTA]
    assert results[0].passed is True
    assert results[1].passed is False
    # Gate 3 (mejor) was never evaluated.
    assert GATE_MEJOR not in {r.gate for r in results}


@pytest.mark.asyncio
async def test_gate3_fails_when_metric_does_not_beat_baseline():
    results = await run_gates(
        HARMLESS,
        tests_spec={"type": "command", "cmd": "true"},
        metric_fn=lambda *_: 15.0,  # 15 > 10 → worse → fails
        baseline=10.0,
    )
    assert results[-1].gate == GATE_MEJOR
    assert results[-1].passed is False


@pytest.mark.asyncio
async def test_gate3_fails_without_baseline():
    results = await run_gates(
        HARMLESS,
        tests_spec={"type": "command", "cmd": "true"},
        metric_fn=lambda *_: 1.0,
        baseline=None,  # cannot prove "better"
    )
    assert results[-1].gate == GATE_MEJOR
    assert results[-1].passed is False
    assert "baseline" in results[-1].detail


@pytest.mark.asyncio
async def test_verify_subprocess_seam_is_mocked(monkeypatch):
    # Demonstrate the verify.py seam: replace the subprocess, no real spawn.
    import subprocess

    from lhassa.agents import verify

    def fake_run(self, cmd):
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(verify.CommandVerifier, "_run", fake_run)
    results = await run_gates(
        HARMLESS,
        tests_spec={"type": "command", "cmd": "anything"},
        metric_fn=lambda *_: 1.0,
        baseline=2.0,
    )
    assert all(r.passed for r in results)


# ── promote (updates the tools table) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_promote_graduates_and_records_tool(brain):
    out = await promote(
        "inst_test",
        "optimizar_calendario",
        HARMLESS,
        domain="optimizacion",
        tests_spec={"type": "command", "cmd": "true"},
        metric_fn=lambda *_: 4.0,
        baseline=9.0,
    )
    assert out["graduated"] is True
    assert out["estado"] == "graduada"

    row = await brain.get_tool("inst_test", "optimizar_calendario")
    assert row["estado"] == "graduada"
    assert row["dominio"] == "optimizacion"
    assert row["metrica_baseline"] == 9.0
    assert row["metrica_actual"] == 4.0


@pytest.mark.asyncio
async def test_promote_rejects_on_gate2_failure_with_reason(brain):
    out = await promote(
        "inst_test",
        "tool_malo",
        HARMLESS,
        tests_spec={"type": "command", "cmd": "false"},  # correcta fails
        metric_fn=lambda *_: 1.0,
        baseline=9.0,
    )
    assert out["graduated"] is False
    assert out["estado"] == "rechazada"
    assert GATE_CORRECTA in out["reason"]
    # Gate 3 never ran.
    assert GATE_MEJOR not in {g.gate for g in out["gates"]}

    row = await brain.get_tool("inst_test", "tool_malo")
    assert row["estado"] == "rechazada"


@pytest.mark.asyncio
async def test_promote_rejects_on_gate3_failure(brain):
    out = await promote(
        "inst_test",
        "tool_no_mejor",
        HARMLESS,
        tests_spec={"type": "command", "cmd": "true"},
        metric_fn=lambda *_: 99.0,  # worse than baseline
        baseline=9.0,
    )
    assert out["estado"] == "rechazada"
    assert GATE_MEJOR in out["reason"]


@pytest.mark.asyncio
async def test_promote_deny_by_default_without_tests_or_metric(brain):
    out = await promote("inst_test", "tool_vacio", HARMLESS)
    assert out["graduated"] is False
    assert out["estado"] == "rechazada"
    row = await brain.get_tool("inst_test", "tool_vacio")
    assert row["estado"] != "graduada"


# ── gate 4: retiro ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retire_demotes_graduated_tool_to_cuarentena(brain):
    await promote(
        "inst_test",
        "tool_graduada",
        HARMLESS,
        tests_spec={"type": "command", "cmd": "true"},
        metric_fn=lambda *_: 1.0,
        baseline=9.0,
    )
    res = await retire("inst_test", "tool_graduada", reason="prod failures")
    assert res["estado"] == "cuarentena"
    row = await brain.get_tool("inst_test", "tool_graduada")
    assert row["estado"] == "cuarentena"


def test_should_retire_predicate():
    # Graduated but unused → retire.
    assert should_retire({"estado": "graduada", "usos": 0, "fallos": 0}) is True
    # Graduated, high prod failure rate over enough uses → retire.
    assert should_retire({"estado": "graduada", "usos": 4, "fallos": 3}) is True
    # Graduated, healthy → keep.
    assert should_retire({"estado": "graduada", "usos": 10, "fallos": 0}) is False
    # Not graduated → never retired here.
    assert should_retire({"estado": "cuarentena", "usos": 0, "fallos": 0}) is False
    assert should_retire({}) is False


@pytest.mark.asyncio
async def test_bump_tool_usage_feeds_retiro(brain):
    await promote(
        "inst_test",
        "tool_usada",
        HARMLESS,
        tests_spec={"type": "command", "cmd": "true"},
        metric_fn=lambda *_: 1.0,
        baseline=9.0,
    )
    await brain.bump_tool_usage("inst_test", "tool_usada", ok=False)
    await brain.bump_tool_usage("inst_test", "tool_usada", ok=False)
    await brain.bump_tool_usage("inst_test", "tool_usada", ok=False)
    row = await brain.get_tool("inst_test", "tool_usada")
    assert row["usos"] == 3
    assert row["fallos"] == 3
    assert should_retire(row) is True
