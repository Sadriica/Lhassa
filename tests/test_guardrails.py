"""Tests for tool guardrails."""

from pathlib import Path



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
