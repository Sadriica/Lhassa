"""Tests for ``lhassa capabilities`` / ``doctor`` — the elasticity surface.

Fully offline and LLM-free: ``collect_capabilities`` never constructs a provider
and never calls a model. These tests assert the expected sections are present, the
gather is defensive (a degraded subsystem is reported, not fatal), the ``--json``
output is valid and complete, the command (and its ``doctor`` alias) is registered,
and the default chat path is preserved.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from lhassa.cli import capabilities as cap
from lhassa.cli.agent import CliResult
from lhassa.cli.app import app

runner = CliRunner()

_FAKE = CliResult(
    response="# Hi\n\nHello there.",
    model="claude-haiku-4-5",
    input_tokens=12,
    output_tokens=5,
    cost_usd=0.0001,
)


def _tmp_config(tmp_path, **overrides):
    """A minimal config-like object with the attributes the collector reads."""
    base = dict(
        plugins_dir=str(tmp_path / "plugins"),
        org_role="",
        org_departments="optimization,documentation",
        org_topology_file=str(tmp_path / "topology.json"),
        notify_channels="telegram,desktop",
        heartbeat_provider="auto",
        local_model_url="",
        opencode_bin="opencode",
        opencode_model="opencode/deepseek-v4-flash-free",
        model="cloud-model",
        pseudonymize_remote=True,
        memory_layer4_enabled=False,
        voice_enabled=False,
        llm_kill_switch=False,
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_poll_enabled=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── collect_capabilities: sections present ───────────────────────────────────────

def test_collect_has_all_sections(tmp_path):
    data = cap.collect_capabilities(_tmp_config(tmp_path))
    assert set(data) >= {"registries", "active_config", "plugins", "errors"}

    reg = data["registries"]
    for key in (
        "providers",
        "harnesses",
        "verifiers",
        "specialists",
        "channels",
        "commands",
        "actions",
        "departments",
    ):
        assert key in reg, key
        assert "registered" in reg[key]


def test_collect_reports_known_registry_names(tmp_path):
    data = cap.collect_capabilities(_tmp_config(tmp_path))
    reg = data["registries"]
    # The built-in factories/adapters/verifiers are registered.
    assert "default" in reg["providers"]["registered"]
    assert set(reg["harnesses"]["registered"]) >= {"opencode", "claude", "codex"}
    assert set(reg["verifiers"]["registered"]) >= {"command", "tests", "metric"}
    assert "optimization" in reg["specialists"]["registered"]
    assert "telegram" in reg["channels"]["registered"]
    assert reg["commands"]["registered"]  # /start, /help, /status ...
    assert "sync_repo" in reg["actions"]["registered"]
    assert "optimization" in reg["departments"]["registered"]
    # harnesses available is a subset of registered.
    assert set(reg["harnesses"]["available"]) <= set(reg["harnesses"]["registered"])


def test_collect_active_config(tmp_path):
    data = cap.collect_capabilities(_tmp_config(tmp_path))
    active = data["active_config"]
    assert active["pseudonymize_remote"] is True
    assert active["memory_layer4_enabled"] is False
    assert active["voice_enabled"] is False
    assert active["llm_kill_switch"] is False
    assert active["routine_provider"]  # a label, mirrored off config (no model call)
    assert active["telegram"]["configured"] is False
    assert active["telegram"]["poll_enabled"] is False
    # Standalone org (empty role, no topology file) is the single-brain tree.
    assert active["org"]["node_count"] >= 1


def test_collect_plugins_section(tmp_path):
    (tmp_path / "plugins").mkdir()
    data = cap.collect_capabilities(_tmp_config(tmp_path))
    plug = data["plugins"]
    assert plug["plugins_dir_exists"] is True
    assert isinstance(plug["loaded"], list)


def test_collect_with_no_config_uses_default():
    # Must not raise and must still produce the sections from the real Config.
    data = cap.collect_capabilities()
    assert "registries" in data and "active_config" in data


# ── defensive: a degraded subsystem is reported, not fatal ───────────────────────

def test_degraded_registry_is_reported_not_fatal(tmp_path):
    cfg = _tmp_config(tmp_path)
    real_import = __import__

    def _boom(name, *args, **kwargs):
        if name == "lhassa.harness.adapter":
            raise RuntimeError("simulated harness import failure")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_boom):
        data = cap.collect_capabilities(cfg)

    # Never raised; the failure is recorded and a safe placeholder remains.
    assert "harnesses" in data["errors"]
    assert data["registries"]["harnesses"]["registered"] == []
    # Other registries still collected fine.
    assert "default" in data["registries"]["providers"]["registered"]


# ── render ───────────────────────────────────────────────────────────────────────

def test_render_is_a_string_with_sections(tmp_path):
    data = cap.collect_capabilities(_tmp_config(tmp_path))
    text = cap.render(data)
    assert isinstance(text, str)
    for marker in ("Registries", "Org", "Plugins", "Active config"):
        assert marker in text


def test_render_handles_errors_section(tmp_path):
    data = cap.collect_capabilities(_tmp_config(tmp_path))
    data["errors"] = {"providers": "RuntimeError: boom"}
    text = cap.render(data)
    assert "Degraded subsystems" in text
    assert "providers" in text


# ── CLI wiring ───────────────────────────────────────────────────────────────────

def test_command_registered_in_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "capabilities" in result.output
    assert "doctor" in result.output


def test_capabilities_renders_offline():
    result = runner.invoke(app, ["capabilities"])
    assert result.exit_code == 0
    assert "Registries" in result.output


def test_doctor_alias_works():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Registries" in result.output


def test_capabilities_json_is_valid_and_complete():
    result = runner.invoke(app, ["capabilities", "--json"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip())
    assert set(obj) >= {"registries", "active_config", "plugins", "errors"}
    assert "providers" in obj["registries"]
    assert "routine_provider" in obj["active_config"]
    assert "loaded" in obj["plugins"]


# ── the default chat path is preserved ───────────────────────────────────────────

def test_default_chat_still_routes(tmp_path):
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE):
        result = runner.invoke(app, ["hi"])
    assert result.exit_code == 0
    assert "Hello there." in result.output
