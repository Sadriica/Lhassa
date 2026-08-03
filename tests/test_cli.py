"""Tests for the Lhassa CLI — fully mocked, no LLM or network is touched."""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from lhassa.cli.agent import CliResult
from lhassa.cli.app import app

runner = CliRunner()

_FAKE = CliResult(
    response="# Hi\n\nHello there.",
    model="claude-haiku-4-5-20251001",
    input_tokens=12,
    output_tokens=5,
    cost_usd=0.0001,
)


def test_one_shot_prompt_prints_response():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE):
        result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "Hello there." in result.output


def test_json_output_shape():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE):
        result = runner.invoke(app, ["hello", "--json"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip())
    assert obj["response"] == _FAKE.response
    assert obj["model"] == _FAKE.model
    assert obj["input_tokens"] == 12
    assert obj["output_tokens"] == 5
    assert obj["cost_usd"] == _FAKE.cost_usd


def test_reads_prompt_from_stdin():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE) as m:
        result = runner.invoke(app, [], input="summarize this piped text")
    assert result.exit_code == 0
    passed_prompt = m.call_args[0][0]
    assert "summarize this piped text" in passed_prompt


def test_arg_and_stdin_are_combined():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE) as m:
        result = runner.invoke(app, ["summarize"], input="some file contents")
    assert result.exit_code == 0
    passed_prompt = m.call_args[0][0]
    assert "summarize" in passed_prompt
    assert "some file contents" in passed_prompt


def test_no_prompt_no_stdin_exits_nonzero():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE):
        result = runner.invoke(app, [], input="")
    assert result.exit_code == 1


def test_build_system_prompt_uses_persona_and_allows_markdown():
    from lhassa.cli.agent import build_system_prompt

    sysprompt = build_system_prompt()
    assert "Lhassa" in sysprompt
    assert "Markdown" in sysprompt
