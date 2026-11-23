# SPDX-License-Identifier: AGPL-3.0-or-later
"""The harness recorder, the ``limit`` event and replay determinism. Offline."""

import json
import sys
import types
from pathlib import Path

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.harness import runner
from lhassa.harness.adapter import ClaudeCodeAdapter, CodexAdapter, OpenCodeAdapter, RunEvent
from lhassa.harness.recorder import LedgerSink, RunRecorder, open_recorder
from lhassa.record import read_log
from tests.test_harness import _all_installed, _FakeProc, _init_db

def _config():
    """The live Config class (other tests reload lhassa.config, so never cache it)."""
    import lhassa.config

    return lhassa.config.Config


FIXTURES = Path(__file__).parent / "fixtures" / "harness_transcripts"


def _clock():
    t = iter(x / 10 for x in range(1, 10_000))
    return lambda: next(t)


def _replay(adapter, fixture, out, **kw):
    rec = RunRecorder(out, run_id="r1", harness=adapter.name, clock=_clock(), **kw)
    for raw in (FIXTURES / fixture).read_text().splitlines():
        ev = adapter.parse_line(raw + "\n")
        if ev is not None:
            rec.record(ev)
    rec.close("done")
    return out.read_bytes()


# ── replay determinism (no model, no process) ────────────────────────────────

def test_replaying_a_transcript_twice_gives_identical_logs(tmp_path):
    a = _replay(ClaudeCodeAdapter(), "claude_session.jsonl", tmp_path / "a.jsonl")
    b = _replay(ClaudeCodeAdapter(), "claude_session.jsonl", tmp_path / "b.jsonl")
    assert a == b


def test_a_session_records_tools_targets_and_cost_without_text(tmp_path):
    out = tmp_path / "s.jsonl"
    _replay(ClaudeCodeAdapter(), "claude_session.jsonl", out)
    events, torn = read_log(out)
    assert not torn
    kinds = [e["t"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "end"
    tools = [(e["tool"], e["target"]) for e in events if e["t"] == "tool"]
    assert tools == [("Read", "/work/src/app.py"), ("Bash", "pytest -q")]
    assert [e["cost_usd"] for e in events if e["t"] == "cost"] == [0.12]
    assert not any("text" in e for e in events)  # metadata by default


def test_content_is_kept_only_when_asked_and_is_scrubbed(tmp_path):
    out = tmp_path / "c.jsonl"
    rec = RunRecorder(out, run_id="r", harness="x", content=True, clock=_clock())
    rec.record(RunEvent(kind="text", payload="mail bob@example.com about it"))
    rec.record(RunEvent(kind="text", payload="word " * 1000))
    rec.close()
    texts = [e["text"] for e in read_log(out)[0] if "text" in e]
    assert "bob@example.com" not in texts[0] and "<email_1>" in texts[0]
    assert len(texts[1]) == 2000


def test_a_never_keep_target_is_withheld_and_its_text_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = tmp_path / "w.jsonl"
    rec = RunRecorder(out, run_id="r", harness="x", content=True, clock=_clock())
    rec.record(RunEvent(kind="tool", payload="Read", tool="Read",
                        target=str(tmp_path / ".ssh" / "id_ed25519")))
    rec.record(RunEvent(kind="text", payload=f"cat {tmp_path}/.ssh/id_rsa"))
    rec.close()
    events = read_log(out)[0]
    tool = next(e for e in events if e["t"] == "tool")
    assert tool["target"] == "<withheld>" and tool["withheld"]
    assert "id_ed25519" not in out.read_text()
    assert not any("text" in e for e in events if e["t"] == "text")


# ── the limit event ──────────────────────────────────────────────────────────

def test_a_limit_result_keeps_its_cost_and_is_flagged():
    ev = ClaudeCodeAdapter().parse_line(
        (FIXTURES / "claude_limit.jsonl").read_text().splitlines()[1] + "\n"
    )
    assert ev.kind == "limit" and ev.cost_usd == 0.4 and "usage limit" in ev.payload


@pytest.mark.parametrize(
    "adapter, raw",
    [
        (ClaudeCodeAdapter(), "Claude AI usage limit reached|1700000000"),
        (CodexAdapter(), "ERROR: You've hit your usage limit. Try again later."),
        (OpenCodeAdapter(), '{"type":"error","error":"429 Too Many Requests"}'),
        (OpenCodeAdapter(), '{"type":"error","message":"rate limit exceeded"}'),
        (CodexAdapter(), "Rate limit reached for requests"),
    ],
)
def test_limit_notices_are_recognised(adapter, raw):
    assert adapter.parse_line(raw + "\n").kind == "limit"


@pytest.mark.parametrize(
    "raw",
    [
        "I will add a rate limit to the API client",
        "The 429 handler retries with backoff, see retry.py",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"adding a rate limit"}]}}',
        '{"type":"error","error":"file not found"}',
    ],
)
def test_prose_about_limits_and_other_errors_are_not_limits(raw):
    assert ClaudeCodeAdapter().parse_line(raw + "\n").kind != "limit"


