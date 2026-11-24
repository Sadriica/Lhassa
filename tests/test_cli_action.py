# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa action run``: one action, always through the allowlist, never a direct script call."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from lhassa.actionpack import _system
from lhassa.cli import action_cmds
from lhassa.cli.app import app

runner = CliRunner()


class Proc:
    returncode, stdout, stderr = 0, "profile set", ""


@pytest.fixture(autouse=True)
def _own_signing_key(tmp_path, monkeypatch):
    from lhassa.security import signing

    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")


@pytest.fixture
def fake_power(monkeypatch):
    calls = []
    monkeypatch.setattr(_system.shutil, "which", lambda name: "/fake/power-mode")

    def run(cmd, **kw):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(_system.subprocess, "run", run)
    return calls


def test_parse_params():
    assert action_cmds.parse_params(["mode=saver", "x=a=b"]) == {"mode": "saver", "x": "a=b"}
    for bad in ("nokey", "=v", ""):
        with pytest.raises(ValueError):
            action_cmds.parse_params([bad])


def test_power_mode_runs_through_the_allowlist(fake_power, monkeypatch):
    from lhassa.security import allowlist

    decisions = []
    real = allowlist.allow
    monkeypatch.setattr(allowlist, "allow", lambda task, **kw: decisions.append(task.accion) or real(task, **kw))
    result = runner.invoke(app, ["action", "run", "power_mode", "--param", "mode=saver"])
    assert result.exit_code == 0, result.output
    assert "power_mode saver" in result.output
    assert decisions == ["power_mode"]  # the gate was consulted before anything ran
    assert fake_power == [["power-mode", "saver"]]


def test_json_output(fake_power):
    result = runner.invoke(app, ["action", "run", "power_mode", "-p", "mode=balanced", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "result": "power_mode balanced: profile set"}


def test_an_invalid_mode_is_refused_by_the_action_and_exits_1(fake_power):
    result = runner.invoke(app, ["action", "run", "power_mode", "-p", "mode=Rm -rf"])
    assert result.exit_code == 1 and fake_power == []


def test_a_failing_script_exits_1(monkeypatch, fake_power):
    class Bad(Proc):
        returncode, stdout, stderr = 3, "", "no such profile"

    monkeypatch.setattr(_system.subprocess, "run", lambda cmd, **kw: Bad())
    result = runner.invoke(app, ["action", "run", "power_mode", "-p", "mode=saver"])
    assert result.exit_code == 1


def test_unknown_actions_and_bad_params_are_refused(fake_power):
    assert runner.invoke(app, ["action", "run", "no_such_action"]).exit_code == 1
    assert runner.invoke(app, ["action", "run", "power_mode", "--param", "oops"]).exit_code == 2
    assert fake_power == []


def test_an_action_missing_from_the_allowlist_is_denied(fake_power, monkeypatch):
    from lhassa.security import allowlist

    monkeypatch.delitem(allowlist.ALLOWLIST, "power_mode")
    result = runner.invoke(app, ["action", "run", "power_mode", "-p", "mode=saver", "--json"])
    assert result.exit_code == 1 and "not in the allowlist" in json.loads(result.output)["error"]
    assert fake_power == []


def test_an_action_that_needs_approval_is_not_run_from_here(fake_power, monkeypatch):
    from lhassa.security import allowlist

    spec = allowlist.ALLOWLIST["power_mode"]
    monkeypatch.setitem(allowlist.ALLOWLIST, "power_mode", type(spec)(
        objetivos=spec.objetivos, min_trust=spec.min_trust, requires_approval=True))
    result = runner.invoke(app, ["action", "run", "power_mode", "-p", "mode=saver", "--json"])
    assert result.exit_code == 1 and "requires human approval" in json.loads(result.output)["error"]
    assert fake_power == []
