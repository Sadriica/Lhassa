"""
Tests for Phase-4 slice 4.A — the HarnessAdapter seam, the PID/stream runner, and
the router's delegate axis. Fully offline: subprocess and shutil.which are mocked,
the brain DB is a tmp file. NO real subprocess, network, or LLM — except the one
stderr-drain deadlock test below, which needs a real OS pipe (a fake in-memory
stream has no buffer limit and can never reproduce the deadlock).
"""

import asyncio
import sys

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.harness import adapter as ad
from lhassa.harness import runner
from lhassa.harness.adapter import (
    ClaudeCodeAdapter,
    CodexAdapter,
    HarnessAdapter,
    OpenCodeAdapter,
    RunEvent,
    available_adapters,
    get_adapter,
)


@pytest.fixture(autouse=True)
def _clean_jobs():
    """Keep the module-level live-job registry isolated per test."""
    runner._JOBS.clear()
    yield
    runner._JOBS.clear()


# ── fake subprocess plumbing (mirrors test_local_cli) ────────────────────────────

class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def readline(self):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProc:
    def __init__(self, stdout, stderr=(b"",), returncode=0, pid=4321):
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self.returncode = returncode
        self.pid = pid
        self.killed = False

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


async def _init_db(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


def _all_installed(monkeypatch):
    monkeypatch.setattr(ad.shutil, "which", lambda name: "/usr/bin/" + name)


def _none_installed(monkeypatch):
    monkeypatch.setattr(ad.shutil, "which", lambda name: None)


# ── build_command per CLI ────────────────────────────────────────────────────────

def test_build_command_opencode():
    assert OpenCodeAdapter().build_command("do x") == [
        "opencode", "run", "--format", "json", "do x",
    ]


def test_build_command_claude_stream_json_default():
    cmd = ClaudeCodeAdapter().build_command("do x")
    assert cmd == ["claude", "--print", "--output-format", "stream-json",
                   "--verbose", "do x"]


def test_build_command_claude_plain():
    cmd = ClaudeCodeAdapter().build_command("do x", stream_json=False)
    assert cmd == ["claude", "--print", "do x"]


def test_build_command_codex():
    assert CodexAdapter().build_command("do x") == ["codex", "exec", "--json", "do x"]


# ── static facts + protocol conformance ──────────────────────────────────────────

def test_static_facts():
    oc, cc, cx = OpenCodeAdapter(), ClaudeCodeAdapter(), CodexAdapter()
    assert (oc.name, oc.model_locked, oc.billing) == ("opencode", False, "metered")
    assert (cc.name, cc.model_locked, cc.billing) == ("claude", True, "subscription")
    assert (cx.name, cx.model_locked, cx.billing) == ("codex", True, "subscription")


def test_adapters_satisfy_protocol():
    for a in (OpenCodeAdapter(), ClaudeCodeAdapter(), CodexAdapter()):
        assert isinstance(a, HarnessAdapter)


# ── parse_line: stream-json + plain ──────────────────────────────────────────────

def test_parse_line_plain_text():
    ev = OpenCodeAdapter().parse_line("hello world\n")
    assert ev == RunEvent(kind="text", payload="hello world")


def test_parse_line_blank_is_none():
    assert ClaudeCodeAdapter().parse_line("   \n") is None
    assert ClaudeCodeAdapter().parse_line("") is None


def test_parse_line_stream_json_assistant_text():
    line = ('{"type":"assistant","message":{"content":'
            '[{"type":"text","text":"escribí el archivo"}]}}')
    ev = ClaudeCodeAdapter().parse_line(line)
    assert ev.kind == "text"
    assert ev.payload == "escribí el archivo"


def test_parse_line_stream_json_result_cost():
    line = '{"type":"result","subtype":"success","total_cost_usd":0.0123}'
    ev = ClaudeCodeAdapter().parse_line(line)
    assert ev.kind == "cost"
    assert ev.cost_usd == pytest.approx(0.0123)


def test_parse_line_stream_json_error():
    ev = ClaudeCodeAdapter().parse_line('{"type":"result","is_error":true,"error":"boom"}')
    assert ev.kind in ("error", "cost")  # is_error → error wins only when no cost
    ev2 = ClaudeCodeAdapter().parse_line('{"type":"error","message":"nope"}')
    assert ev2.kind == "error"
    assert "nope" in ev2.payload


def test_parse_line_strips_ansi():
    ev = CodexAdapter().parse_line("\x1b[2mdim line\x1b[0m\n")
    assert ev == RunEvent(kind="text", payload="dim line")


# ── available() reflects which are installed ─────────────────────────────────────

def test_available_reflects_which(monkeypatch):
    monkeypatch.setattr(
        ad.shutil, "which",
        lambda name: "/usr/bin/opencode" if name == "opencode" else None,
    )
    assert OpenCodeAdapter().available() is True
    assert ClaudeCodeAdapter().available() is False
    names = [a.name for a in available_adapters()]
    assert names == ["opencode"]


def test_get_adapter_and_unknown():
    assert get_adapter("CLAUDE").name == "claude"
    with pytest.raises(ValueError, match="Unknown harness"):
        get_adapter("bogus")


# ── keystone registry: introspection + drop-in seam ──────────────────────────────

def test_adapters_registry_exposes_names_in_preference_order():
    # The capabilities slice introspects ``ADAPTERS`` (a keystone Registry).
    from lhassa.registry import Registry

    assert isinstance(ad.ADAPTERS, Registry)
    assert ad.ADAPTERS.names() == ["opencode", "claude", "codex"]
    # The legacy ``_ADAPTERS`` mapping is a live read-only view of the same store.
    assert set(ad._ADAPTERS) == {"opencode", "claude", "codex"}
    assert ad._ADAPTERS["opencode"].name == "opencode"


def test_register_adapter_dropin_and_resolves(monkeypatch):
    """A plugin can register a new adapter without editing core, then resolve it."""

    class DummyAdapter(ad._BaseAdapter):
        name = "dummy"
        model_locked = False
        billing = "free"
        binary = "dummy-cli"

        def build_command(self, prompt, **opts):
            return ["dummy-cli", prompt]

    try:
        ad.register_adapter("dummy", DummyAdapter())
        got = get_adapter("DUMMY")  # case-insensitive
        assert got.name == "dummy"
        assert "dummy" in ad.ADAPTERS.names()
        # available() reflects shutil.which on the registered instance.
        monkeypatch.setattr(ad.shutil, "which", lambda n: "/x" if n == "dummy-cli" else None)
        assert got in available_adapters()
    finally:
        ad.ADAPTERS.unregister("dummy")


def test_register_adapter_override_protection():
    with pytest.raises(ValueError, match="already registered"):
        ad.register_adapter("opencode", OpenCodeAdapter())
    # override=True replaces without raising, then restore the original.
    original = get_adapter("opencode")
    try:
        ad.register_adapter("opencode", CodexAdapter(), override=True)
        assert get_adapter("opencode").name == "codex"
    finally:
        ad.register_adapter("opencode", original, override=True)
        assert get_adapter("opencode").name == "opencode"


# ── runner.launch — records a running job + streams, without blocking ────────────

@pytest.mark.asyncio
async def test_launch_records_running_job_and_streams(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    _all_installed(monkeypatch)

    events = []

    async def fake_broadcast(ev):
        events.append(ev)

    captured = {}

    async def fake_create(*args, **kwargs):
        captured["argv"] = list(args)
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc(
            stdout=[
                b'{"type":"assistant","message":{"content":[{"type":"text","text":"trabajando"}]}}\n',
                b'{"type":"result","subtype":"success","total_cost_usd":0.05}\n',
                b"",
            ],
            pid=999,
        )

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runner.state, "broadcast", fake_broadcast)

    adapter = ClaudeCodeAdapter()
    job_id = await runner.launch(adapter, "haz X", instance_id="inst_personal")

    # launch returned immediately with a job_id and recorded a RUNNING job (PID set).
    assert job_id.startswith("j_")
    assert captured["argv"][0] == "claude"
    rows = await _jobs(db)
    assert len(rows) == 1
    assert rows[0]["estado"] == "running"
    assert rows[0]["pid"] == 999
    assert rows[0]["harness"] == "claude"

    # The streaming task is live (fire-and-forget) — let it finish, then supervise.
    await runner._JOBS[job_id].task
    assert any(e["type"] == "agent_started" for e in events)
    assert any(e["type"] == "agent_token" for e in events)


# ── runner.supervise — reaps to done with accumulated cost ───────────────────────

@pytest.mark.asyncio
async def test_supervise_reaps_done_with_cost(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    _all_installed(monkeypatch)

    async def fake_broadcast(ev):
        pass

    async def fake_create(*args, **kwargs):
        return _FakeProc(
            stdout=[
                b'{"type":"result","total_cost_usd":0.25}\n',
                b"",
            ],
            returncode=0,
            pid=777,
        )

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runner.state, "broadcast", fake_broadcast)

    job_id = await runner.launch(OpenCodeAdapter(), "go", instance_id="inst_personal")
    await runner._JOBS[job_id].task  # wait for stream to finish

    summary = await runner.supervise("inst_personal")
    assert len(summary) == 1
    assert summary[0]["job_id"] == job_id
    assert summary[0]["estado"] == "done"
    assert summary[0]["costo_usd"] == pytest.approx(0.25)

    rows = await _jobs(db)
    assert rows[0]["estado"] == "done"
    assert rows[0]["costo_usd"] == pytest.approx(0.25)
    assert rows[0]["terminado_en"]

    # Reaped — gone from the live registry; a second supervise is a no-op.
    assert job_id not in runner._JOBS
    assert await runner.supervise("inst_personal") == []


@pytest.mark.asyncio
async def test_supervise_marks_error_on_nonzero_exit(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    _all_installed(monkeypatch)

    async def fake_broadcast(ev):
        pass

    async def fake_create(*args, **kwargs):
        return _FakeProc(stdout=[b"some output\n", b""], returncode=2, pid=1)

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runner.state, "broadcast", fake_broadcast)

    job_id = await runner.launch(CodexAdapter(), "go", instance_id="inst_personal")
    await runner._JOBS[job_id].task
    await runner.supervise("inst_personal")

    rows = await _jobs(db)
    assert rows[0]["estado"] == "error"


# ── runner._stream — draining stderr concurrently avoids a pipe-full deadlock ────

@pytest.mark.asyncio
async def test_stream_drains_stderr_without_deadlock(monkeypatch):
    """A CLI that floods stderr must not block the parent forever.

    Uses a REAL subprocess (a fake in-memory stream has no OS pipe buffer, so it
    can never reproduce this). The child writes well over the typical 64KB pipe
    buffer to stderr (in newline-delimited chunks, so a single ``readline()``
    never trips the stream reader's own line-length limit) *before* writing
    anything to stdout or exiting. If the
    parent only drains stdout, the child's stderr write() never returns and
    ``_stream`` hangs past its own ``timeout`` (the timeout only bounds our own
    await, not a blocked child process) — so this test itself is wrapped in a
    short ``asyncio.wait_for`` as a safety net, and should complete well within
    it once stderr is drained concurrently.
    """

    async def fake_broadcast(ev):
        pass

    monkeypatch.setattr(runner.state, "broadcast", fake_broadcast)

    code = (
        "import sys\n"
        "for _ in range(400):\n"
        "    sys.stderr.write('x' * 1000 + '\\n')\n"
        "sys.stderr.flush()\n"
        "print('done')\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    class _TextAdapter:
        name = "textcli"

        def parse_line(self, raw):
            clean = raw.strip()
            return RunEvent(kind="text", payload=clean) if clean else None

    from lhassa.state import AgentRun

    run = AgentRun(id="j_stderrtest", name="test", status="running", task="test")
    rj = runner._RunningJob(
        job_id="j_stderrtest", instance_id="inst_personal",
        harness="textcli", proc=proc, run=run,
    )

    # A big internal timeout: if the fix is broken, the deadlock would outlast
    # it — this test's own wait_for is the thing that actually bounds the run.
    await asyncio.wait_for(runner._stream(_TextAdapter(), rj, timeout=30), timeout=5)

    assert rj.estado == "done"
    assert "done" in rj.run.output


# ── unavailable harness degrades to an error job (no subprocess, no raise) ────────

@pytest.mark.asyncio
async def test_launch_unavailable_records_error_job(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    _none_installed(monkeypatch)

    async def fake_broadcast(ev):
        pass

    async def fake_create(*args, **kwargs):
        raise AssertionError("subprocess must not be launched for an absent CLI")

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runner.state, "broadcast", fake_broadcast)

    job_id = await runner.launch(ClaudeCodeAdapter(), "go", instance_id="inst_personal")
    rows = await _jobs(db)
    assert len(rows) == 1
    assert rows[0]["estado"].startswith("error")
    assert "unavailable" in rows[0]["estado"]
    assert job_id not in runner._JOBS  # nothing streaming


# ── pick_harness — available + override ──────────────────────────────────────────

def test_pick_harness_prefers_metered_over_subscription(monkeypatch):
    from lhassa.config import Config
    from lhassa.llm import router

    monkeypatch.delenv("LHASSA_HARNESS", raising=False)
    cfg = Config()
    cfg.harness = ""
    # All three "available" → opencode (metered) beats the subscription pair.
    chosen = router.pick_harness(
        config=cfg, available=[OpenCodeAdapter(), ClaudeCodeAdapter(), CodexAdapter()]
    )
    assert chosen.name == "opencode"


def test_pick_harness_subscription_when_only_option(monkeypatch):
    from lhassa.config import Config
    from lhassa.llm import router

    monkeypatch.delenv("LHASSA_HARNESS", raising=False)
    cfg = Config()
    cfg.harness = ""
    chosen = router.pick_harness(config=cfg, available=[CodexAdapter()])
    assert chosen.name == "codex"


def test_pick_harness_none_when_empty(monkeypatch):
    from lhassa.config import Config
    from lhassa.llm import router

    monkeypatch.delenv("LHASSA_HARNESS", raising=False)
    cfg = Config()
    cfg.harness = ""
    assert router.pick_harness(config=cfg, available=[]) is None


def test_pick_harness_respects_override(monkeypatch):
    from lhassa.config import Config
    from lhassa.llm import router

    cfg = Config()
    cfg.harness = "codex"
    # opencode is "available" and cheaper, but the override wins.
    chosen = router.pick_harness(
        config=cfg, available=[OpenCodeAdapter(), ClaudeCodeAdapter(), CodexAdapter()]
    )
    assert chosen.name == "codex"


def test_pick_harness_override_via_env(monkeypatch):
    from lhassa.config import Config
    from lhassa.llm import router

    monkeypatch.setenv("LHASSA_HARNESS", "claude")
    cfg = Config()
    cfg.harness = ""  # not set on the object; env should still be honored
    chosen = router.pick_harness(config=cfg, available=[OpenCodeAdapter()])
    assert chosen.name == "claude"


# ── helpers ──────────────────────────────────────────────────────────────────────

async def _jobs(db):
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM jobs ORDER BY creado_en") as cur:
            return [dict(r) for r in await cur.fetchall()]