# ── runner integration ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_launched_job_leaves_a_log_and_a_limit_is_remembered(tmp_path, monkeypatch):
    await _init_db(tmp_path, monkeypatch)
    _all_installed(monkeypatch)
    runner._JOBS.clear()
    monkeypatch.setattr(_config(), "record_runs", True)
    monkeypatch.setattr(_config(), "records_dir", str(tmp_path / "records"))

    async def fake_broadcast(ev):
        pass

    lines = (FIXTURES / "claude_limit.jsonl").read_text().splitlines()

    async def fake_create(*args, **kwargs):
        return _FakeProc(stdout=[ln.encode() + b"\n" for ln in lines] + [b""], pid=7)

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runner.state, "broadcast", fake_broadcast)
    job_id = await runner.launch(ClaudeCodeAdapter(), "do it", instance_id="inst_t")
    rj = runner._JOBS[job_id]
    await rj.task
    assert rj.limit_hit is True and rj.estado == "error"
    summary = await runner.supervise("inst_t")
    assert summary[0]["estado"] == "error"

    events, torn = read_log(tmp_path / "records" / "inst_t" / f"{job_id}.jsonl")
    assert not torn
    assert [e["t"] for e in events] == ["start", "tool", "limit", "end"]
    assert events[-1]["estado"] == "limit"
    runner._JOBS.clear()


@pytest.mark.asyncio
async def test_recording_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(_config(), "record_runs", False)
    monkeypatch.setattr(_config(), "records_dir", str(tmp_path / "records"))
    assert open_recorder("i", "j", "claude") is None
    assert not (tmp_path / "records").exists()


def test_a_recorder_that_cannot_start_never_breaks_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr(_config(), "record_runs", True)
    monkeypatch.setattr(_config(), "records_dir", "/proc/nope/records")
    rec = open_recorder("i", "j", "claude")
    rec.record(RunEvent(kind="text", payload="x"))  # no file, no raise
    rec.close()


# ── the ledger sink ──────────────────────────────────────────────────────────

class _FakeStore:
    def __init__(self):
        self.rows = {}

    def record(self, event):
        self.rows.setdefault(event.id, event)
        return True


def _fake_ledger(monkeypatch):
    mod = types.ModuleType("agent_ledger.event")
    mod.KIND_TOOL_CALL, mod.KIND_LLM_CALL, mod.KIND_NOTE = "tool_call", "llm_call", "note"
    mod.OUTCOME_ERROR, mod.COST_FROM_HOST, mod.COST_UNKNOWN = "error", "host", "unknown"

    class AgentEvent:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    mod.AgentEvent = AgentEvent
    pkg = types.ModuleType("agent_ledger")
    pkg.event = mod
    monkeypatch.setitem(sys.modules, "agent_ledger", pkg)
    monkeypatch.setitem(sys.modules, "agent_ledger.event", mod)


def test_the_ledger_sink_maps_events_and_is_idempotent(tmp_path, monkeypatch):
    _fake_ledger(monkeypatch)
    store = _FakeStore()

    def run(name):
        rec = RunRecorder(tmp_path / name, run_id="r9", harness="claude", clock=_clock(),
                          sinks=[LedgerSink(store)])
        for raw in (FIXTURES / "claude_limit.jsonl").read_text().splitlines():
            ev = ClaudeCodeAdapter().parse_line(raw + "\n")
            rec.record(ev)
        rec.close("limit")

    run("a.jsonl")
    n = len(store.rows)
    run("b.jsonl")  # same run replayed: same ids, nothing new
    assert len(store.rows) == n == 2
    by_kind = sorted(e.kind for e in store.rows.values())
    assert by_kind == ["note", "tool_call"]
    note = next(e for e in store.rows.values() if e.kind == "note")
    assert note.detail == {"type": "limit"} and note.host == "lhassa" and note.session == "r9"


def test_the_ledger_sink_is_inert_without_the_package(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "agent_ledger", None)
    sink = LedgerSink()
    sink({"t": "tool", "run": "r", "seq": 1, "ts": 1.0, "source": "x", "tool": "T"})
    sink({"t": "tool", "run": "r", "seq": 2, "ts": 2.0, "source": "x", "tool": "T"})


def test_log_lines_are_valid_json_objects(tmp_path):
    out = tmp_path / "j.jsonl"
    _replay(ClaudeCodeAdapter(), "claude_session.jsonl", out)
    for line in out.read_text().splitlines():
        assert isinstance(json.loads(line), dict)
