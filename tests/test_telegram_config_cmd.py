"""The Telegram ``/config`` command; the CLI-parity door onto the settings service.

Offline: HOME is redirected so ``settings.env`` lands in a temp ``~/.lhassa``,
and every editable Config attr is snapshotted (the same isolation
``test_settings.py`` uses). LLM-free by construction; these tests never touch
a provider.
"""

import asyncio

import pytest

import lhassa.config as config_mod
from lhassa import settings
from lhassa.notify.commands import COMMANDS


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    Config = config_mod.Config
    for entry in settings.EDITABLE:
        monkeypatch.setattr(Config, entry["attr"], getattr(Config, entry["attr"]))
    return tmp_path


def _run(args=""):
    return asyncio.run(COMMANDS.get("config")(args))


def test_bare_config_lists_the_editable_settings(env):
    out = _run()
    assert "knowledge_roots" in out
    assert "llm_provider" in out
    assert "/config <key> <value>" in out


def test_show_one_setting_includes_default_and_help(env):
    out = _run("knowledge_roots")
    assert "knowledge_roots" in out
    assert "default:" in out
    assert "Comma-separated directories" in out  # the help text is included


def test_unknown_key_is_an_honest_line(env):
    out = _run("definitely_not_a_setting")
    assert "No editable setting" in out


def test_set_persists_to_settings_env_and_applies_live(env):
    out = _run(f"knowledge_roots {env}/docs")
    assert "✅" in out
    assert getattr(config_mod.Config, "knowledge_roots") == f"{env}/docs"
    written = (env / ".lhassa" / "settings.env").read_text()
    assert "LHASSA_KNOWLEDGE_ROOTS" in written
    assert f"{env}/docs" in written


def test_bad_value_reports_the_error_not_an_oops(env):
    # llm_provider is guarded: an unrunnable provider is refused with a reason.
    out = _run("llm_provider not_a_provider")
    assert "Couldn't set" in out


def test_secrets_are_masked_in_the_listing(env):
    rows = settings.effective(config_mod.Config)["settings"]
    secret_keys = [r["key"] for r in rows if r.get("secret")]
    if not secret_keys:
        pytest.skip("no secret-typed settings in EDITABLE")
    out = _run()
    for row in rows:
        if not row.get("secret"):
            continue
        # The listing shows the masked form, never raw key material.
        raw = getattr(config_mod.Config, row["key"], "")
        if raw:
            assert str(raw) not in out
        assert str(row["value"]).count("*") or row["value"] in ("", "unset")
