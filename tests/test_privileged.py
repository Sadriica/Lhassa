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


def test_allowlist_has_expected_defaults():
    allowed = privileged.allowlist()
    assert {"systemctl", "journalctl", "pacman", "power-mode"} <= allowed


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

    monkeypatch.setattr(privileged.asyncio, "create_subprocess_exec", fake_exec)
    out = await privileged.run_privileged("systemctl restart bluetooth")
    assert "install-privileged" in out


async def test_unparseable_command_is_safe():
    out = await privileged.run_privileged('systemctl "unterminated')
    assert "couldn't parse" in out.lower()
