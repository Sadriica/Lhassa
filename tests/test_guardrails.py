"""Tests for tool guardrails."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reload_after_env_changes():
    """Undo the module reloads these tests do, so none inherits another's env.

    ``Config`` evaluates its knobs at import, so a test that sets an env var and
    reloads leaves the reloaded class behind for whoever runs next: a later test
    asserting that ``rm -rf /`` is blocked would see the override still on.
    """
    yield
    import os

    import lhassa.config as config_mod
    import lhassa.tools.guardrails as guardrails

    # Clear the flags HERE rather than trusting the env fixture's teardown to
    # have run first; teardown order between fixtures is not ours to assume,
    # and reloading while a flag is still set just re-bakes the override in.
    for key in ("LHASSA_ALLOW_DESTRUCTIVE_SHELL", "LHASSA_ALLOW_PERSISTENT_SHELL"):
        os.environ.pop(key, None)
    importlib.reload(config_mod)
    importlib.reload(guardrails)



def test_validate_shell_command_blocks_destructive(clean_env):
    from lhassa.tools.guardrails import validate_shell_command

    error = validate_shell_command("rm -rf /")
    assert error is not None
    assert "Blocked potentially destructive shell command" in error


def test_validate_shell_command_allows_override(clean_env):
    clean_env.setenv("LHASSA_ALLOW_DESTRUCTIVE_SHELL", "1")

    import importlib
    import lhassa.config as config_mod
    import lhassa.tools.guardrails as guardrails

    importlib.reload(config_mod)
    importlib.reload(guardrails)
    assert guardrails.validate_shell_command("rm -rf /") is None


def test_a_destructive_command_cannot_hide_behind_an_operator(clean_env):
    """The patterns anchor at the start, so the command must be split first.

    Before this, ``rm -rf /`` was blocked and ``true && rm -rf /`` ran.
    """
    from lhassa.tools.guardrails import validate_shell_command

    for command in (
        "true && rm -rf /",
        "echo hi; rm -rf /",
        "x=1\nrm -rf /",
        "true || shutdown -h now",
        "cat f | dd of=/dev/sda",
    ):
        assert validate_shell_command(command) is not None, command


def test_validate_shell_command_blocks_persistence_and_privilege(clean_env):
    """Persistence outlives the turn that created it; that needs the owner."""
    from lhassa.tools.guardrails import validate_shell_command

    for command in (
        "systemctl --user enable --now lhassa-x.timer",
        "sudo tee /etc/hosts",
        "echo x > ~/.config/systemd/user/a.service",
        "crontab -e",
        "pacman -S ripgrep",
        "cp unit.service /etc/systemd/system/",
    ):
        error = validate_shell_command(command)
        assert error is not None, command
        assert "persists past the turn" in error


def test_ordinary_commands_still_run(clean_env):
    """A guardrail that blocks normal work would just be turned off."""
    from lhassa.tools.guardrails import validate_shell_command

    for command in (
        "ls -la && git status",
        "lhassa flow run x --json",
        "systemctl --user list-timers",
        "cat /etc/hostname",
        "grep -r foo . | head -20",
        "python -c 'print(1)'",
    ):
        assert validate_shell_command(command) is None, command


def test_persistent_shell_allows_override(clean_env):
    clean_env.setenv("LHASSA_ALLOW_PERSISTENT_SHELL", "1")

    import importlib

    import lhassa.config as config_mod
    import lhassa.tools.guardrails as guardrails

    importlib.reload(config_mod)
    importlib.reload(guardrails)
    assert guardrails.validate_shell_command("crontab -e") is None
    # The destructive tier is a SEPARATE opt-in; this flag must not unlock it.
    assert guardrails.validate_shell_command("rm -rf /") is not None


def test_validate_write_path_respects_root(clean_env, tmp_path):
    clean_env.setenv("LHASSA_FILE_WRITE_ROOT", str(tmp_path))

    import importlib
    import lhassa.config as config_mod
    import lhassa.tools.guardrails as guardrails

    importlib.reload(config_mod)
    importlib.reload(guardrails)

    allowed, error = guardrails.validate_write_path(str(tmp_path / "ok.txt"))
    denied, denied_error = guardrails.validate_write_path("/tmp/outside.txt")

    assert allowed == Path(tmp_path / "ok.txt")
    assert error is None
    assert denied is None
    assert "outside LHASSA_FILE_WRITE_ROOT" in denied_error
