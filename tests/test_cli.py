"""Tests for the Lhassa CLI; fully mocked, no LLM or network is touched."""

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


# ── The REPL is a conversation, not a row of one-shots ─────────────────────────

def test_clip_cuts_a_huge_tool_result_and_says_so():
    from lhassa.cli import agent as agent_mod

    clipped = agent_mod._clip("x" * (agent_mod.MAX_TOOL_RESULT_CHARS + 500))
    assert len(clipped) < agent_mod.MAX_TOOL_RESULT_CHARS + 200
    assert "truncated" in clipped
    assert agent_mod._clip("short") == "short"


def test_seed_messages_threads_history_then_the_prompt():
    from lhassa.cli.agent import _seed_messages

    msgs = _seed_messages([("user", "create it"), ("assistant", "done")], "did you?")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"] == "did you?"


def test_seed_messages_keeps_only_the_last_turns():
    from lhassa.cli import agent as agent_mod

    long = [("user", f"q{i}") for i in range(agent_mod.HISTORY_TURNS * 4)]
    msgs = agent_mod._seed_messages(long, "now")
    assert len(msgs) == agent_mod.HISTORY_TURNS * 2 + 1


def test_final_never_returns_an_empty_answer():
    from lhassa.cli.agent import _final

    assert _final("").strip()
    assert _final("   ") != ""
    assert _final("real answer") == "real answer"


# ── The CLI thinks with the brain's own memory, not an empty head ──────────────

def test_system_prompt_layers_the_memory_context():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from lhassa.cli import agent as agent_mod
    from lhassa.memory import load as memory_load

    ctx = SimpleNamespace(to_prompt=lambda: "Directivas (siempre vigentes):\n- no em dashes")
    with patch.object(memory_load, "load", AsyncMock(return_value=ctx)) as m:
        sysprompt = asyncio.run(agent_mod.system_prompt(SimpleNamespace(), "did you create it?"))
    assert "no em dashes" in sysprompt
    assert "Markdown" in sysprompt  # the CLI instruction still rides along
    assert m.await_args.kwargs["query"] == "did you create it?"


def test_system_prompt_degrades_to_the_floor_when_memory_fails():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from lhassa.cli import agent as agent_mod
    from lhassa.memory import load as memory_load

    with patch.object(memory_load, "load", AsyncMock(side_effect=RuntimeError("db gone"))):
        sysprompt = asyncio.run(agent_mod.system_prompt(SimpleNamespace(), "hi"))
    assert sysprompt == agent_mod.build_system_prompt()


# ── The CLI door leaves a trace, and stops paying for what it already answered ──

def test_door_schemas_drop_the_screen_grab_by_default(monkeypatch):
    from lhassa.cli import agent as agent_mod

    monkeypatch.delenv("LHASSA_CLI_TOOLS", raising=False)
    monkeypatch.delenv("LHASSA_CLI_TOOLS_EXCLUDE", raising=False)
    names = {d["name"] for d in agent_mod._door_schemas()}
    assert "analyze_screen" not in names
    assert "shell" in names and "track_postulaciones" in names


def test_door_schemas_honor_the_allow_list(monkeypatch):
    from lhassa.cli import agent as agent_mod

    monkeypatch.setenv("LHASSA_CLI_TOOLS", "shell,read_file")
    monkeypatch.delenv("LHASSA_CLI_TOOLS_EXCLUDE", raising=False)
    assert {d["name"] for d in agent_mod._door_schemas()} == {"shell", "read_file"}


def test_door_schemas_never_leave_the_model_toolless(monkeypatch):
    from lhassa.cli import agent as agent_mod

    monkeypatch.setenv("LHASSA_CLI_TOOLS", "no_such_tool")
    assert len(agent_mod._door_schemas()) > 1  # degrades to the full catalog


def test_a_turn_logs_one_cli_episode():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from lhassa.cli import agent as agent_mod
    from lhassa.core import db
    from lhassa.core.episode import Episode

    appended: list = []

    async def _fake_append(self):
        appended.append(self)
        return self.id

    with patch.object(db, "init_db", AsyncMock()), \
         patch.object(Episode, "append", _fake_append):
        asyncio.run(agent_mod._log_episode(
            SimpleNamespace(), "inst_x", "did you create it?",
            CliResult("no, not yet", "m", 1, 2, 0.5, tools_used=("shell",)),
            cache_hit=False,
        ))

    # The turn episode, plus one capability-use episode for the shell call.
    assert len(appended) == 2
    ep = appended[0]
    assert ep.despertar_por == "cli"
    assert "did you create it?" in ep.accion   # FTS5-indexed: recall can find it
    assert "not yet" in ep.resultado
    assert "shell" in ep.decision and ep.costo_usd == 0.5


def test_a_turn_records_the_capabilities_it_exercised():
    """The utility tribunal groups on ``accion``; the terminal must name itself.

    Without this its turn episode carries the QUESTION in ``accion``, so the
    tribunal counted zero owner uses for everything and started proposing to
    retire capabilities the owner drives daily.
    """
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from lhassa.cli import agent as agent_mod
    from lhassa.core import db
    from lhassa.core.episode import Episode

    appended: list = []

    async def _fake_append(self):
        appended.append(self)
        return self.id

    with patch.object(db, "init_db", AsyncMock()), \
         patch.object(Episode, "append", _fake_append):
        asyncio.run(agent_mod._log_episode(
            SimpleNamespace(), "inst_x", "list my applications",
            CliResult(
                "two of them", "m", 1, 2, 0.5,
                # shell twice: one capability use, not two. web_search has no
                # action twin and must not invent one.
                tools_used=("track_postulaciones", "shell", "web_search", "shell"),
            ),
            cache_hit=False,
        ))

    capability_rows = [e for e in appended[1:]]
    assert [e.accion for e in capability_rows] == ["tracking_postulaciones", "run_command"]
    for row in capability_rows:
        assert row.despertar_por == "cli"   # classify_wake buckets this as owner
        assert row.costo_usd == 0.0         # accounting must never cost anything


