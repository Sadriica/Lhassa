"""set_config; natural-language settings changes through the settings seam.

Offline, with the same isolation ``test_settings.py`` uses: HOME redirected so
``settings.env`` lands in a temp ``~/.lhassa``; every editable Config attr
snapshotted so live mutations restore at teardown.
"""

import pytest

import lhassa.config as config_mod
from lhassa import settings
from lhassa.actionpack._config import set_config


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    Config = config_mod.Config
    for entry in settings.EDITABLE:
        monkeypatch.setattr(Config, entry["attr"], getattr(Config, entry["attr"]))
    return tmp_path


def test_set_applies_live_and_persists_and_states_old_to_new(env):
    out = set_config({"key": "knowledge_roots", "value": f"{env}/docs"})
    assert "set_config: knowledge_roots:" in out
    assert "→" in out
    assert getattr(config_mod.Config, "knowledge_roots") == f"{env}/docs"
    assert "LHASSA_KNOWLEDGE_ROOTS" in (env / ".lhassa" / "settings.env").read_text()


def test_unknown_key_is_refused_honestly(env):
    out = set_config({"key": "deepseek_api_key", "value": "sk-evil"})
    assert "not an editable setting" in out
    # …and nothing was written.
    path = env / ".lhassa" / "settings.env"
    assert not path.exists() or "deepseek" not in path.read_text()


def test_guarded_value_reports_the_error(env):
    out = set_config({"key": "llm_provider", "value": "not_a_provider"})
    assert "couldn't set" in out


def test_missing_key_or_value_degrade(env):
    assert "no key" in set_config({})
    assert "no value" in set_config({"key": "knowledge_roots"})
    assert isinstance(set_config(None), str)
