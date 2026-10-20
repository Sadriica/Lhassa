"""CLI tests for ``lhassa calendar`` (add/list/remove); Typer CliRunner against
the real app, same pattern as ``tests/test_flow_templates.py``. Offline: every
test points ``config.calendars_file`` at a tmp path (no real ``~/.lhassa`` touched).
"""

import json

from typer.testing import CliRunner

from lhassa.cli.app import app

runner = CliRunner()


def _use_tmp(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    calendars_file = tmp_path / "calendars.json"
    monkeypatch.setattr(config_mod.Config, "calendars_file", str(calendars_file))
    monkeypatch.setattr(config_mod.Config, "calendar_ics_url", "")
    return calendars_file


def test_cli_calendar_add_and_list(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)

    res = runner.invoke(app, ["calendar", "add", "Work", "https://example.edu/feed.ics"])
    assert res.exit_code == 0, res.output
    assert "Work" in res.output

    res_list = runner.invoke(app, ["calendar", "list", "--json"])
    assert res_list.exit_code == 0, res_list.output
    data = json.loads(res_list.output)
    assert data == [{"name": "Work", "url": "https://example.edu/feed.ics"}]


def test_cli_calendar_add_rejects_bad_url(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["calendar", "add", "Bad", "not-a-url"])
    assert res.exit_code == 1


def test_cli_calendar_remove(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    runner.invoke(app, ["calendar", "add", "Work", "https://example.edu/feed.ics"])

    res = runner.invoke(app, ["calendar", "remove", "Work"])
    assert res.exit_code == 0, res.output

    res_list = runner.invoke(app, ["calendar", "list", "--json"])
    assert json.loads(res_list.output) == []


def test_cli_calendar_remove_unknown_exits_nonzero(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["calendar", "remove", "Ghost"])
    assert res.exit_code == 1


def test_cli_calendar_list_empty(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["calendar", "list"])
    assert res.exit_code == 0, res.output
    assert "No calendars configured" in res.output