def test_a_turn_with_no_capability_tools_logs_only_itself():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from lhassa.cli import agent as agent_mod
    from lhassa.core import db
    from lhassa.core.episode import Episode

    appended: list = []

    async def _fake_append(self):
        appended.append(self)
        return self.id

    with patch.object(db, "init_db", AsyncMock()), \
         patch.object(Episode, "append", _fake_append):
        asyncio.run(agent_mod._log_episode(
            SimpleNamespace(), "inst_x", "hello",
            CliResult("hi", "m", 1, 2, 0.0, tools_used=("web_search",)),
            cache_hit=False,
        ))

    assert len(appended) == 1


def test_every_mapped_capability_is_a_real_action():
    """A typo in the map would silently log a capability nothing can survey."""
    from lhassa import actionpack
    from lhassa.cli import agent as agent_mod
    from lhassa.core.actions import ACTIONS

    actionpack.register_all()
    # Actions a capability pack provides are only real when that pack is
    # installed (core alone has no calendar_sync); still checked when it is.
    # Ask the pack registry, not the filesystem: a stale ``__pycache__`` left
    # where a pack's modules used to live still satisfies ``find_spec``.
    from lhassa.extensions import PACKS

    pack_actions = {"calendar_sync": "calendar"}
    absent = {action for action, pack in pack_actions.items() if pack not in PACKS}
    unknown = sorted(
        set(agent_mod._TOOL_CAPABILITY.values()) - set(ACTIONS) - absent
    )
    assert not unknown, f"mapped to actions that do not exist: {unknown}"


def test_episode_failure_never_breaks_the_answer():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from lhassa.cli import agent as agent_mod
    from lhassa.core import db

    with patch.object(db, "init_db", AsyncMock(side_effect=RuntimeError("locked"))):
        asyncio.run(agent_mod._log_episode(
            SimpleNamespace(), "inst_x", "q", _FAKE, cache_hit=False
        ))  # must not raise


def test_the_cache_is_never_consulted_mid_conversation():
    import asyncio
    from types import SimpleNamespace

    from lhassa.cli import agent as agent_mod

    # "and that one?" means nothing without the turns before it.
    got = asyncio.run(agent_mod._cache_lookup(
        SimpleNamespace(), "inst_x", "y ese?", [("user", "q"), ("assistant", "a")]
    ))
    assert got is None


def test_a_stable_first_question_can_hit_the_cache():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from lhassa.cli import agent as agent_mod
    from lhassa.memory import answer_cache
    from lhassa.notify import intent

    with patch.object(intent, "classify", return_value="cacheable"), \
         patch.object(answer_cache, "lookup", AsyncMock(return_value=("42", 0.97))):
        got = asyncio.run(agent_mod._cache_lookup(
            SimpleNamespace(), "inst_x", "what is the answer?", None
        ))
    assert got is not None
    assert got.response == "42"
    assert got.cost_usd == 0.0 and got.input_tokens == 0


def test_a_truncated_answer_is_never_cached():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from lhassa.cli import agent as agent_mod
    from lhassa.memory import answer_cache
    from lhassa.notify import intent

    partial = CliResult("half an answer", "m", 1, 1, 0.1, truncated=True)
    with patch.object(intent, "classify", return_value="cacheable"), \
         patch.object(answer_cache, "store", AsyncMock()) as store:
        asyncio.run(agent_mod._cache_store(SimpleNamespace(), "inst_x", "q", partial, None))
    store.assert_not_awaited()


def test_the_audit_door_is_bindable_and_defaults_to_voice():
    from lhassa.tools.handlers._shared import current_door, reset_door, set_door

    assert current_door() == "voice_tool"
    tok = set_door("cli_tool")
    assert current_door() == "cli_tool"
    reset_door(tok)
    assert current_door() == "voice_tool"


def test_absent_pack_command_prints_install_hint_not_a_chat():
    """``lhassa tui`` without the lhassa-tui pack must not become a paid chat turn."""
    import typer
    from typer.testing import CliRunner

    from lhassa.cli import app as app_mod
    from lhassa.extensions import CLI_COMMANDS

    probe = typer.Typer()
    probe.command("noop")(lambda: None)
    probe.command("tui")(app_mod._absent_pack_stub("tui", "lhassa-tui"))
    result = CliRunner().invoke(probe, ["tui"])
    assert result.exit_code == 1
    assert "uv sync --package lhassa-tui" in result.output
    if "tui" not in CLI_COMMANDS:
        # Core alone: the real CLI routes `tui` to the stub, never to chat.
        real = CliRunner().invoke(app_mod.app, ["tui"])
        assert real.exit_code == 1 and "lhassa-tui" in real.output


def test_absent_pack_sub_app_with_arguments_still_gets_the_hint():
    """``lhassa calendar list`` with the calendar pack absent: hint, not usage error or chat."""
    from typer.testing import CliRunner

    from lhassa.cli import app as app_mod
    from lhassa.extensions import CLI_APPS, CLI_COMMANDS

    for cmd in app_mod._ABSENT_PACK_COMMANDS:
        if cmd in CLI_APPS or cmd in CLI_COMMANDS:
            continue
        result = CliRunner().invoke(app_mod.app, [cmd, "list", "--all"])
        assert result.exit_code == 1, (cmd, result.output)
        assert app_mod._ABSENT_PACK_COMMANDS[cmd] in result.output
