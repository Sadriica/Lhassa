"""``lhassa serve`` (the api pack's CLI command): arg parsing only, never binds."""

from unittest.mock import patch

from typer.testing import CliRunner

from lhassa.cli.app import app
from lhassa.extensions import CLI_COMMANDS

runner = CliRunner()


def test_pack_registers_serve():
    assert "serve" in CLI_COMMANDS


def test_serve_parses_args_without_binding():
    with patch("lhassa.api.cli._serve") as m:
        res = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9123"])
    assert res.exit_code == 0
    m.assert_called_once_with("0.0.0.0", 9123)
    # The URL hint uses 'localhost' for a wildcard bind.
    assert "9123" in res.output


def test_serve_default_args():
    with patch("lhassa.api.cli._serve") as m:
        res = runner.invoke(app, ["serve"])
    assert res.exit_code == 0
    m.assert_called_once_with("127.0.0.1", 7766)
