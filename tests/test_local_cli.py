"""Tests for lhassa.agents.local_cli — argv construction, detection, graceful absence."""

from pathlib import Path

import pytest

from lhassa.agents import local_cli


# ── argv construction per CLI ───────────────────────────────────────────────────

def test_build_argv_claude():
    assert local_cli.build_argv("claude", "do work") == ["claude", "--print", "do work"]


def test_build_argv_codex():
    assert local_cli.build_argv("codex", "do work") == ["codex", "exec", "do work"]


def test_build_argv_opencode():
    assert local_cli.build_argv("opencode", "do work") == ["opencode", "run", "do work"]


def test_build_argv_unknown_raises():
    with pytest.raises(ValueError, match="Unknown AI CLI"):
        local_cli.build_argv("nope", "task")


def test_cli_binary():
    assert local_cli.cli_binary("claude") == "claude"
    assert local_cli.cli_binary("codex") == "codex"
    assert local_cli.cli_binary("opencode") == "opencode"


# ── auto-detection ──────────────────────────────────────────────────────────────

def test_detect_explicit_env(monkeypatch):
    monkeypatch.setenv("LHASSA_AI_CLI", "Codex")
    assert local_cli.detect_ai_cli() == "codex"


def test_detect_first_installed(monkeypatch):
    monkeypatch.delenv("LHASSA_AI_CLI", raising=False)
    # Only opencode is "installed".
    monkeypatch.setattr(
        local_cli.shutil, "which",
        lambda name: "/usr/bin/opencode" if name == "opencode" else None,
    )
    assert local_cli.detect_ai_cli() == "opencode"
    assert local_cli.available_clis() == ["opencode"]
    assert local_cli.is_installed("opencode") is True
    assert local_cli.is_installed("claude") is False


def test_detect_priority_order(monkeypatch):
    monkeypatch.delenv("LHASSA_AI_CLI", raising=False)
    # Both codex and opencode installed → priority picks codex over opencode.
    monkeypatch.setattr(
        local_cli.shutil, "which",
        lambda name: "/usr/bin/x" if name in ("codex", "opencode") else None,
    )
    assert local_cli.detect_ai_cli() == "codex"


def test_detect_falls_back_to_claude_when_none(monkeypatch):
    monkeypatch.delenv("LHASSA_AI_CLI", raising=False)
    monkeypatch.setattr(local_cli.shutil, "which", lambda name: None)
    assert local_cli.detect_ai_cli() == "claude"
    assert local_cli.available_clis() == []


# ── graceful handling when the chosen CLI is absent ─────────────────────────────

@pytest.mark.asyncio
async def test_run_returns_message_when_cli_absent(monkeypatch):
    monkeypatch.setattr(local_cli.shutil, "which", lambda name: None)

    called = {"create": False}

    async def fake_create_subprocess_exec(*args, **kwargs):
        called["create"] = True
        raise AssertionError("subprocess must not be launched when CLI is absent")

    monkeypatch.setattr(
        local_cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    agent = local_cli.LocalCliAgent(cli="codex")
    result = await agent.run("do work")

    assert called["create"] is False
    assert "not installed" in result
    assert "codex" in result


@pytest.mark.asyncio
async def test_run_returns_message_for_unknown_cli(monkeypatch):
    agent = local_cli.LocalCliAgent(cli="bogus")
    result = await agent.run("do work")
    assert "Unknown AI CLI" in result


# ── subprocess invocation + streaming (mocked) ──────────────────────────────────

class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def readline(self):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProc:
    def __init__(self, stdout, stderr, returncode=0):
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self.returncode = returncode
        self.killed = False

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.mark.asyncio
async def test_run_builds_correct_argv_and_streams(monkeypatch):
    monkeypatch.setattr(local_cli.shutil, "which", lambda name: "/usr/bin/" + name)

    captured = {}
    events = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["argv"] = list(args)
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc([b"line one\n", b"line two\n", b""], [b""])

    async def fake_broadcast(event):
        events.append(event)

    monkeypatch.setattr(
        local_cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(local_cli.state, "broadcast", fake_broadcast)

    # No OS hint → prompt equals the task, so argv is easy to assert.
    agent = local_cli.LocalCliAgent(cli="codex", hint="")
    result = await agent.run("do work")

    assert captured["argv"] == ["codex", "exec", "do work"]
    assert result == "line one\nline two"
    token_events = [e for e in events if e["type"] == "agent_token"]
    assert [e["token"] for e in token_events] == ["line one\n", "line two\n"]
    assert any(e["type"] == "agent_done" for e in events)


@pytest.mark.asyncio
async def test_run_reports_nonzero_exit(monkeypatch):
    monkeypatch.setattr(local_cli.shutil, "which", lambda name: "/usr/bin/" + name)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc([b""], [b"boom\n", b""], returncode=2)

    async def fake_broadcast(event):
        pass

    monkeypatch.setattr(
        local_cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(local_cli.state, "broadcast", fake_broadcast)

    agent = local_cli.LocalCliAgent(cli="claude", hint="")
    result = await agent.run("do work")
    assert "error" in result.lower()
    assert "boom" in result


@pytest.mark.asyncio
async def test_invoke_passes_agent_dir_as_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(local_cli.shutil, "which", lambda name: "/usr/bin/" + name)

    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc([b"ok\n", b""], [b""])

    async def fake_broadcast(event):
        pass

    monkeypatch.setattr(
        local_cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(local_cli.state, "broadcast", fake_broadcast)

    agent = local_cli.LocalCliAgent(cli="opencode", hint="")
    run = local_cli.AgentRun(id="rid", name="x", status="running")
    await agent._invoke("rid", run, "task", Path(tmp_path))

    assert captured["cwd"] == str(tmp_path)
