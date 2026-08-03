"""Tests for lhassa.tools.privileged — scoped, allowlisted root execution.

No real sudo is invoked: subprocess creation is mocked, and the audit sink is
stubbed so nothing touches the real home directory.
"""

from unittest.mock import AsyncMock

import pytest

from lhassa.state import state
from lhassa.tools import privileged


@pytest.fixture(autouse=True)
def stub_audit(monkeypatch):
    """Don't write the real ~/.lhassa/audit.json during tests."""
    monkeypatch.setattr(state, "record_audit_event", AsyncMock())


def _env_extras() -> set:
    import os

    raw = os.getenv("LHASSA_PRIVILEGED_ALLOW") or ""
    return {p.strip() for p in raw.split(",") if p.strip()}


def test_allowlist_is_the_candidates_actually_installed():
    """The allowlist is the CANDIDATE set filtered by what exists on this host.

    It is not a fixed list: ``privileged.allowlist()`` keeps only candidates
    ``shutil.which`` can find, so asserting {systemctl, journalctl, pacman,
    power-mode} assumes a Linux host with systemd and pacman — which macOS CI
    does not have. Assert the property that holds on every platform instead.
    """
    import shutil

    allowed = privileged.allowlist()
    assert allowed <= set(privileged._CANDIDATE_ALLOW) | _env_extras()
    for name in allowed - _env_extras():
        assert shutil.which(name), f"{name} allowlisted but not installed"


def test_allowlist_keeps_installed_candidates_and_drops_the_rest(monkeypatch):
    """The filtering itself, pinned by injection rather than by the host.

    ``_installed_allow`` is exercised directly: ``privileged`` imports shutil
    inside the function and freezes ``_DEFAULT_ALLOW`` at import time, so the
    collector — not the cached result — is what a platform test can drive.
    """
    import shutil as _shutil

    monkeypatch.setattr(
        _shutil,
        "which",
        lambda name: "/usr/bin/" + name if name in {"systemctl", "journalctl"} else None,
    )
    installed = privileged._installed_allow()
    assert {"systemctl", "journalctl"} <= installed
    assert "pacman" not in installed
    assert "power-mode" not in installed


def test_allowlist_extends_via_env(monkeypatch):
    monkeypatch.setenv("LHASSA_PRIVILEGED_ALLOW", "foo, bar")
    allowed = privileged.allowlist()
    assert {"foo", "bar"} <= allowed


async def test_refuses_command_outside_allowlist():
    out = await privileged.run_privileged("cat /etc/shadow")
    assert "allowlist" in out.lower()
    state.record_audit_event.assert_awaited()


async def test_refuses_destructive_even_if_base_allowlisted(monkeypatch):
    monkeypatch.setenv("LHASSA_PRIVILEGED_ALLOW", "dd")
    out = await privileged.run_privileged("dd if=/dev/zero of=/dev/sda")
    assert "destructive" in out.lower()


async def test_runs_allowlisted_command(monkeypatch):
    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"active", b"")

    async def fake_exec(*argv, **kwargs):
        assert argv[:3] == ("sudo", "-n", "systemctl")
        return FakeProc()

    # ``systemctl`` is only in the default allowlist on a host that HAS it, so
    # it is granted here through the documented env seam. Otherwise this test
    # asserts nothing on any platform without systemd — macOS CI caught that.
    monkeypatch.setenv("LHASSA_PRIVILEGED_ALLOW", "systemctl")
    monkeypatch.setattr(privileged.asyncio, "create_subprocess_exec", fake_exec)
    out = await privileged.run_privileged("systemctl is-active bluetooth")
    assert out == "active"


async def test_reports_when_sudoers_not_installed(monkeypatch):
    class FakeProc:
        returncode = 1

        async def communicate(self):
            return (b"sudo: a password is required", b"")

    async def fake_exec(*argv, **kwargs):
        return FakeProc()

    # ``systemctl`` is only in the default allowlist on a host that HAS it, so
    # it is granted here through the documented env seam. Otherwise this test
    # asserts nothing on any platform without systemd — macOS CI caught that.
    monkeypatch.setenv("LHASSA_PRIVILEGED_ALLOW", "systemctl")
    monkeypatch.setattr(privileged.asyncio, "create_subprocess_exec", fake_exec)
    out = await privileged.run_privileged("systemctl restart bluetooth")
    assert "install-privileged" in out


async def test_unparseable_command_is_safe(monkeypatch):
    monkeypatch.setenv("LHASSA_PRIVILEGED_ALLOW", "systemctl")
    out = await privileged.run_privileged('systemctl "unterminated')
    assert "couldn't parse" in out.lower()
