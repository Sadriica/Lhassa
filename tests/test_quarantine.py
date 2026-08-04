"""Tests for lhassa.security.quarantine — the gate-1 sandbox (no red/datos).

All offline: candidates are harmless ``bash -c`` snippets run in a throwaway temp
dir with a scrubbed env. No real network is ever touched.
"""

import time

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.security import quarantine
from lhassa.security.quarantine import QuarantineResult, run_in_quarantine


@pytest.mark.asyncio
async def test_harmless_command_runs_and_passes():
    res = await run_in_quarantine("echo hola")
    assert isinstance(res, QuarantineResult)
    assert res.ok is True
    assert res.exit_code == 0
    assert res.timed_out is False
    assert res.escaped is False
    assert "hola" in res.stdout


@pytest.mark.asyncio
async def test_runs_in_temp_dir_not_real_cwd(tmp_path):
    workdir = tmp_path / "cage"
    res = await run_in_quarantine("pwd", workdir=workdir)
    assert res.ok is True
    assert res.stdout.strip() == str(workdir)


@pytest.mark.asyncio
async def test_env_is_scrubbed_of_secrets(monkeypatch):
    # A secret in the parent env must NOT be visible to the candidate.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret-token")
    monkeypatch.setenv("LHASSA_INSTANCE_ID", "inst_leak")
    res = await run_in_quarantine('echo "[$ANTHROPIC_API_KEY][$LHASSA_INSTANCE_ID]"')
    assert res.ok is True
    assert "super-secret-token" not in res.stdout
    assert "inst_leak" not in res.stdout
    assert res.stdout.strip() == "[][]"


@pytest.mark.asyncio
async def test_timeout_kills_the_candidate():
    start = time.monotonic()
    res = await run_in_quarantine("sleep 30", timeout=1)
    elapsed = time.monotonic() - start
    assert res.timed_out is True
    assert res.ok is False
    assert elapsed < 10  # killed promptly, nowhere near 30s


@pytest.mark.asyncio
async def test_nonzero_exit_is_not_ok():
    res = await run_in_quarantine("exit 3")
    assert res.ok is False
    assert res.exit_code == 3
    assert res.timed_out is False


@pytest.mark.asyncio
async def test_argv_list_candidate():
    res = await run_in_quarantine(["echo", "from-argv"])
    assert res.ok is True
    assert "from-argv" in res.stdout


@pytest.mark.asyncio
async def test_reports_isolation_level():
    res = await run_in_quarantine("true")
    # In CI/dev at least one of these holds; the field is always populated.
    assert res.isolation in {"bwrap", "firejail", "env+net only", "none"}


@pytest.mark.asyncio
async def test_fallback_when_no_namespacing_tool(monkeypatch):
    # Force the best-effort path: no unshare/firejail/bwrap available.
    # conftest.py pins LHASSA_QUARANTINE_ISOLATION=none suite-wide, which is the
    # "deliberate operator opt-out" that bypasses the new default refusal — see
    # test_quarantine_platform.py for the refusal itself and its escape hatch.
    monkeypatch.setattr(quarantine.shutil, "which", lambda _name: None)
    res = await run_in_quarantine("echo plain")
    assert res.isolation == "none"
    assert res.ok is True
    assert "plain" in res.stdout


@pytest.mark.asyncio
async def test_bwrap_preferred_over_unshare_when_both_available(monkeypatch, tmp_path):
    # Requirement: FS-isolating tools (bwrap/firejail) must win over bare
    # unshare, which cannot hide the real filesystem.
    monkeypatch.delenv("LHASSA_QUARANTINE_ISOLATION", raising=False)
    monkeypatch.setattr(
        quarantine.shutil, "which",
        lambda name: "/usr/bin/" + name if name in {"unshare", "bwrap"} else None,
    )
    monkeypatch.setattr(quarantine, "_sandbox_works", lambda prefix, workdir: True)
    prefix, level = quarantine._select_isolation(tmp_path)
    assert level == "bwrap"
    assert prefix[0] == "bwrap"


@pytest.mark.asyncio
async def test_unshare_only_reports_env_net_only_not_fs_isolated(monkeypatch, tmp_path):
    # unshare alone never hides the real filesystem — the label must say so.
    monkeypatch.delenv("LHASSA_QUARANTINE_ISOLATION", raising=False)
    monkeypatch.setattr(
        quarantine.shutil, "which",
        lambda name: "/usr/bin/unshare" if name == "unshare" else None,
    )
    monkeypatch.setattr(quarantine, "_sandbox_works", lambda prefix, workdir: True)
    prefix, level = quarantine._select_isolation(tmp_path)
    assert level == "env+net only"
    assert prefix[:2] == ["unshare", "-rnm"]


@pytest.mark.asyncio
async def test_never_raises_on_bad_candidate():
    # A non-existent program → caged failure, not an exception.
    res = await run_in_quarantine(["/nonexistent/binary/xyz"])
    assert isinstance(res, QuarantineResult)
    assert res.ok is False
