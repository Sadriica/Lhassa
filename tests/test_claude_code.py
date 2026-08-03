"""Tests for lhassa.agents.claude_code."""

from pathlib import Path

import pytest


class _FakeStream:
    def __init__(self, chunks, on_eof=None):
        self._chunks = list(chunks)
        self._on_eof = on_eof

    async def readline(self):
        if self._chunks:
            chunk = self._chunks.pop(0)
            if not chunk and self._on_eof is not None:
                self._on_eof()
                self._on_eof = None
            return chunk
        return b""


class _FakeProc:
    def __init__(self):
        self.stderr_drained = False
        self.returncode = 1
        self.stdout = _FakeStream([b"hello\n", b""])
        self.stderr = _FakeStream([b"warn\n", b""], on_eof=self._mark_stderr_drained)

    def _mark_stderr_drained(self):
        self.stderr_drained = True

    async def wait(self):
        if not self.stderr_drained:
            raise AssertionError("stderr was not drained before wait")
        return self.returncode


@pytest.mark.asyncio
async def test_invoke_drains_stderr_before_wait(monkeypatch):
    from lhassa.agents.claude_code import ClaudeCliAgent
    from lhassa.state import AgentRun, state

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    events = []

    async def fake_broadcast(event: dict):
        events.append(event)

    monkeypatch.setattr("lhassa.agents.claude_code.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(state, "broadcast", fake_broadcast)

    agent = ClaudeCliAgent(name="code")
    run = AgentRun(id="run1", name="code", status="running")

    with pytest.raises(RuntimeError, match="warn"):
        await agent._invoke("run1", run, "do work", Path.cwd())

    assert run.output == "hello\n"
    assert events == [{"type": "agent_token", "run_id": "run1", "token": "hello\n"}]
