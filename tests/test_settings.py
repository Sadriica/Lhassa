"""Tests for lhassa.settings — the runtime settings service.

Offline: HOME is redirected to a tmp dir so ``settings.env`` is written under a temp
``~/.lhassa``, and every editable Config attribute is snapshotted via monkeypatch so a
``set`` that mutates the live Config class is restored at teardown (no cross-test
pollution).
"""

import pytest

import lhassa.config as config_mod
from lhassa import settings


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Redirect ~/.lhassa/settings.env under a temp HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    # Snapshot every editable Config attr so set()'s live mutation is auto-restored.
    # Reference the LIVE class via the module so a config reload in another test file
    # (test_config.py uses importlib.reload) does not leave us patching a stale class.
    Config = config_mod.Config
    for entry in settings.EDITABLE:
        monkeypatch.setattr(Config, entry["attr"], getattr(Config, entry["attr"]))
    return tmp_path


def _settings_env_text(home):
    path = home / ".lhassa" / "settings.env"
    return path.read_text() if path.exists() else ""


# ── effective() read model ──────────────────────────────────────────────────────


def test_effective_shape_and_available(env):
    model = settings.effective()
    assert {"settings", "groups", "available"} <= set(model)
    keys = {s["key"] for s in model["settings"]}
    assert {"heartbeat_provider", "llm_kill_switch", "llm_daily_token_budget",
            "org_departments"} <= keys
    # Read-only lists the panel needs.
    assert "default" in model["available"]["providers"]
    assert isinstance(model["available"]["harnesses"], list)
    assert model["available"]["opencode_models"]  # static hint, no network
    # Groups are sensible and de-duped.
    assert "Heartbeat" in model["groups"] and "Budget & Safety" in model["groups"]


# ── set() persists + applies live + restart hint ────────────────────────────────


def test_set_bool_writes_settings_env_and_applies_live(env):
    res = settings.set("llm_kill_switch", "true")
    assert res["applied"] is True
    assert res["value"] is True
    # Persisted to settings.env (canonical 1/0), never to .env.
    assert "LHASSA_KILL_SWITCH=1" in _settings_env_text(env)
    # Applied live on the Config class so the running API reflects it immediately.
    assert config_mod.Config.llm_kill_switch is True
    # effective() now reports it overridden.
    row = settings.get("llm_kill_switch")
    assert row["overridden"] is True and row["value"] is True


def test_set_int_coerces(env):
    res = settings.set("llm_daily_token_budget", "500")
    assert res["applied"] is True and res["value"] == 500
    assert config_mod.Config.llm_daily_token_budget == 500
    assert "LHASSA_LLM_DAILY_TOKEN_BUDGET=500" in _settings_env_text(env)


def test_set_enum_validates(env):
    bad = settings.set("heartbeat_provider", "bogus")
    assert bad["applied"] is False and "one of" in bad["error"]
    ok = settings.set("heartbeat_provider", "opencode")
    assert ok["applied"] is True and config_mod.Config.heartbeat_provider == "opencode"


def test_set_unknown_key_is_error_not_raise(env):
    res = settings.set("does_not_exist", "x")
    assert res["applied"] is False and "unknown setting" in res["error"]


def test_restart_hint_for_service_keys(env):
    res = settings.set("voice_enabled", "1")
    assert res["applied"] is True and res["restart_needed"] is True
    assert "voice" in res["restart_hint"]
    # A heartbeat-only key needs no restart.
    res2 = settings.set("router_mode", "off")
    assert res2["restart_needed"] is False


def test_settings_env_preserves_other_lines(env):
    settings.set("router_mode", "off")
    settings.set("notify_channels", "desktop")
    text = _settings_env_text(env)
    assert "LHASSA_ROUTER=off" in text and "LHASSA_NOTIFY_CHANNELS=desktop" in text


# ── secret masking ──────────────────────────────────────────────────────────────


def test_set_env_masks_secret_and_persists(env):
    res = settings.set_env("LHASSA_TELEGRAM_BOT_TOKEN", "super-secret")
    assert res["applied"] is True and res["secret"] is True
    assert res["value"] == "set"  # never echoes the value
    # Written to settings.env (the raw value lives only there, not in the response).
    assert "LHASSA_TELEGRAM_BOT_TOKEN=super-secret" in _settings_env_text(env)


def test_set_env_refuses_non_allowed_name(env):
    res = settings.set_env("EVILVAR", "x")
    assert res["applied"] is False and "not an allowed" in res["error"]


def test_set_env_allows_lhassa_prefix(env):
    res = settings.set_env("LHASSA_CUSTOM_FLAG", "1")
    assert res["applied"] is True and res["secret"] is False
    assert "LHASSA_CUSTOM_FLAG=1" in _settings_env_text(env)


def test_mask_helper():
    assert settings._mask("x") == "set"
    assert settings._mask("") == "unset"
    assert settings._mask(None) == "unset"
