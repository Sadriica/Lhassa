"""Quarantine across platforms — no sandbox tool now means refusal everywhere.

The invariant at stake is "authored code never executes outside quarantine".
``isolation="none"`` (no namespacing tool at all, on any platform) is REFUSED by
default — Linux used to warn-and-proceed here, which was the one inconsistency:
every other platform already refused. Two explicit, deliberate opt-outs bypass
the refusal on any platform: ``LHASSA_QUARANTINE_ISOLATION=none`` (skip trying a
sandbox tool at all) and ``LHASSA_QUARANTINE_ALLOW_UNSANDBOXED=1`` (a sandbox
tool was looked for and not found; run anyway).

Platform is selected by injection (``LHASSA_PLATFORM``), so these run on Linux CI.
``tests/conftest.py`` pins ``LHASSA_QUARANTINE_ISOLATION=none`` suite-wide, so a
test that needs the real selection chain — or the not-forced refusal — clears it
explicitly.
"""

import pytest

from lhassa.security import quarantine


@pytest.mark.asyncio
async def test_linux_behaviour_is_unchanged(tmp_path):
    """The tier-1 path still runs and still reports a real isolation level."""
    result = await quarantine.run_in_quarantine(
        "echo hola", timeout=10, workdir=tmp_path
    )
    assert result.ok
    assert "hola" in result.stdout
    assert result.isolation in {"bwrap", "firejail", "env+net only", "none"}


@pytest.mark.asyncio
async def test_unsandboxed_run_is_refused_off_linux(tmp_path, monkeypatch):
    """No sandbox + not Linux → refuse, do not downgrade."""
    monkeypatch.delenv("LHASSA_QUARANTINE_ISOLATION", raising=False)
    monkeypatch.delenv("LHASSA_QUARANTINE_ALLOW_UNSANDBOXED", raising=False)
    monkeypatch.setenv("LHASSA_PLATFORM", "plan9")  # unregistered → no sandbox
    monkeypatch.setattr(quarantine, "_select_isolation", lambda wd: ([], "none"))

    result = await quarantine.run_in_quarantine(
        "echo should-not-run", timeout=10, workdir=tmp_path
    )
    assert result.ok is False
    assert result.isolation == "none"
    assert "refusing to run unsandboxed" in result.stderr
    assert "should-not-run" not in result.stdout


@pytest.mark.asyncio
async def test_linux_now_also_refuses_unsandboxed_by_default(tmp_path, monkeypatch):
    """The old Linux-only warn-and-proceed was the inconsistency; it is gone.

    No sandbox tool found + no explicit opt-out → refuse, same as every other
    platform. This is the behaviour change requirement #2 asks for.
    """
    monkeypatch.delenv("LHASSA_QUARANTINE_ISOLATION", raising=False)
    monkeypatch.delenv("LHASSA_QUARANTINE_ALLOW_UNSANDBOXED", raising=False)
    monkeypatch.setenv("LHASSA_PLATFORM", "linux")
    monkeypatch.setattr(quarantine, "_select_isolation", lambda wd: ([], "none"))

    result = await quarantine.run_in_quarantine(
        "echo should-not-run", timeout=10, workdir=tmp_path
    )
    assert result.ok is False
    assert result.isolation == "none"
    assert "refusing to run unsandboxed" in result.stderr
    assert "should-not-run" not in result.stdout


@pytest.mark.asyncio
async def test_allow_unsandboxed_escape_hatch_permits_the_run(tmp_path, monkeypatch):
    """LHASSA_QUARANTINE_ALLOW_UNSANDBOXED=1 is the explicit, narrow opt-out.

    Distinct from LHASSA_QUARANTINE_ISOLATION=none (which skips even trying a
    sandbox tool): this one says "a sandbox tool was looked for, none was
    found, run anyway" — and it works on every platform, including Linux.
    """
    monkeypatch.delenv("LHASSA_QUARANTINE_ISOLATION", raising=False)
    monkeypatch.setenv("LHASSA_QUARANTINE_ALLOW_UNSANDBOXED", "1")
    monkeypatch.setenv("LHASSA_PLATFORM", "linux")
    monkeypatch.setattr(quarantine, "_select_isolation", lambda wd: ([], "none"))

    result = await quarantine.run_in_quarantine(
        "echo still-runs", timeout=10, workdir=tmp_path
    )
    assert result.ok
    assert "still-runs" in result.stdout
    assert result.isolation == "none"


def test_selection_does_not_consult_the_layer_on_linux(tmp_path, monkeypatch):
    """On Linux the chain is the three tools and nothing else — byte-identical."""
    monkeypatch.delenv("LHASSA_QUARANTINE_ISOLATION", raising=False)
    monkeypatch.setenv("LHASSA_PLATFORM", "linux")
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("the platform layer must not be consulted on Linux")

    monkeypatch.setattr(quarantine, "get_platform", _boom)
    prefix, level = quarantine._select_isolation(tmp_path)
    assert called["n"] == 0
    assert level in {"bwrap", "firejail", "env+net only", "none"}
    assert isinstance(prefix, list)


def test_selection_asks_the_layer_off_linux(tmp_path, monkeypatch):
    """A registered non-Linux platform contributes its own sandbox.

    The Linux chain is attempted first and short-circuits when its tools are
    present, so this simulates the real off-Linux situation: none of
    unshare/firejail/bwrap exist.
    """
    monkeypatch.delenv("LHASSA_QUARANTINE_ISOLATION", raising=False)
    monkeypatch.setenv("LHASSA_PLATFORM", "fake-sandboxed")
    monkeypatch.setattr(
        quarantine.shutil, "which",
        lambda name: None if name in {"unshare", "firejail", "bwrap"} else "/usr/bin/" + name,
    )

    class _Fake:
        name = "fake-sandboxed"

        def uptime_seconds(self):
            return None

        def memory(self):
            return None

        def failed_services(self):
            return None

        def notify_argv(self, title, message):
            return None

        def sandbox_prefix(self, workdir):
            return (["fake-sandbox", "--"], "fake-sandbox")

    from lhassa import host

    host.register_platform("fake-sandboxed", _Fake, override=True)
    prefix, level = quarantine._select_isolation(tmp_path)
    assert level == "fake-sandbox"
    assert prefix == ["fake-sandbox", "--"]


@pytest.mark.asyncio
async def test_an_explicit_opt_out_is_honoured_on_every_platform(tmp_path, monkeypatch):
    """LHASSA_QUARANTINE_ISOLATION=none is a deliberate operator choice.

    The refusal above is for "no sandbox was available", not for an operator who
    switched it off on purpose — otherwise this suite's own convention (see
    conftest) would become unrunnable off Linux.
    """
    monkeypatch.setenv("LHASSA_QUARANTINE_ISOLATION", "none")
    monkeypatch.setenv("LHASSA_PLATFORM", "plan9")

    result = await quarantine.run_in_quarantine(
        "echo opted-out", timeout=10, workdir=tmp_path
    )
    assert result.ok
    assert "opted-out" in result.stdout
    assert result.isolation == "none"
