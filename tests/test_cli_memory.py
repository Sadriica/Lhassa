"""Tests for ``lhassa memory add`` and ``lhassa onboard``.

Fully offline: the brain DB is redirected to a tmp file per test. ``onboard``'s
``input()`` calls are monkeypatched with a scripted iterator of answers so the
questionnaire runs without a real terminal.
"""

from __future__ import annotations

import asyncio
import builtins
import json

import pytest
from typer.testing import CliRunner

from lhassa.cli.app import app
from lhassa.config import Config

runner = CliRunner()

INST = Config.instance_id


def _use_tmp_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


# ── memory add ──────────────────────────────────────────────────────────────


def test_memory_add_writes_a_fact(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)

    res = runner.invoke(
        app,
        ["memory", "add", "The owner prefers dark roast coffee.", "--tags", "prefs,coffee", "--json"],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["tipo"] == "dato"
    assert payload["contenido"] == "The owner prefers dark roast coffee."
    assert payload["id"]

    from lhassa.memory import facts

    rows = asyncio.run(facts.load_facts(INST))
    assert any(f.contenido == "The owner prefers dark roast coffee." for f in rows)


def test_memory_add_directiva_and_pin(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)

    res = runner.invoke(
        app,
        ["memory", "add", "Always answer concisely.", "--tipo", "directiva", "--pin", "--json"],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["tipo"] == "directiva"
    assert payload["pinned"] is True


def test_memory_add_rejects_bad_tipo(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)

    res = runner.invoke(app, ["memory", "add", "Some fact.", "--tipo", "bogus"])
    assert res.exit_code != 0
    assert res.exit_code == 2
    # Never crashed with a raw traceback; a clean CLI usage error.
    assert res.exception is None or isinstance(res.exception, SystemExit)


def test_memory_add_requires_nonblank_text(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)

    res = runner.invoke(app, ["memory", "add", "   "])
    assert res.exit_code == 2


# ── memory search still works after the group conversion ────────────────────


def test_memory_search_still_works_after_group_conversion(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    from lhassa.memory import facts

    asyncio.run(facts.add_fact(INST, "dato", "Loves chocolate.", tags=["prefs"]))

    res = runner.invoke(app, ["memory", "chocolate", "--json"])
    assert res.exit_code == 0, res.output
    rows = json.loads(res.output)
    assert any("chocolate" in r["contenido"].lower() for r in rows)


# ── onboard ──────────────────────────────────────────────────────────────────


def _script_input(monkeypatch, answers):
    """Feed ``answers`` to successive ``input()`` calls (extra calls raise)."""
    it = iter(answers)

    def _fake_input(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError("no more scripted answers")

    monkeypatch.setattr(builtins, "input", _fake_input)


def test_onboard_writes_facts_from_scripted_answers(tmp_path, monkeypatch, capsys):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    from lhassa.cli.memory_cmds import onboard_cmd

    answers = [
        "Owner is a solo developer building Lhassa.",
        "Shipping the Ola 1 batch of parity features.",
        "Keep it short and to the point.",
        "09:00-18:00 CET.",
        "Never post to social media on my behalf.",
    ]
    _script_input(monkeypatch, answers)

    onboard_cmd()
    out = capsys.readouterr().out
    assert "5" in out or "I've taken in" in out

    from lhassa.memory import facts

    rows = asyncio.run(facts.load_facts(INST))
    contents = [f.contenido for f in rows]
    assert "Owner is a solo developer building Lhassa." in contents
    assert "Never post to social media on my behalf." in contents

    # The hard-limits answer is written as a pinned directiva.
    limit_fact = next(f for f in rows if f.contenido == "Never post to social media on my behalf.")
    assert limit_fact.tipo == "directiva"
    assert limit_fact.pinned is True
    assert set(limit_fact.tags) >= {"owner", "onboard"}
    assert limit_fact.procedencia == "onboard"


def test_onboard_skips_blank_answers(tmp_path, monkeypatch, capsys):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    from lhassa.cli.memory_cmds import onboard_cmd

    _script_input(monkeypatch, ["", "", "", "", ""])

    onboard_cmd()
    out = capsys.readouterr().out
    assert "Nothing new" in out

    from lhassa.memory import facts

    rows = asyncio.run(facts.load_facts(INST))
    assert rows == []


def test_onboard_dedupes_against_existing_facts(tmp_path, monkeypatch, capsys):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    from lhassa.memory import facts

    asyncio.run(facts.add_fact(INST, "dato", "Owner is a solo developer building Lhassa.", tags=["owner"]))

    from lhassa.cli.memory_cmds import onboard_cmd

    answers = [
        "Owner is a solo developer building Lhassa.",  # duplicate; should be skipped
        "",
        "",
        "",
        "",
    ]
    _script_input(monkeypatch, answers)

    onboard_cmd()
    out = capsys.readouterr().out
    assert "already carry" in out

    rows = asyncio.run(facts.load_facts(INST))
    # Still just the one original fact; no duplicate written.
    assert len(rows) == 1


def test_onboard_non_tty_exits_cleanly_without_prompting(tmp_path, monkeypatch, capsys):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def _boom(prompt: str = ""):
        raise AssertionError("onboard must not call input() when stdin is not a tty")

    monkeypatch.setattr(builtins, "input", _boom)

    from lhassa.cli.memory_cmds import onboard_cmd
    import typer

    with pytest.raises(typer.Exit) as exc_info:
        onboard_cmd()
    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "interactive terminal" in out

    from lhassa.memory import facts

    rows = asyncio.run(facts.load_facts(INST))
    assert rows == []
