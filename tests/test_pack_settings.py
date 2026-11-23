# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capability packs declare their own Config knobs and settings-panel rows."""

from __future__ import annotations

import importlib

import pytest

from lhassa import config as config_mod
from lhassa import settings


@pytest.fixture
def scratch_knob(monkeypatch):
    attr = "test_pack_knob"
    yield attr
    config_mod._PACK_KNOBS.pop(attr, None)
    if hasattr(config_mod.Config, attr):
        delattr(config_mod.Config, attr)


def test_knob_reads_env_parses_and_falls_back(monkeypatch, scratch_knob):
    monkeypatch.setenv("LHASSA_TEST_PACK_KNOB", "2.5")
    assert config_mod.register_config_knob(scratch_knob, "LHASSA_TEST_PACK_KNOB", 1.0, float) == 2.5
    assert config_mod.Config.test_pack_knob == 2.5
    # Second declaration is a no-op, not a re-read.
    monkeypatch.setenv("LHASSA_TEST_PACK_KNOB", "9")
    assert config_mod.register_config_knob(scratch_knob, "LHASSA_TEST_PACK_KNOB", 1.0, float) == 2.5


def test_unparseable_value_keeps_the_default(monkeypatch, scratch_knob):
    monkeypatch.setenv("LHASSA_TEST_PACK_KNOB", "not-a-number")
    assert config_mod.register_config_knob(scratch_knob, "LHASSA_TEST_PACK_KNOB", 1.0, float) == 1.0


def test_a_pack_cannot_redefine_a_core_knob():
    with pytest.raises(ValueError):
        config_mod.register_config_knob("llm_provider", "LHASSA_X", "evil")


def test_pack_knobs_survive_a_config_reload(monkeypatch, scratch_knob):
    monkeypatch.setenv("LHASSA_TEST_PACK_KNOB", "kept")
    config_mod.register_config_knob(scratch_knob, "LHASSA_TEST_PACK_KNOB", "")
    importlib.reload(config_mod)
    assert config_mod.Config.test_pack_knob == "kept"


def test_register_editable_adds_a_row_and_refuses_core_keys():
    entry = {
        "key": "test_pack_setting", "attr": "test_pack_setting", "env_var": "LHASSA_TEST_PACK_SETTING",
        "type": "str", "label": "x", "group": "Surfaces", "default": "", "restart": settings.RESTART_NONE,
        "pack": "test",
    }
    try:
        assert settings.register_editable(entry) is entry
        assert settings.register_editable(dict(entry)) is entry  # same pack again: no-op
        assert settings._BY_KEY["test_pack_setting"] is entry
        with pytest.raises(ValueError):
            settings.register_editable({**entry, "key": "llm_provider", "pack": "test"})
    finally:
        settings.EDITABLE[:] = [e for e in settings.EDITABLE if e["key"] != "test_pack_setting"]
        settings._BY_KEY.pop("test_pack_setting", None)
